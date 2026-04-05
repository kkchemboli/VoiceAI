"""
RAG Engine for Expert Institute Voice Agent
============================================
Provides Retrieval Augmented Generation over knowledge.txt using:
  - OpenAI text-embedding-3-small  (embeddings)
  - FAISS (in-memory vector search)

Usage:
    engine = RAGEngine(openai_api_key="sk-...")
    await engine.load_knowledge("knowledge.txt")

    result = await engine.retrieve("What courses do you offer?")
    if result.found:
        # inject result.context into LLM prompt
    else:
        # tell user we don't know, offer transfer
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger("rag-engine")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RetrievalResult:
    """Result returned by RAGEngine.retrieve()"""
    found: bool                        # True if relevant chunks were found
    context: str = ""                  # Formatted context to inject into prompt
    chunks: list[str] = field(default_factory=list)  # Raw matching chunks
    scores: list[float] = field(default_factory=list)  # Similarity scores


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------

class KnowledgeChunker:
    """
    Splits a plain-text knowledge base into semantic chunks.
    Strategy:
      1. Split on numbered section headers (e.g., "2. Courses Offered")
      2. Within each section, split on double newlines if section is too long
      3. For Q&A pairs, each Q+A pair is its own chunk
    """

    # Matches lines like "2. Courses Offered" or "10. Common Questions"
    SECTION_RE = re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE)
    MAX_CHUNK_CHARS = 500

    def chunk(self, text: str) -> list[str]:
        chunks: list[str] = []

        # Find all section header positions
        matches = list(self.SECTION_RE.finditer(text))
        if not matches:
            # Fallback: split on double newlines
            return [c.strip() for c in text.split("\n\n") if c.strip()]

        # Split text into sections
        boundaries = [m.start() for m in matches] + [len(text)]
        for i in range(len(boundaries) - 1):
            section_text = text[boundaries[i]: boundaries[i + 1]].strip()
            if not section_text:
                continue

            # If Q&A section, split into individual Q+A pairs
            if re.search(r"Q:", section_text):
                qa_chunks = self._split_qa(section_text)
                chunks.extend(qa_chunks)
            elif len(section_text) <= self.MAX_CHUNK_CHARS:
                chunks.append(section_text)
            else:
                # Split large sections on double newlines
                sub_chunks = [c.strip() for c in section_text.split("\n\n") if c.strip()]
                chunks.extend(sub_chunks)

        # Also grab any leading text before first section
        if matches[0].start() > 0:
            preamble = text[: matches[0].start()].strip()
            if preamble:
                chunks.insert(0, preamble)

        return [c for c in chunks if len(c) > 20]  # Drop tiny noise chunks

    def _split_qa(self, text: str) -> list[str]:
        """Split a Q&A block into individual Q+A pairs."""
        pairs: list[str] = []
        # Split on lines starting with "Q:"
        parts = re.split(r"(?=Q:)", text)
        for part in parts:
            part = part.strip()
            if part:
                pairs.append(part)
        return pairs


# ---------------------------------------------------------------------------
# RAG Engine
# ---------------------------------------------------------------------------

class RAGEngine:
    """
    In-memory RAG engine backed by FAISS and OpenAI embeddings.

    The FAISS index and chunk texts are built once at startup (in prewarm).
    Each user query is embedded on-the-fly and matched against the index.
    """

    SIMILARITY_THRESHOLD = 0.30   # Cosine similarity; below this = "not found"
    TOP_K = 3                     # Number of chunks to retrieve per query
    EMBED_MODEL = "text-embedding-3-small"
    EMBED_DIM = 1536

    def __init__(self, openai_api_key: str):
        try:
            from openai import AsyncOpenAI
            import faiss  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "RAGEngine requires 'openai' and 'faiss-cpu'. "
                "Install with: pip install openai faiss-cpu numpy"
            ) from e

        self._client = AsyncOpenAI(api_key=openai_api_key)
        self._chunker = KnowledgeChunker()
        self._chunks: list[str] = []
        self._index = None          # faiss.IndexFlatIP (inner product = cosine on L2-normed vecs)
        self._kb_hash: Optional[str] = None
        self._ready = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load_knowledge(self, file_paths: str | list[str]) -> None:
        """
        Load, chunk, embed, and index one or more knowledge base files.
        If multiple files are provided, their chunks are merged into a single index.
        Caches embeddings to a .npy file to avoid re-embedding on every restart.
        """
        import faiss

        if isinstance(file_paths, str):
            file_paths = [file_paths]

        all_text = ""
        all_chunks = []
        
        # Collect and chunk all files
        for path in file_paths:
            if not os.path.exists(path):
                logger.warning(f"RAG: Knowledge file not found: {path}")
                continue
                
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                all_text += content + "\n\n"
                chunks = self._chunker.chunk(content)
                all_chunks.extend(chunks)

        if not all_chunks:
            logger.error("RAG: No knowledge chunks found to index.")
            return

        self._chunks = all_chunks
        
        # Generate hash based on all file contents to handle updates in any file
        kb_hash = hashlib.md5(all_text.encode()).hexdigest()
        
        # Use first file path as base for cache naming
        cache_path = file_paths[0] + f".merged.{kb_hash}.embeddings.npy"

        logger.info(f"RAG: Total knowledge base split into {len(self._chunks)} chunks")

        # Try loading cached embeddings
        if os.path.exists(cache_path):
            logger.info(f"RAG: Loading cached merged embeddings from {cache_path}")
            embeddings = np.load(cache_path)
            
            # Safety check for cache validity
            if len(embeddings) != len(self._chunks):
                logger.warning("RAG: Cached embeddings size mismatch, re-embedding...")
                embeddings = await self._embed_texts(self._chunks)
                np.save(cache_path, embeddings)
        else:
            logger.info(f"RAG: Embedding {len(self._chunks)} chunks via OpenAI...")
            embeddings = await self._embed_texts(self._chunks)
            np.save(cache_path, embeddings)
            logger.info(f"RAG: Embeddings cached to {cache_path}")

        # Build FAISS index (Inner Product on L2-normalised vectors = cosine similarity)
        embeddings = embeddings.astype(np.float32)
        faiss.normalize_L2(embeddings)

        index = faiss.IndexFlatIP(self.EMBED_DIM)
        index.add(embeddings)

        self._index = index
        self._kb_hash = kb_hash
        self._ready = True
        logger.info(f"RAG: Combined FAISS index built with {index.ntotal} vectors. Engine ready.")

    async def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        """
        Embed query and retrieve the most relevant chunks.

        Returns RetrievalResult:
          - found=True  → context string with relevant chunks
          - found=False → caller should trigger fallback (agent offers transfer)
        """
        import faiss

        if not self._ready or self._index is None:
            logger.warning("RAG: Engine not ready, skipping retrieval.")
            return RetrievalResult(found=False)

        k = top_k or self.TOP_K

        # Embed the query
        query_vec = await self._embed_texts([query])
        query_vec = query_vec.astype(np.float32)
        faiss.normalize_L2(query_vec)

        # Search
        n_results = min(k, self._index.ntotal)
        scores, indices = self._index.search(query_vec, n_results)
        scores = scores[0].tolist()
        indices = indices[0].tolist()

        logger.info(f"RAG: Query='{query[:60]}...' | Top scores: {[round(s,3) for s in scores]}")

        # Filter by threshold
        matched_chunks = []
        matched_scores = []
        for score, idx in zip(scores, indices):
            if score >= self.SIMILARITY_THRESHOLD and idx >= 0:
                matched_chunks.append(self._chunks[idx])
                matched_scores.append(score)

        if not matched_chunks:
            logger.info("RAG: No chunks above threshold — fallback triggered.")
            return RetrievalResult(found=False, scores=scores)

        context = self._format_context(matched_chunks)
        logger.info(f"RAG: {len(matched_chunks)} relevant chunk(s) found and injected.")
        return RetrievalResult(
            found=True,
            context=context,
            chunks=matched_chunks,
            scores=matched_scores,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _embed_texts(self, texts: list[str]) -> np.ndarray:
        """Batch-embed a list of texts. Returns float32 numpy array (N, EMBED_DIM)."""
        response = await self._client.embeddings.create(
            model=self.EMBED_MODEL,
            input=texts,
        )
        vectors = [item.embedding for item in response.data]
        return np.array(vectors, dtype=np.float32)

    @staticmethod
    def _format_context(chunks: list[str]) -> str:
        """Format retrieved chunks into a context block for the LLM."""
        divider = "\n---\n"
        body = divider.join(chunks)
        return (
            "[KNOWLEDGE CONTEXT — Use ONLY this information to answer the user's question. "
            "Do NOT invent facts beyond what is written below.]\n"
            + divider
            + body
            + divider
        )
