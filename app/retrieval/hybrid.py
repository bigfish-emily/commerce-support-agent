from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "data" / "rescommons_derived" / "support_corpus.jsonl"
VECTOR_CANDIDATES = 300
FUSION_CANDIDATES = 40


@dataclass(frozen=True)
class RetrievalHit:
    doc: dict
    score: float


class HybridSupportRetriever:
    """Production-shaped local hybrid retrieval.

    The interfaces mirror an ES/BM25 + vector DB + reranker stack while keeping
    the project runnable without external services or API keys.
    """

    def __init__(self, corpus_path: Path = DEFAULT_CORPUS) -> None:
        self.docs = _load_jsonl(corpus_path)
        self._doc_idx_by_id = {doc["doc_id"]: idx for idx, doc in enumerate(self.docs)}
        self.doc_tokens = [_tokens(doc["text"]) for doc in self.docs]
        self.doc_counters = [Counter(tokens) for tokens in self.doc_tokens]
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.avgdl = sum(self.doc_lengths) / max(len(self.doc_lengths), 1)
        self.inverted: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for idx, counts in enumerate(self.doc_counters):
            for token, tf in counts.items():
                self.inverted[token].append((idx, tf))

    def bm25_search(self, query: str, k: int = 5) -> list[RetrievalHit]:
        return self._rank(self._bm25_scores(query), k)

    def vector_search(self, query: str, k: int = 5) -> list[RetrievalHit]:
        candidate_scores = self._bm25_scores(query)
        candidates = [
            idx
            for idx, _ in sorted(candidate_scores.items(), key=lambda item: item[1], reverse=True)[
                :VECTOR_CANDIDATES
            ]
        ]
        return self._vector_search_candidates(query, candidates, k)

    def hybrid_search(self, query: str, k: int = 5) -> list[RetrievalHit]:
        bm25_scores = self._bm25_scores(query)
        candidates = [
            idx
            for idx, _ in sorted(bm25_scores.items(), key=lambda item: item[1], reverse=True)[
                :VECTOR_CANDIDATES
            ]
        ]
        vector_scores = self._vector_scores(query, candidates)
        scores: dict[int, float] = defaultdict(float)
        bm25_ranked = sorted(bm25_scores.items(), key=lambda item: item[1], reverse=True)[:FUSION_CANDIDATES]
        vector_ranked = sorted(vector_scores.items(), key=lambda item: item[1], reverse=True)[
            :FUSION_CANDIDATES
        ]
        for rank, (idx, _) in enumerate(bm25_ranked, start=1):
            scores[idx] += 0.2 / (rank + 20)
        for rank, (idx, _) in enumerate(vector_ranked, start=1):
            scores[idx] += 2.0 / (rank + 20)
        return self._rank(scores, k)

    def _bm25_scores(self, query: str) -> dict[int, float]:
        query_tokens = _tokens(query)
        scores: dict[int, float] = defaultdict(float)
        n_docs = len(self.docs)
        k1 = 1.5
        b = 0.75
        for token in query_tokens:
            postings = self.inverted.get(token, [])
            if not postings:
                continue
            idf = math.log(1 + (n_docs - len(postings) + 0.5) / (len(postings) + 0.5))
            for idx, tf in postings:
                dl = self.doc_lengths[idx] or 1
                denom = tf + k1 * (1 - b + b * dl / self.avgdl)
                scores[idx] += idf * (tf * (k1 + 1) / denom)
        return scores

    def _vector_search_candidates(self, query: str, candidates: list[int], k: int) -> list[RetrievalHit]:
        return self._rank(self._vector_scores(query, candidates), k)

    def _vector_scores(self, query: str, candidates: list[int]) -> dict[int, float]:
        query_ngrams = _char_ngrams(query)
        if not query_ngrams:
            return {}
        scores = {}
        for idx in candidates:
            ngrams = _char_ngrams(self.docs[idx]["text"])
            overlap = len(query_ngrams & ngrams)
            if overlap:
                scores[idx] = overlap / math.sqrt(len(query_ngrams) * len(ngrams))
        return scores

    def _rank(self, scores: dict[int, float], k: int) -> list[RetrievalHit]:
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:k]
        return [RetrievalHit(doc=self.docs[idx], score=score) for idx, score in ranked]


def _load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _tokens(text: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", text.lower()) if token]


def _char_ngrams(text: str, n: int = 4) -> set[str]:
    compact = re.sub(r"\s+", " ", text.lower())
    if len(compact) <= n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}
