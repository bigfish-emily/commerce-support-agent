"""Dependency injection — module-level singletons wired together."""

import os

from dotenv import load_dotenv

from app.agent.actions import AgentActions
from app.agent.graph import AgentGraph
from app.llm.client import LlmClient
from app.llm.guardrail import Guardrail
from app.llm.intent_planner import IntentPlanner
from app.llm.response_generator import OlistTaskExtractor, PolicyResponseGenerator, QaResponseGenerator
from app.olist.knowledge import MarkdownKnowledgeBase
from app.olist.service import OlistService, SQLiteCaseService
from app.retrieval.hybrid import HybridSupportRetriever

load_dotenv()

olist_service = OlistService()
knowledge_base = MarkdownKnowledgeBase()
support_retriever = HybridSupportRetriever()
case_service = SQLiteCaseService()

llm_client = LlmClient(
    api_key=os.environ.get("OPENAI_API_KEY", "offline"),
    model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
)
runtime_status = {
    "mode": llm_client.mode,
    "model": llm_client.model,
    "base_url": llm_client.base_url,
}

intent_planner = IntentPlanner(llm_client.chat_openai)
qa_generator = QaResponseGenerator(llm_client.chat_openai)
policy_generator = PolicyResponseGenerator(llm_client.chat_openai)
task_extractor = OlistTaskExtractor(llm_client.chat_openai)
guardrail = Guardrail(llm_client.chat_openai)

actions = AgentActions(
    intent_planner=intent_planner,
    qa_generator=qa_generator,
    policy_generator=policy_generator,
    task_extractor=task_extractor,
    olist_service=olist_service,
    knowledge_base=knowledge_base,
    support_retriever=support_retriever,
    case_service=case_service,
)

agent_graph_builder = AgentGraph(actions=actions)
