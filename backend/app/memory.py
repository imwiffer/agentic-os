"""Long-term memory: semantic search over embeddings with a keyword fallback (no native extensions needed)."""
from __future__ import annotations

import math
import re
from array import array

from . import config, db, llm

_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are", "my", "me", "i", "you",
         "what", "do", "does", "that", "this", "it", "with", "be", "was", "at", "by", "about"}


def _vec(blob: bytes | None) -> array | None:
    if not blob:
        return None
    v = array("f")
    v.frombytes(blob)
    return v


def _cosine(a, b) -> float:
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _keywords(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1}


def _keyword_score(query: set[str], text: str) -> float:
    if not query:
        return 0.0
    words = _keywords(text)
    return len(query & words) / len(query)


async def save(text: str, tags: str = "", source: str = "agent") -> dict:
    text = text.strip()[:2000]
    if not text:
        raise ValueError("Memory text is empty")
    embedding = await llm.embed(text)
    mid = db.add_memory(text, tags.strip()[:200], source, embedding)
    return {"id": mid, "text": text, "tags": tags, "semantic": embedding is not None}


async def search(query: str, k: int | None = None, min_score: float = 0.35) -> list[dict]:
    k = k or config.MEMORY_TOP_K
    rows = db.all_memories()
    if not rows:
        return []
    qvec = _vec(await llm.embed(query)) if any(r["embedding"] for r in rows) else None
    qwords = _keywords(query)
    scored = []
    for r in rows:
        mvec = _vec(r["embedding"])
        if qvec is not None and mvec is not None:
            score = _cosine(qvec, mvec)
            threshold = min_score + 0.15  # embeddings are generally > 0.3 for unrelated text
        else:
            score = _keyword_score(qwords, f"{r['text']} {r['tags']}")
            threshold = min_score
        if score >= threshold:
            scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    return [{"id": r["id"], "text": r["text"], "tags": r["tags"], "created_at": r["created_at"],
             "score": round(s, 3)} for s, r in scored[:k]]


def listing() -> list[dict]:
    return [{"id": r["id"], "text": r["text"], "tags": r["tags"], "source": r["source"],
             "created_at": r["created_at"], "semantic": r["embedding"] is not None} for r in db.all_memories()]


def forget(memory_id: int) -> bool:
    return db.delete_memory(int(memory_id))
