from app.olist.knowledge import MarkdownKnowledgeBase


def test_cancellation_synonyms_retrieve_applicable_policy():
    from app.llm.offline import OfflineChatModel
    from app.llm.response_generator import PolicyResponseGenerator

    query = "包裹已经送达后还能撤销订单吗？"
    hits = MarkdownKnowledgeBase().search(query)
    sections = [{"section_title": hit.section_title, "text": hit.text} for hit in hits]
    assert any(hit.section_title == "Order Cancellation Eligibility Policy" for hit in hits)
    reply = PolicyResponseGenerator(OfflineChatModel()).customer_safe_template(query, sections)
    assert "不能直接取消" in reply
    assert "没有为您取消订单" in reply


def test_markdown_knowledge_base_searches_policy_sections() -> None:
    hits = MarkdownKnowledgeBase().search("refund coupon compensation human confirmation", k=2)
    assert hits
    assert "human confirmation" in hits[0].text.lower() or "confirmation" in hits[0].text.lower()


def test_markdown_knowledge_base_searches_support_faq() -> None:
    hits = MarkdownKnowledgeBase().search("退款进度是否已经完成", k=3)

    assert hits
    assert any(hit.source_type == "faq" for hit in hits)
    assert any("Refund Status FAQ" == hit.section_title for hit in hits)


def test_markdown_knowledge_base_searches_merchant_rules() -> None:
    hits = MarkdownKnowledgeBase().search("美妆 health beauty 已送达订单能不能取消", k=5)

    assert hits
    assert any(hit.source_type == "merchant_rule" for hit in hits)
    assert any("Health Beauty Merchant Rule" == hit.section_title for hit in hits)
