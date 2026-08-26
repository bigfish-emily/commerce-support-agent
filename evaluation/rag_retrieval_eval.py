from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.olist.catalog import load_orders
from app.olist.retrieval import (
    RetrievalHit,
    adaptive_category_retrieval,
    exact_underscore_retrieval,
    token_overlap_retrieval,
)

Retriever = Callable[[str, list[str], int], list[RetrievalHit]]


@dataclass(frozen=True)
class RetrievalCase:
    case_id: str
    query: str
    expected_category: str
    variant: str


def build_cases(limit_categories: int = 50) -> list[RetrievalCase]:
    categories = []
    seen = set()
    for order in load_orders():
        for product in order["products"]:
            category = str(product["category"])
            if category != "unknown" and category not in seen:
                seen.add(category)
                categories.append(category)

    cases: list[RetrievalCase] = []
    for category in categories[:limit_categories]:
        variants = {
            "canonical": category,
            "space_alias": category.replace("_", " "),
            "hyphen_alias": category.replace("_", "-"),
            "compact_alias": category.replace("_", ""),
        }
        for variant, mention in variants.items():
            cases.append(
                RetrievalCase(
                    case_id=f"{variant}-{category}",
                    query=f"{mention} 类目的订单主要有哪些物流和评价风险？",
                    expected_category=category,
                    variant=variant,
                )
            )
    return cases


def evaluate(name: str, retriever: Retriever, categories: list[str], cases: list[RetrievalCase]) -> dict:
    top1 = 0
    recall3 = 0
    reciprocal_rank = 0.0
    failures: list[str] = []

    for case in cases:
        hits = retriever(case.query, categories, 4)
        ranked = [hit.category for hit in hits]
        if ranked and ranked[0] == case.expected_category:
            top1 += 1
        if case.expected_category in ranked[:3]:
            recall3 += 1
            reciprocal_rank += 1 / (ranked.index(case.expected_category) + 1)
        else:
            failures.append(case.case_id)

    total = len(cases)
    return {
        "strategy": name,
        "cases": total,
        "top1": top1 / total,
        "recall@3": recall3 / total,
        "mrr@3": reciprocal_rank / total,
        "failures": failures,
    }


def main() -> None:
    categories = sorted(
        {
            str(product["category"])
            for order in load_orders()
            for product in order["products"]
            if product["category"] != "unknown"
        }
    )
    cases = build_cases()
    strategies: list[tuple[str, Retriever]] = [
        ("exact_underscore", exact_underscore_retrieval),
        ("token_overlap", token_overlap_retrieval),
        ("adaptive_rewrite", adaptive_category_retrieval),
    ]

    print(f"RAG retrieval eval cases: {len(cases)}")
    print("strategy,cases,top1,recall@3,mrr@3,failures")
    for name, retriever in strategies:
        result = evaluate(name, retriever, categories, cases)
        print(
            f"{result['strategy']},{result['cases']},"
            f"{result['top1']:.2%},{result['recall@3']:.2%},{result['mrr@3']:.2%},"
            f"{len(result['failures'])}"
        )
        if result["failures"]:
            print("  sample_failures=" + ",".join(result["failures"][:5]))


if __name__ == "__main__":
    main()
