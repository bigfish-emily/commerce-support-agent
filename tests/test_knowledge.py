from app.olist.knowledge import MarkdownKnowledgeBase


def test_markdown_knowledge_base_searches_policy_sections() -> None:
    hits = MarkdownKnowledgeBase().search("refund coupon compensation human confirmation", k=2)
    assert hits
    assert "human confirmation" in hits[0].text.lower() or "confirmation" in hits[0].text.lower()
