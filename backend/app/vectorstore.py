"""Local embeddings + embedded Qdrant.

Both run on this machine: no API key, no cost, no rate limit in the hottest
loop of the system (design doc S13). Swapping to a hosted embedding provider
later is a change to embed() alone.
"""

from __future__ import annotations

import atexit
import uuid
from typing import Any, Iterable

from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.config import get_settings

_embedder: TextEmbedding | None = None
_client: QdrantClient | None = None


def embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name=get_settings().embedding_model)
    return _embedder


def embed(texts: Iterable[str]) -> list[list[float]]:
    return [vector.tolist() for vector in embedder().embed(list(texts))]


def client() -> QdrantClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = QdrantClient(path=str(settings.qdrant_path))
        # Close before the interpreter tears down. Qdrant's own __del__ runs
        # too late and raises a confusing ImportError instead; relying on
        # every entry point remembering to call close() does not scale.
        atexit.register(close)
    return _client


def ensure_collection() -> None:
    settings = get_settings()
    existing = {c.name for c in client().get_collections().collections}
    if settings.collection_name in existing:
        return
    client().create_collection(
        collection_name=settings.collection_name,
        vectors_config=VectorParams(
            size=settings.embedding_dim,
            distance=Distance.COSINE,
        ),
    )


def add_chunks(chunks: list[dict[str, Any]]) -> int:
    """Add text chunks. Each needs a 'text' key; everything else is payload."""
    if not chunks:
        return 0
    ensure_collection()
    vectors = embed(chunk["text"] for chunk in chunks)
    points = [
        PointStruct(id=str(uuid.uuid4()), vector=vector, payload=chunk)
        for vector, chunk in zip(vectors, chunks)
    ]
    client().upsert(
        collection_name=get_settings().collection_name,
        points=points,
    )
    return len(points)


def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    ensure_collection()
    vector = embed([query])[0]
    hits = client().query_points(
        collection_name=get_settings().collection_name,
        query=vector,
        limit=limit,
    ).points
    return [{"score": hit.score, **(hit.payload or {})} for hit in hits]


def count() -> int:
    ensure_collection()
    return client().count(collection_name=get_settings().collection_name).count


def close() -> None:
    """Release the embedded Qdrant file lock.

    Without an explicit close, Qdrant's __del__ runs during interpreter
    shutdown and raises a confusing ImportError. Scripts should call this.
    """
    global _client
    if _client is not None:
        _client.close()
        _client = None
