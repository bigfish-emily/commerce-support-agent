from __future__ import annotations

import hashlib
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class VectorDocument:
    doc_id: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VectorSearchHit:
    doc_id: str
    score: float
    metadata: dict[str, Any]


class VectorStore(Protocol):
    def search(
        self,
        query: str,
        *,
        k: int = 5,
        candidate_ids: set[str] | None = None,
    ) -> list[VectorSearchHit]:
        """Return nearest documents for a text query."""


class HashingTextEmbedder:
    """Deterministic low-cost text embedder used for local tests and demos.

    Production deployments can replace this with a real embedding model while
    keeping the same VectorStore boundary.
    """

    def __init__(self, dimensions: int = 65536) -> None:
        self.dimensions = dimensions

    def embed_sparse(self, text: str) -> dict[int, float]:
        counts: dict[int, float] = {}
        for token, weight in _features(text):
            bucket = _stable_bucket(token, self.dimensions)
            counts[bucket] = max(counts.get(bucket, 0.0), weight)
        norm = math.sqrt(sum(value * value for value in counts.values()))
        if norm <= 0:
            return {}
        return {bucket: value / norm for bucket, value in counts.items()}

    def embed_dense(self, text: str) -> list[float]:
        sparse = self.embed_sparse(text)
        dense = [0.0] * self.dimensions
        for bucket, value in sparse.items():
            dense[bucket] = value
        return dense


class LocalVectorStore:
    """In-process vector index with an inverted sparse vector posting list."""

    def __init__(
        self,
        documents: list[VectorDocument],
        embedder: HashingTextEmbedder | None = None,
    ) -> None:
        self._embedder = embedder or HashingTextEmbedder()
        self._documents = {doc.doc_id: doc for doc in documents}
        self._vectors = {doc.doc_id: self._embedder.embed_sparse(doc.text) for doc in documents}
        self._postings: dict[int, list[tuple[str, float]]] = defaultdict(list)
        for doc_id, vector in self._vectors.items():
            for bucket, value in vector.items():
                self._postings[bucket].append((doc_id, value))

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        candidate_ids: set[str] | None = None,
    ) -> list[VectorSearchHit]:
        query_vector = self._embedder.embed_sparse(query)
        if not query_vector:
            return []
        scores: dict[str, float] = defaultdict(float)
        for bucket, query_value in query_vector.items():
            for doc_id, doc_value in self._postings.get(bucket, []):
                if candidate_ids is not None and doc_id not in candidate_ids:
                    continue
                scores[doc_id] += query_value * doc_value
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:k]
        return [
            VectorSearchHit(
                doc_id=doc_id,
                score=score,
                metadata=self._documents[doc_id].metadata,
            )
            for doc_id, score in ranked
        ]


class QdrantVectorStore:
    """Qdrant-backed vector store adapter.

    This adapter is intentionally thin: the project can run without Qdrant, and
    Docker deployments can switch to it by installing `qdrant-client` and
    setting SUPPORT_VECTOR_BACKEND=qdrant.
    """

    def __init__(
        self,
        *,
        url: str,
        collection_name: str,
        documents: list[VectorDocument],
        embedder: HashingTextEmbedder | None = None,
    ) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http.models import Distance, PointStruct, VectorParams
        except ModuleNotFoundError as exc:  # pragma: no cover - optional backend
            raise RuntimeError("Install vector support with `pip install .[vector]`.") from exc

        self._embedder = embedder or HashingTextEmbedder(dimensions=384)
        self._client = QdrantClient(url=url)
        self._collection_name = collection_name
        existing = {collection.name for collection in self._client.get_collections().collections}
        if collection_name not in existing:
            self._client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=self._embedder.dimensions, distance=Distance.COSINE),
            )
        count = self._client.count(collection_name=collection_name, exact=False).count
        if count == 0:
            self._client.upsert(
                collection_name=collection_name,
                points=[
                    PointStruct(
                        id=_qdrant_point_id(doc.doc_id),
                        vector=self._embedder.embed_dense(doc.text),
                        payload={"doc_id": doc.doc_id, **doc.metadata},
                    )
                    for doc in documents
                ],
            )

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        candidate_ids: set[str] | None = None,
    ) -> list[VectorSearchHit]:
        query_vector = self._embedder.embed_dense(query)
        query_filter = None
        if candidate_ids:
            try:
                from qdrant_client.http.models import FieldCondition, Filter, MatchAny
            except ModuleNotFoundError as exc:  # pragma: no cover - optional backend
                raise RuntimeError("Install vector support with `pip install .[vector]`.") from exc
            query_filter = Filter(
                must=[FieldCondition(key="doc_id", match=MatchAny(any=list(candidate_ids)))]
            )
        hits = self._client.search(
            collection_name=self._collection_name,
            query_vector=query_vector,
            query_filter=query_filter,
            limit=k,
        )
        return [
            VectorSearchHit(
                doc_id=str(hit.payload.get("doc_id", hit.id)),
                score=float(hit.score),
                metadata=dict(hit.payload or {}),
            )
            for hit in hits
        ]


def build_vector_store_from_env(documents: list[VectorDocument]) -> VectorStore:
    backend = os.environ.get("SUPPORT_VECTOR_BACKEND", "local").strip().lower()
    if backend in {"local", "memory", "inmemory", ""}:
        return LocalVectorStore(documents)
    if backend == "qdrant":
        return QdrantVectorStore(
            url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
            collection_name=os.environ.get("QDRANT_COLLECTION", "olist_support_examples"),
            documents=documents,
        )
    raise RuntimeError(f"Unsupported SUPPORT_VECTOR_BACKEND: {backend}")


def _features(text: str) -> list[tuple[str, float]]:
    normalized = re.sub(r"\s+", " ", text.lower()).strip()
    features: list[tuple[str, float]] = []
    compact = re.sub(r"\s+", " ", normalized)
    if len(compact) <= 4:
        if compact:
            features.append((f"c:{compact}", 1.0))
        return features
    features.extend((f"c:{compact[index : index + 4]}", 1.0) for index in range(len(compact) - 3))
    return features


def _stable_bucket(token: str, dimensions: int) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dimensions


def _qdrant_point_id(doc_id: str) -> int:
    digest = hashlib.blake2b(doc_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=False)
