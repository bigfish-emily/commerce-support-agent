from langchain_openai import ChatOpenAI

from app.llm.offline import OfflineChatModel


class LlmClient:
    """OpenAI-compatible chat client wrapper.

    Created once in di.py and injected into LLM components.
    """

    def __init__(self, api_key: str = "", model: str = "gpt-4o-mini", base_url: str | None = None) -> None:
        self.model = model
        self.base_url = base_url or "https://api.openai.com/v1"
        if not api_key or api_key in {"offline", "test-key", "sk-your-key-here"}:
            self.mode = "offline_workflow"
            self.chat_openai = OfflineChatModel()
        else:
            self.mode = "live_llm_agent"
            self.chat_openai = ChatOpenAI(
                model=model,
                api_key=api_key,
                base_url=base_url,
                temperature=0.3,
            )
