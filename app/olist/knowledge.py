from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_KB_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge_base"


@dataclass(frozen=True)
class KnowledgeHit:
    source: str
    section_title: str
    text: str
    score: float


class MarkdownKnowledgeBase:
    """Small markdown KB adapter for policy/FAQ-style business documents."""

    def __init__(self, kb_dir: Path = _DEFAULT_KB_DIR) -> None:
        self._kb_dir = kb_dir

    def search(self, query: str, k: int = 3) -> list[KnowledgeHit]:
        sections = self._sections()
        query_tokens = set(_tokens(_expand_query(query)))
        scored = []
        for section in sections:
            title_tokens = set(_tokens(section["title"]))
            text_tokens = set(_tokens(section["text"]))
            title_overlap = query_tokens & title_tokens
            text_overlap = query_tokens & text_tokens
            if not title_overlap and not text_overlap:
                continue
            score = len(title_overlap) * 3.0 + len(text_overlap) / max(len(query_tokens), 1)
            scored.append((score, section))
        ranked = sorted(scored, key=lambda item: item[0], reverse=True)[:k]
        return [
            KnowledgeHit(
                source=section["source"],
                section_title=section["title"],
                text=section["text"],
                score=score,
            )
            for score, section in ranked
        ]

    def _sections(self) -> list[dict[str, str]]:
        sections: list[dict[str, str]] = []
        for path in sorted(self._kb_dir.glob("*.md")):
            current_title = path.stem
            current_lines: list[str] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    if current_lines:
                        sections.append(_section(path.name, current_title, current_lines))
                    current_title = line.removeprefix("## ").strip()
                    current_lines = []
                else:
                    current_lines.append(line)
            if current_lines:
                sections.append(_section(path.name, current_title, current_lines))
        return sections


def _section(source: str, title: str, lines: list[str]) -> dict[str, str]:
    text = "\n".join(line for line in lines if line.strip()).strip()
    return {
        "source": source,
        "title": title,
        "text": f"{title}\n{text}",
    }


def _tokens(text: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", text.lower()) if part]


def _expand_query(query: str) -> str:
    expansions = {
        "订单": "order",
        "状态": "status",
        "当前状态": "order status",
        "查什么": "query tool facts",
        "延迟": "delivery delay delayed",
        "晚于": "delivery delay delayed",
        "送达": "delivery delivered",
        "退款": "refund compensation",
        "优惠券": "coupon compensation",
        "补偿": "compensation coupon refund",
        "低分": "low review recovery",
        "1星": "low review",
        "2星": "low review",
        "取消": "cancellation canceled",
        "发票": "invoice request billing tax profile",
        "开票": "invoice request billing tax profile",
        "地址": "address change shipping recipient street postal",
        "收货地址": "address change shipping recipient street postal",
        "修改": "change request",
        "人工": "human approval confirmation",
        "确认": "approval confirmation",
        "创建": "create case side effect",
        "case": "case",
        "话术": "customer message style",
        "技术词": "internal language tool rag checkpoint workflow",
        "事实": "facts tool boundary",
        "哪里来": "tool boundary",
        "类目": "category operations",
        "运营风险": "category operations risk",
        "替代": "specific order exact lookup",
        "轨迹": "audit trace",
        "记录": "record trace",
        "审计": "audit trace",
    }
    extra = [value for key, value in expansions.items() if key in query]
    return f"{query} {' '.join(extra)}"
