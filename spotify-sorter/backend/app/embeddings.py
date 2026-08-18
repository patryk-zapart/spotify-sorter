"""Continuous semantic similarity for genre/vibe matching.

Replaces the old discrete, position-weighted substring matching in
fit_score.py, which produced clusters of identical scores (many different
songs landing on the exact same 80-point tie) and couldn't recognize that
"anthemic" and "epic" are closely related concepts unless one was literally
a substring of the other. Cosine similarity between OpenAI embeddings gives
a smooth 0-100 gradient and captures relatedness organically - no
hand-curated cluster list needed.

Embeddings are cached per unique tag string in the TagEmbedding table
(models.py), since the same genre/vibe tag strings recur constantly across
songs. Without this cache, every scoring operation would need a fresh API
call per tag - both slow and wasteful. Callers are expected to collect
every tag they'll need for a scoring pass and fetch them all in one batched
call via get_embeddings_for_tags, rather than calling this per tag.

This module deliberately does NOT import anything from fit_score.py (and
vice versa) - fit_score.py stays a pure, synchronous computation module
that's handed a ready-made {tag: vector} dict, while all async/DB/API work
for producing that dict lives here. This also avoids a circular import,
since models.py (which defines TagEmbedding) is imported here, not from
fit_score.py.
"""
import math

from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TagEmbedding

settings = get_settings()

EMBEDDING_MODEL = "text-embedding-3-small"

_client = AsyncOpenAI(api_key=settings.openai_api_key)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _normalize_tag(tag: str) -> str:
    return tag.strip().lower()


async def get_embeddings_for_tags(tags: list[str], db: Session) -> dict[str, list[float]]:
    """Returns {original_tag_string: embedding_vector} for every tag given,
    reusing cached vectors where available and fetching the rest in a
    single batched API call. Callers should call db.commit() afterward (or
    let their own endpoint's later commit cover it) so newly-cached vectors
    actually persist - this function only adds/flushes, it doesn't commit,
    since it may be one step within a larger request.
    """
    unique_tags = list(dict.fromkeys(t for t in tags if t and t.strip()))
    if not unique_tags:
        return {}

    # Cache key is case/whitespace-normalized so "Epic" and "epic" share one
    # cached vector, but the returned dict uses each tag's ORIGINAL casing
    # as its key, matching what callers passed in.
    normalized_to_original: dict[str, str] = {}
    for t in unique_tags:
        normalized_to_original.setdefault(_normalize_tag(t), t)
    normalized_keys = list(normalized_to_original.keys())

    cached_rows = db.query(TagEmbedding).filter(TagEmbedding.tag.in_(normalized_keys)).all()
    result: dict[str, list[float]] = {}
    cached_normalized = set()
    for row in cached_rows:
        original = normalized_to_original.get(row.tag)
        if original:
            result[original] = row.embedding
            cached_normalized.add(row.tag)

    missing_normalized = [k for k in normalized_keys if k not in cached_normalized]
    if missing_normalized:
        resp = await _client.embeddings.create(model=EMBEDDING_MODEL, input=missing_normalized)
        for normalized_key, item in zip(missing_normalized, resp.data):
            vector = list(item.embedding)
            result[normalized_to_original[normalized_key]] = vector
            db.add(TagEmbedding(tag=normalized_key, embedding=vector))
        db.flush()

    return result


def _collect_tags(songs: list, profiles: list[dict]) -> list[str]:
    tags: list[str] = []
    for s in songs:
        tags.extend(getattr(s, "genres", None) or [])
        tags.extend(getattr(s, "vibe_tags", None) or [])
    for p in profiles:
        if not p:
            continue
        tags.extend((p.get("genre_distribution") or {}).keys())
        tags.extend((p.get("vibe_distribution") or {}).keys())
        title_signal = p.get("title_signal")
        if title_signal:
            tags.extend(title_signal.get("genres") or [])
            tags.extend(title_signal.get("vibe_tags") or [])
    return tags


async def fetch_embeddings_for(songs: list, profiles: list[dict], db: Session) -> dict[str, list[float]]:
    """Convenience wrapper for routers: collects every genre/vibe tag
    referenced by a set of songs and playlist profiles (including each
    profile's title_signal, if any), and fetches embeddings for the whole
    batch in one call rather than the caller hand-rolling tag collection
    every time. Callers still need to db.commit() afterward (this function
    only flushes) so any newly-cached vectors actually persist.
    """
    return await get_embeddings_for_tags(_collect_tags(songs, profiles), db)
