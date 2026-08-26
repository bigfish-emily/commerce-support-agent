import json
import os
from pathlib import Path

from dotenv import load_dotenv

from app.llm.client import LlmClient
from app.llm.intent_planner import IntentPlanner

load_dotenv()

EVAL_PATH = Path(__file__).resolve().parents[1] / "data" / "olist_derived" / "eval_cases.jsonl"

llm_client = LlmClient(
    api_key=os.environ["OPENAI_API_KEY"],
    model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
    base_url=os.environ.get("OPENAI_BASE_URL"),
)

intent_planner = IntentPlanner(llm_client.chat_openai)


def load_cases() -> list[dict]:
    with EVAL_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def select_cases(cases: list[dict], limit: int) -> list[dict]:
    if os.environ.get("LLM_EVAL_BALANCED", "0") != "1":
        return cases[:limit]
    selected: list[dict] = []
    seen_intents: set[str] = set()
    for case in cases:
        intent = case.get("expected_intent", case.get("expected_skill"))
        if intent in seen_intents:
            continue
        selected.append(case)
        seen_intents.add(intent)
        if len(selected) >= limit:
            return selected
    if len(selected) < limit:
        selected_ids = {case["id"] for case in selected}
        selected.extend(case for case in cases if case["id"] not in selected_ids)
    return selected[:limit]


async def evaluate() -> None:
    limit = int(os.environ.get("LLM_EVAL_LIMIT", "20"))
    cases = select_cases(load_cases(), limit)
    wrong_predictions: dict[str, int] = {}
    correct = 0
    for case in cases:
        message = case["message"]
        expected_intent = case.get("expected_intent", case.get("expected_skill"))
        result = await intent_planner.classify(message)
        result_correct = result.intent == expected_intent
        if result_correct:
            correct += 1
        else:
            key = expected_intent + ":" + result.intent
            wrong_predictions[key] = wrong_predictions.get(key, 0) + 1

        marker = "CORRECT" if result_correct else "WRONG"
        print(
            f'Case {case["id"]}: "{message}", expected: {expected_intent}, '
            f"planner route result: {result.intent} - {marker}"
        )

    print(f"Cases: {len(cases)} (LLM_EVAL_LIMIT={limit})")
    print(f"Accuracy: {correct / len(cases):.2%}")
    print("Wrong predictions:")
    for key, count in sorted(wrong_predictions.items()):
        expected_intent, result_intent = key.split(":")
        print(f"  expected={expected_intent}, got={result_intent} x {count}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(evaluate())
