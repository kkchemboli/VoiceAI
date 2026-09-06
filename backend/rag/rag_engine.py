"""
RAG Engine for Expert Institute Voice Agent
============================================
Provides Retrieval Augmented Generation over knowledge files using:
  - OpenAI text-embedding-3-small  (embeddings)
  - FAISS (in-memory vector search)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional, List
import aiohttp

import numpy as np

logger = logging.getLogger("rag-engine")


@dataclass
class RetrievalResult:
    """Result returned by RAGEngine.retrieve()"""

    found: bool  # True if relevant chunks were found
    context: str = ""  # Formatted context to inject into prompt
    chunks: list[str] = field(default_factory=list)  # Raw matching chunks
    scores: list[float] = field(default_factory=list)  # Similarity scores


class KnowledgeChunker:
    """Splits plain-text or PDF knowledge base documents into semantic chunks."""

    SECTION_RE = re.compile(r"^\s*\d+\.\s+\S", re.MULTILINE)
    MAX_CHUNK_CHARS = 500

    def chunk(self, text: str) -> list[str]:
        chunks: list[str] = []

        matches = list(self.SECTION_RE.finditer(text))
        if not matches:
            return [c.strip() for c in text.split("\n\n") if c.strip()]

        boundaries = [m.start() for m in matches] + [len(text)]
        for i in range(len(boundaries) - 1):
            section_text = text[boundaries[i] : boundaries[i + 1]].strip()
            if not section_text:
                continue

            if re.search(r"Q:", section_text):
                qa_chunks = self._split_qa(section_text)
                chunks.extend(qa_chunks)
            elif len(section_text) <= self.MAX_CHUNK_CHARS:
                chunks.append(section_text)
            else:
                sub_chunks = [
                    c.strip() for c in section_text.split("\n\n") if c.strip()
                ]
                chunks.extend(sub_chunks)

        if matches[0].start() > 0:
            preamble = text[: matches[0].start()].strip()
            if preamble:
                chunks.insert(0, preamble)

        return [c for c in chunks if len(c) > 20]

    def _split_qa(self, text: str) -> list[str]:
        pairs: list[str] = []
        parts = re.split(r"(?=Q:)", text)
        for part in parts:
            part = part.strip()
            if part:
                pairs.append(part)
        return pairs


class RAGEngine:
    """In-memory RAG engine backed by FAISS and OpenAI embeddings."""

    SIMILARITY_THRESHOLD = 0.30  # Cosine similarity; below this = "not found"
    TOP_K = 3  # Number of chunks to retrieve per query
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
        self._index = None
        self._kb_hash: Optional[str] = None
        self._ready = False

    async def load_knowledge(self, file_paths: str | list[str]) -> None:
        import faiss

        if isinstance(file_paths, str):
            file_paths = [file_paths]

        all_text = ""
        all_chunks = []

        for path in file_paths:
            if not os.path.exists(path):
                logger.warning(f"RAG: Knowledge file not found: {path}")
                continue

            content = ""
            if path.lower().endswith(".pdf"):
                logger.info(f"RAG: Extracting text from PDF: {path}")
                content = self._extract_text_from_pdf(path)
            else:
                logger.debug(f"RAG: Loading knowledge file: {path}")
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        content = f.read()
                    logger.debug(f"RAG: Loaded {len(content)} chars from {path}")
                except UnicodeDecodeError as e:
                    logger.error(f"RAG: Failed to decode {path} with utf-8-sig: {e}. Trying latin-1...")
                    with open(path, "r", encoding="latin-1") as f:
                        content = f.read()
                    logger.debug(f"RAG: Loaded {len(content)} chars from {path} using latin-1")
                except Exception as e:
                    logger.error(f"RAG: Unexpected error loading {path}: {e}")
                    continue

            if content.strip():
                all_text += content + "\n\n"
                chunks = self._chunker.chunk(content)
                all_chunks.extend(chunks)

        if not all_chunks:
            logger.error("RAG: No knowledge chunks found to index.")
            return

        self._chunks = all_chunks

        kb_hash = hashlib.md5(all_text.encode()).hexdigest()
        cache_path = file_paths[0] + f".merged.{kb_hash}.embeddings.npy"

        logger.info(f"RAG: Total knowledge base split into {len(self._chunks)} chunks")

        if os.path.exists(cache_path):
            logger.info(f"RAG: Loading cached merged embeddings from {cache_path}")
            embeddings = np.load(cache_path)

            if len(embeddings) != len(self._chunks):
                logger.warning("RAG: Cached embeddings size mismatch, re-embedding...")
                embeddings = await self._embed_texts(self._chunks)
                np.save(cache_path, embeddings)
        else:
            logger.info(f"RAG: Embedding {len(self._chunks)} chunks via OpenAI...")
            embeddings = await self._embed_texts(self._chunks)
            np.save(cache_path, embeddings)
            logger.info(f"RAG: Embeddings cached to {cache_path}")

        embeddings = embeddings.astype(np.float32)
        faiss.normalize_L2(embeddings)

        index = faiss.IndexFlatIP(self.EMBED_DIM)
        index.add(embeddings)

        self._index = index
        self._kb_hash = kb_hash
        self._ready = True
        logger.info(
            f"RAG: Combined FAISS index built with {index.ntotal} vectors. Engine ready."
        )

    async def load_knowledge_from_text(self, texts: list[str]) -> None:
        import faiss

        if not texts:
            logger.warning("RAG: No texts provided to load_knowledge_from_text")
            return

        all_text = "\n\n".join(texts)
        all_chunks = []

        for text in texts:
            if text.strip():
                chunks = self._chunker.chunk(text)
                all_chunks.extend(chunks)

        if not all_chunks:
            logger.error("RAG: No knowledge chunks found to index.")
            return

        self._chunks = all_chunks
        kb_hash = hashlib.md5(all_text.encode()).hexdigest()

        logger.info(f"RAG: Total knowledge base split into {len(self._chunks)} chunks")

        logger.info(f"RAG: Embedding {len(self._chunks)} chunks via OpenAI...")
        embeddings = await self._embed_texts(self._chunks)

        embeddings = embeddings.astype(np.float32)
        faiss.normalize_L2(embeddings)

        index = faiss.IndexFlatIP(self.EMBED_DIM)
        index.add(embeddings)

        self._index = index
        self._kb_hash = kb_hash
        self._ready = True
        logger.info(
            f"RAG: Text-based FAISS index built with {index.ntotal} vectors. Engine ready."
        )

    async def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        import faiss

        if not self._ready or self._index is None:
            logger.warning("RAG: Engine not ready, skipping retrieval.")
            return RetrievalResult(found=False)

        k = top_k or self.TOP_K

        query_vec = await self._embed_texts([query])
        query_vec = query_vec.astype(np.float32)
        faiss.normalize_L2(query_vec)

        n_results = min(k, self._index.ntotal)
        scores, indices = self._index.search(query_vec, n_results)
        scores = scores[0].tolist()
        indices = indices[0].tolist()

        logger.info(
            f"RAG: Query='{query[:60]}...' | Top scores: {[round(s, 3) for s in scores]}"
        )

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

    async def load_knowledge_from_sheet(self, csv_url: str) -> None:
        logger.info(f"RAG: Fetching live knowledge from Google Sheet: {csv_url}")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(csv_url) as resp:
                    if resp.status == 200:
                        csv_text = await resp.text()
                        await self.load_knowledge_from_text([csv_text])
                        logger.info("RAG: Google Sheet indexed successfully.")
                    else:
                        logger.error(f"RAG: Failed to fetch Google Sheet: Status {resp.status}")
        except Exception as e:
            logger.error(f"RAG: Error loading Google Sheet: {e}")

    def _extract_text_from_pdf(self, path: str) -> str:
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text
        except Exception as e:
            logger.error(f"RAG: Error reading PDF {path}: {e}")
            return ""

    async def _embed_texts(self, texts: list[str]) -> np.ndarray:
        response = await self._client.embeddings.create(
            model=self.EMBED_MODEL,
            input=texts,
        )
        vectors = [item.embedding for item in response.data]
        return np.array(vectors, dtype=np.float32)

    @staticmethod
    def _format_context(chunks: list[str]) -> str:
        divider = "\n---\n"
        body = divider.join(chunks)
        return (
            "[KNOWLEDGE CONTEXT — Use ONLY this information to answer the user's question. "
            "Do NOT invent facts beyond what is written below.]\n"
            + divider
            + body
            + divider
        )
