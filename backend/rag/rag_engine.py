"""
RAG Engine for Expert Institute Voice Agent
============================================
Retrieval Augmented Generation over the Supabase ``knowledge_base`` table using:
  - OpenAI text-embedding-3-small  (embeddings)
  - FAISS (in-memory vector search)
  - Redis (cross-process embedding cache, guarded by a distributed lock)

The engine is immutable once built. ``retrieve()`` reads a single immutable
``KnowledgeIndex`` and never mutates engine state, so a rebuild is invisible to
calls already in flight, and a failed rebuild leaves the previous index serving
traffic untouched.

Builds are deduplicated cheapest-check-first:
  1. in-memory snapshot fingerprint match -- the steady state, no I/O at all
  2. Redis cache hit on the same content fingerprint
  3. Redis build lock, so only one process embeds a given fingerprint
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import struct
from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np
import redis

from core.config import settings
from core.observability import metrics
from utils.distributed_lock import (
    RAG_BUILD_LOCK,
    RAG_BUILD_LOCK_TTL_SECONDS,
    redis_client,
    release_lock,
    wait_for_lock,
)

logger = logging.getLogger("rag-engine")

#: How long to wait for another process to finish building our fingerprint
#: before giving up and building it ourselves. The lock is a cost optimisation,
#: not a correctness mechanism, so timing out must never fail the call.
RAG_BUILD_LOCK_WAIT_SECONDS = 10.0

RAG_INDEX_KEY = "rag:index:{hash}"
RAG_INDEX_KEY_SCAN_PATTERN = "rag:index:*"

# The shared lock client is created with decode_responses=True, which cannot
# carry a raw float32 blob without corrupting it. Index payloads get their own
# bytes-mode client; the lock itself keeps using the shared one.
_index_redis = redis.from_url(settings.redis_url, decode_responses=False)


@dataclass(frozen=True)
class KnowledgeDoc:
    """One source document, tagged with the language it is written in."""

    language: str
    content: str


@dataclass
class RetrievalResult:
    """Result returned by RAGEngine.retrieve()"""

    found: bool  # True if relevant chunks were found
    context: str = ""  # Formatted context to inject into prompt
    chunks: list[str] = field(default_factory=list)  # Raw matching chunks
    scores: list[float] = field(default_factory=list)  # Similarity scores


@dataclass(frozen=True)
class KnowledgeIndex:
    """An immutable, self-consistent catalog + vector index pair.

    ``chunks`` and ``faiss_index`` are always built together and swapped in as
    one unit, so a reader can never pair a vector from one build with text from
    another.
    """

    chunks: tuple[str, ...]
    faiss_index: object
    kb_hash: str
    chunk_digest: str


def _fingerprint(docs: Sequence[KnowledgeDoc]) -> str:
    """Stable fingerprint of a knowledge base, independent of row order.

    Sorting the payload means a Postgres row-order change cannot alter the hash
    and silently defeat every cache and skip check downstream.
    """
    payload = json.dumps(sorted((d.language, d.content) for d in docs))
    return hashlib.md5(payload.encode()).hexdigest()


def _chunk_digest(chunks: Sequence[str]) -> str:
    """Fingerprint of a chunk list, used to detect chunker drift across images."""
    return hashlib.md5(json.dumps(list(chunks)).encode()).hexdigest()


class KnowledgeChunker:
    """Splits a knowledge document into retrievable chunks.

    One algorithm for every document: keep splitting on a separator hierarchy
    until each piece lands in the size band, then merge short neighbours up. No
    format sniffing. The previous version had two paths -- a numbered-section
    path (regex for "1. ") with a "Q:" special case, and a fallback for anything
    else that just split on blank lines. Only the first path respected the size
    limit, so a plain bullet list fell through to the fallback and came back as
    332 fragments averaging 27 characters (median 18, 321 of them under 100, the
    shortest 2). Embedding those produced headers and single bullets as "context":
    a question about working hours retrieved the literal string "Working
    Professionals". Nothing raised, the chunk count was just wrong.

    The fix is to stop special-casing document shape. A separator hierarchy plus
    a min/max band and a merge step handles numbered sections, Q/A blocks, bullet
    lists and unbroken prose through the same code path, which is also the default
    approach in mainstream RAG frameworks.
    """

    # Tried in order, most structural first. "" is the last resort: it splits
    # between characters and is the only thing guaranteed to make progress.
    SEPARATORS: tuple[str, ...] = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", "")
    MIN_CHUNK_CHARS = 100
    MAX_CHUNK_CHARS = 500
    # 20% of MAX, matching the usual default. Overlap keeps a sentence that
    # straddles a boundary retrievable from at least one side of it.
    CHUNK_OVERLAP_CHARS = 100

    def chunk(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []

        pieces = self._split(text, 0)
        if not pieces:
            return []

        chunks = self._merge_short(pieces)
        chunks = self._apply_overlap(chunks)
        return [c for c in chunks if c.strip()]

    def _split(self, text: str, depth: int) -> list[str]:
        """Recursively split on the first separator that helps, deepest last."""
        if len(text) <= self.MAX_CHUNK_CHARS:
            return [text]

        if depth >= len(self.SEPARATORS):
            # No separator left. Splitting on characters is the only way to get
            # under the ceiling, so cut fixed-width windows rather than recursing
            # forever on a string that refuses to shrink.
            return [
                text[i : i + self.MAX_CHUNK_CHARS]
                for i in range(0, len(text), self.MAX_CHUNK_CHARS)
            ]

        sep = self.SEPARATORS[depth]
        # "" is the terminal separator. str.split("") raises, so split into
        # characters directly -- the same intent, expressed in valid Python.
        raw = list(text) if sep == "" else text.split(sep)
        parts = [p.strip() for p in raw if p.strip()]
        if len(parts) <= 1:
            # This separator does not occur here; try the next one down.
            return self._split(text, depth + 1)

        # Re-attach punctuation so a split point still reads as a sentence end
        # ("...address," / "location..."), and keep newlines so paragraph
        # structure survives. A plain " " separator is NOT re-attached:
        # _merge_short inserts its own join, and re-adding a space on top of it
        # produced "word  word" with a doubled space.
        if sep and (not sep.isspace() or "\n" in sep):
            parts = [p if p.endswith(sep) else p + sep for p in parts]
        return [s for p in parts for s in self._split(p, depth + 1)]

    @staticmethod
    def _join(left: str, right: str) -> str:
        """Join two pieces, matching the whitespace the source used."""
        if "\n" in left[-2:] or "\n" in right[:2]:
            return f"{left}\n{right}"
        return f"{left} {right}"

    def _merge_short(self, pieces: list[str]) -> list[str]:
        """Fold undersized pieces into their neighbour instead of dropping them.

        Dropping is the wrong move: a short fragment carries real content, and
        the old `len(c) > 20` filter silently deleted it from the index.
        """
        merged: list[str] = []
        for piece in pieces:
            if merged and len(merged[-1]) < self.MIN_CHUNK_CHARS:
                candidate = self._join(merged[-1], piece)
                # Only absorb if the result still fits; otherwise close the
                # previous chunk and start a new one.
                if len(candidate) <= self.MAX_CHUNK_CHARS:
                    merged[-1] = candidate
                    continue
            merged.append(piece)

        # A short final chunk has no right-hand neighbour, so fold it backwards.
        if len(merged) > 1 and len(merged[-1]) < self.MIN_CHUNK_CHARS:
            tail = merged.pop()
            if len(merged[-1]) + 1 + len(tail) <= self.MAX_CHUNK_CHARS:
                merged[-1] = self._join(merged[-1], tail)
            else:
                merged.append(tail)
        return [m.strip() for m in merged if m.strip()]

    def _apply_overlap(self, chunks: list[str]) -> list[str]:
        if len(chunks) < 2 or self.CHUNK_OVERLAP_CHARS <= 0:
            return chunks
        overlapped = [chunks[0]]
        for prev, current in zip(chunks, chunks[1:]):
            tail = prev[-self.CHUNK_OVERLAP_CHARS :]
            # Snap the overlap to a real boundary. A raw [-N:] slice starts
            # mid-word, so the next chunk opens with a fragment like "fice0,"
            # which is a poor thing to hand an embedding model. Prefer a line
            # start when the window holds one -- that leads with a whole line
            # ("- For any query about ...") rather than a bullet's tail
            # ("0, always return this."). Otherwise fall back to a word start.
            nl = tail.rfind("\n")
            if 0 <= nl < len(tail) - 1:
                tail = tail[nl + 1 :]
            else:
                space = tail.find(" ")
                if 0 <= space < len(tail) - 1:
                    tail = tail[space + 1 :]
            tail = tail.strip()
            # Never let overlap push a chunk past the ceiling.
            if tail and len(tail) + 1 + len(current) <= self.MAX_CHUNK_CHARS:
                overlapped.append(self._join(tail, current))
            else:
                overlapped.append(current)
        return overlapped


class RAGEngine:
    """In-memory RAG engine backed by FAISS and OpenAI embeddings."""

    SIMILARITY_THRESHOLD = 0.30  # Cosine similarity; below this = "not found"
    TOP_K = 3  # Number of chunks to retrieve per query
    EMBED_MODEL = "text-embedding-3-small"
    EMBED_DIM = 1536
    EMBED_BATCH = 512  # OpenAI caps a single embeddings request at 2048 inputs

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
        # The single mutable attribute on this engine. Everything else is either
        # immutable snapshot content or read-only configuration.
        self._snapshot: Optional[KnowledgeIndex] = None
        self._build_lock = asyncio.Lock()
        self._redis_warned = False

    # ------------------------------------------------------------------
    # Index lifecycle
    # ------------------------------------------------------------------

    async def ensure_index(self, docs: Sequence[KnowledgeDoc]) -> None:
        """Make the in-memory index match `docs`, rebuilding only if needed.

        This is the only supported entry point for loading knowledge. Call it on
        every call: in steady state it costs one hash and one string comparison.
        """
        metrics.increment("rag_ensure_calls_total")

        if not docs:
            # A transient Supabase failure must never blind an agent mid-call.
            logger.error("RAG: no knowledge documents supplied — keeping last good index")
            return

        new_hash = _fingerprint(docs)
        if self._is_current(new_hash):
            metrics.increment("rag_cache_hits_total")
            return

        async with self._build_lock:
            # Re-read after acquiring: prewarm or another call may have just
            # finished building this exact fingerprint.
            if self._is_current(new_hash):
                metrics.increment("rag_cache_hits_total")
                return

            chunks = self._chunk_docs(docs)
            if not chunks:
                logger.error("RAG: no knowledge chunks found to index")
                return

            digest = _chunk_digest(chunks)

            cached = await asyncio.to_thread(self._load_cached, new_hash, digest)
            if cached is not None:
                metrics.increment("rag_redis_cache_hits_total")
                logger.info("RAG: reused cached embeddings for %s", new_hash[:8])
                await asyncio.to_thread(self._adopt, *cached, new_hash, digest)
                return

            vectors = await self._build_guarded(docs, chunks, new_hash, digest)
            if vectors is None:
                return
            await asyncio.to_thread(self._adopt, chunks, vectors, new_hash, digest)

    def _is_current(self, kb_hash: str) -> bool:
        snap = self._snapshot
        return snap is not None and snap.kb_hash == kb_hash

    def _chunk_docs(self, docs: Sequence[KnowledgeDoc]) -> list[str]:
        """Chunk each document independently so chunks never span languages."""
        chunks: list[str] = []
        for doc in docs:
            if doc.content.strip():
                chunks.extend(self._chunker.chunk(doc.content))
        return chunks

    async def _build_guarded(
        self,
        docs: Sequence[KnowledgeDoc],
        chunks: list[str],
        new_hash: str,
        digest: str,
    ) -> Optional[np.ndarray]:
        """Embed `chunks`, coordinating with other processes via the Redis lock.

        Returns the embedding matrix, or None if another process was mid-build
        and its result was adopted instead.
        """
        lock_name = RAG_BUILD_LOCK.format(hash=new_hash)
        token = None
        try:
            token = await asyncio.to_thread(
                wait_for_lock,
                redis_client,
                lock_name,
                RAG_BUILD_LOCK_TTL_SECONDS,
                RAG_BUILD_LOCK_WAIT_SECONDS,
            )
        except Exception as e:
            self._warn_redis_once(f"build lock unavailable ({type(e).__name__}: {e})")
            token = None

        if token is None:
            metrics.increment("rag_lock_degraded_total")
            logger.warning("RAG: proceeding without the build lock for %s", new_hash[:8])
        else:
            # Another process may have finished this fingerprint while we waited.
            cached = await asyncio.to_thread(self._load_cached, new_hash, digest)
            if cached is not None:
                metrics.increment("rag_redis_cache_hits_total")
                logger.info("RAG: another process built %s while we waited", new_hash[:8])
                await asyncio.to_thread(self._adopt, *cached, new_hash, digest)
                return None

        try:
            logger.info("RAG: embedding %d chunk(s) via OpenAI...", len(chunks))
            embeddings = await self._embed_texts(chunks)
        except Exception as e:
            metrics.increment("rag_build_failures_total")
            logger.exception("RAG: embedding failed for %s — keeping previous index", new_hash[:8])
            return None
        finally:
            # Release as soon as the build settles, in every path. Holding the
            # lock for its full TTL would serialise every other process behind
            # a build that already finished.
            if token is not None:
                try:
                    await asyncio.to_thread(release_lock, redis_client, lock_name, token)
                except Exception as e:
                    logger.debug("RAG: build lock release failed: %s", e)

        vectors = await asyncio.to_thread(self._normalize, embeddings)
        await asyncio.to_thread(self._store_cached, new_hash, chunks, vectors, digest)
        metrics.increment("rag_builds_total")
        logger.info("RAG: built index %s (%d chunks)", new_hash[:8], len(chunks))
        return vectors

    def _adopt(
        self,
        chunks: list[str],
        vectors: np.ndarray,
        kb_hash: str,
        digest: str,
    ) -> None:
        """Build a fresh FAISS index and swap it in as one unit.

        Assigning the snapshot only after the index is fully built is what makes
        a concurrent rebuild invisible to readers.
        """
        import faiss

        if vectors.shape[0] != len(chunks):
            raise ValueError(
                f"vector/chunk mismatch: {vectors.shape[0]} vectors for {len(chunks)} chunks"
            )

        index = faiss.IndexFlatIP(self.EMBED_DIM)
        index.add(vectors)
        self._snapshot = KnowledgeIndex(
            chunks=tuple(chunks),
            faiss_index=index,
            kb_hash=kb_hash,
            chunk_digest=digest,
        )
        logger.info("RAG: snapshot %s live with %d vectors", kb_hash[:8], index.ntotal)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def retrieve(self, query: str, top_k: int | None = None) -> RetrievalResult:
        import faiss

        # One read, no await: a single attribute load cannot interleave with a
        # concurrent snapshot swap, so this call holds a consistent index for
        # its whole duration even while other calls rebuild.
        snap = self._snapshot
        if snap is None:
            logger.warning("RAG: no index built yet, skipping retrieval")
            return RetrievalResult(found=False)

        k = top_k or self.TOP_K

        query_vec = await self._embed_texts([query])
        query_vec = await asyncio.to_thread(self._normalize, query_vec)

        n_results = min(k, snap.faiss_index.ntotal)
        scores, indices = snap.faiss_index.search(query_vec, n_results)
        scores = scores[0].tolist()
        indices = indices[0].tolist()

        logger.info(
            "RAG: Query='%s...' | Top scores: %s",
            query[:60],
            [round(s, 3) for s in scores],
        )

        matched_chunks = []
        matched_scores = []
        for score, idx in zip(scores, indices):
            if score >= self.SIMILARITY_THRESHOLD and idx >= 0:
                matched_chunks.append(snap.chunks[idx])
                matched_scores.append(score)

        if not matched_chunks:
            logger.info("RAG: no chunks above threshold — fallback triggered.")
            return RetrievalResult(found=False, scores=scores)

        context = self._format_context(matched_chunks)
        logger.info("RAG: %d relevant chunk(s) found and injected.", len(matched_chunks))
        return RetrievalResult(
            found=True,
            context=context,
            chunks=matched_chunks,
            scores=matched_scores,
        )

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def _embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        batches = [
            texts[i : i + self.EMBED_BATCH]
            for i in range(0, len(texts), self.EMBED_BATCH)
        ]
        results = await asyncio.gather(*(self._embed_batch(b) for b in batches))
        return np.concatenate(results, axis=0)

    async def _embed_batch(self, batch: Sequence[str]) -> np.ndarray:
        response = await self._client.embeddings.create(
            model=self.EMBED_MODEL,
            input=list(batch),
        )
        # The API documents `index` as the request position. Do not trust arrival
        # order: an out-of-order batch would pair every chunk with the wrong
        # vector and degrade retrieval to noise with no error raised.
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(batch):
            raise RuntimeError(
                f"embedding response size mismatch: got {len(ordered)}, sent {len(batch)}"
            )
        return np.array([item.embedding for item in ordered], dtype=np.float32)

    def _normalize(self, embeddings: np.ndarray) -> np.ndarray:
        """L2-normalise in place-friendly fashion and return a writable copy."""
        import faiss

        out = np.ascontiguousarray(embeddings, dtype=np.float32).copy()
        faiss.normalize_L2(out)
        return out

    # ------------------------------------------------------------------
    # Cross-process cache
    # ------------------------------------------------------------------

    def _encode_index(
        self, chunks: Sequence[str], vectors: np.ndarray, kb_hash: str, digest: str
    ) -> bytes:
        header = json.dumps(
            {
                "model": self.EMBED_MODEL,
                "dim": self.EMBED_DIM,
                "rows": int(vectors.shape[0]),
                "chunks": list(chunks),
                "digest": digest,
                "kb_hash": kb_hash,
            }
        ).encode()
        return struct.pack("<I", len(header)) + header + vectors.tobytes()

    def _decode_index(
        self, blob: bytes, expected_digest: str
    ) -> Optional[tuple[tuple[str, ...], np.ndarray]]:
        if len(blob) < 4:
            return None
        (header_len,) = struct.unpack_from("<I", blob, 0)
        if len(blob) < 4 + header_len:
            return None
        try:
            header = json.loads(blob[4 : 4 + header_len])
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

        rows = int(header.get("rows", 0))
        dim = int(header.get("dim", 0))
        if header.get("model") != self.EMBED_MODEL or dim != self.EMBED_DIM:
            return None
        if header.get("digest") != expected_digest:
            # A different image chunked this content differently. Trusting the
            # stored vectors would pair them with the wrong text.
            return None

        body = blob[4 + header_len :]
        if len(body) != rows * dim * 4:
            return None

        chunks = tuple(header.get("chunks", ()))
        if len(chunks) != rows:
            return None

        return chunks, np.frombuffer(body, dtype=np.float32).reshape(rows, dim)

    def _load_cached(
        self, kb_hash: str, expected_digest: str
    ) -> Optional[tuple[tuple[str, ...], np.ndarray]]:
        try:
            blob = _index_redis.get(RAG_INDEX_KEY.format(hash=kb_hash))
        except Exception as e:
            self._warn_redis_once(f"index cache read failed ({type(e).__name__}: {e})")
            return None
        if not blob:
            return None
        return self._decode_index(blob, expected_digest)

    def _store_cached(
        self, kb_hash: str, chunks: Sequence[str], vectors: np.ndarray, digest: str
    ) -> None:
        try:
            # A single atomic SET: there is no partial-write state to reason
            # about, unlike a file write followed by a rename.
            _index_redis.set(
                RAG_INDEX_KEY.format(hash=kb_hash),
                self._encode_index(chunks, vectors, kb_hash, digest),
            )
            self._prune_cached(kb_hash)
        except Exception as e:
            self._warn_redis_once(f"index cache write failed ({type(e).__name__}: {e})")

    def _prune_cached(self, keep_hash: str) -> None:
        """Drop superseded fingerprints so the cache cannot grow without bound."""
        keep = RAG_INDEX_KEY.format(hash=keep_hash)
        stale = []
        for key in _index_redis.scan_iter(match=RAG_INDEX_KEY_SCAN_PATTERN, count=100):
            # The index client is created with decode_responses=False, so
            # scan_iter yields bytes while `keep` is str. Comparing those
            # directly is always unequal, which made every key look stale --
            # including the entry just written -- so pruning deleted the cache
            # it was meant to keep and no fingerprint ever survived.
            #
            # Compare on a decoded copy, but delete the original key. Decoding
            # is lossy for undecodable bytes, and re-encoding a replaced
            # character would not match the key Redis actually holds.
            name = key.decode("utf-8", "replace") if isinstance(key, bytes) else key
            if name != keep:
                stale.append(key)
        if stale:
            _index_redis.delete(*stale)
            logger.info("RAG: pruned %d superseded cache entrie(s)", len(stale))

    def _warn_redis_once(self, message: str) -> None:
        if not self._redis_warned:
            self._redis_warned = True
            logger.warning("RAG: Redis unavailable, running uncached — %s", message)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def kb_hash(self) -> Optional[str]:
        snap = self._snapshot
        return snap.kb_hash if snap else None

    @property
    def chunk_count(self) -> int:
        snap = self._snapshot
        return len(snap.chunks) if snap else 0

    def status(self) -> dict:
        """What this process is actually serving, for the admin status endpoint."""
        snap = self._snapshot
        if snap is None:
            return {"ready": False, "fingerprint": None, "chunks": 0}
        return {
            "ready": True,
            "fingerprint": snap.kb_hash,
            "chunks": len(snap.chunks),
            "chunk_digest": snap.chunk_digest,
        }

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
