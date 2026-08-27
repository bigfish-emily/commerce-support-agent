"""LLM-based guardrails - both input (is this on-topic?) and output (is this response valid?)."""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.llm.json_fallback import add_json_instruction, native_structured_output_enabled, parse_json_model
from app.llm.prompts import INPUT_GUARD_PROMPT, OUTPUT_GUARD_PROMPT
from app.llm.types import InputGuardResult, OutputGuardResult

logger = logging.getLogger(__name__)


class Guardrail:
    """Input and output guardrails using LLM classification.

    Uses OpenAI's native structured output for guaranteed JSON schema compliance.
    In production, guardrails use a cheaper/faster model than the main agent.
    Here we reuse the same model for both to keep things simple.
    """

    def __init__(self, llm: ChatOpenAI) -> None:
        self._base_llm = llm
        native_structured = native_structured_output_enabled(llm)
        self._input_llm = llm.with_structured_output(InputGuardResult) if native_structured else None
        self._output_llm = llm.with_structured_output(OutputGuardResult) if native_structured else None

    async def check_input(self, message: str, history: list[dict] | None = None) -> InputGuardResult:
        context_text = ""
        if history:
            last_exchanges = history[-4:]  # last 2 turns (user + assistant)
            context_text = (
                "Conversation so far:\n"
                + "\n".join(f"[{m['role']}] {m['content'][:200]}" for m in last_exchanges)
                + "\n\n"
            )

        messages = [
            SystemMessage(content=INPUT_GUARD_PROMPT),
            HumanMessage(content=f"{context_text}User message: {message}"),
        ]
        try:
            if self._input_llm is None:
                raw = await self._base_llm.ainvoke(add_json_instruction(messages, InputGuardResult))
                return parse_json_model(raw, InputGuardResult)
            return await self._input_llm.ainvoke(messages)
        except Exception as exc:
            logger.warning("Structured input guard failed, trying JSON-text fallback: %s", exc)
            try:
                raw = await self._base_llm.ainvoke(add_json_instruction(messages, InputGuardResult))
                return parse_json_model(raw, InputGuardResult)
            except Exception as fallback_exc:
                logger.warning("Input guard LLM failed, using heuristic fallback: %s", fallback_exc)
                return _heuristic_input_guard(message)

    async def check_output(self, answer: str) -> OutputGuardResult:
        messages = [
            SystemMessage(content=OUTPUT_GUARD_PROMPT),
            HumanMessage(content=answer),
        ]
        try:
            if self._output_llm is None:
                raw = await self._base_llm.ainvoke(add_json_instruction(messages, OutputGuardResult))
                return parse_json_model(raw, OutputGuardResult)
            return await self._output_llm.ainvoke(messages)
        except Exception as exc:
            logger.warning("Structured output guard failed, trying JSON-text fallback: %s", exc)
            try:
                raw = await self._base_llm.ainvoke(add_json_instruction(messages, OutputGuardResult))
                return parse_json_model(raw, OutputGuardResult)
            except Exception as fallback_exc:
                logger.warning("Output guard LLM failed, using deterministic fallback: %s", fallback_exc)
            if not answer.strip():
                return OutputGuardResult(valid=False, reason="empty answer")
            if "[TODO]" in answer or "Traceback" in answer:
                return OutputGuardResult(valid=False, reason="placeholder or traceback")
            return OutputGuardResult(valid=True, reason="llm unavailable; deterministic checks passed")


def _heuristic_input_guard(message: str) -> InputGuardResult:
    text = message.lower()
    blocked = (
        "ignore your instructions",
        "ignore previous instructions",
        "disregard your instructions",
        "developer message",
        "system prompt",
        "hidden prompt",
        "print your prompt",
        "reveal your prompt",
        "jailbreak",
        "dan mode",
        "act as dan",
        "bypass",
        "越权",
        "绕过",
        "忽略之前",
        "忽略你的指令",
        "系统提示词",
        "开发者消息",
        "提示词",
        "泄露",
        "删除数据库",
        "drop table",
        "delete from",
        "操作系统内核",
        "python code",
        "javascript",
        "exploit",
        "sql injection",
        "xss",
        "色情",
        "仇恨",
    )
    if any(item in text for item in blocked):
        return InputGuardResult(on_topic=False, reason="heuristic off-topic or unsafe")
    return InputGuardResult(on_topic=True, reason="llm unavailable; fail-open for support availability")
