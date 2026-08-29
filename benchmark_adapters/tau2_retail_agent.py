#!/usr/bin/env python3
"""tau2/tau3-bench retail adapter for this project.

Run this file inside a separate tau2-bench checkout, because tau2 requires
Python 3.12+ while the main project intentionally stays on Python 3.11.

Example:
    cd /path/to/tau2-bench
    uv sync
    OPENAI_API_KEY=... uv run python /path/to/this/file --num-tasks 5
"""

from __future__ import annotations

import argparse

from pydantic import BaseModel, Field

try:
    from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
    from tau2.data_model.message import (
        APICompatibleMessage,
        AssistantMessage,
        Message,
        MultiToolMessage,
        SystemMessage,
    )
    from tau2.data_model.simulation import TextRunConfig
    from tau2.environment.toolkit import Tool
    from tau2.registry import registry
    from tau2.runner import run_domain
    from tau2.utils.llm_utils import generate
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in tau2 env
    raise SystemExit(
        "This adapter must be executed inside a tau2-bench environment. "
        "See docs/BENCHMARK_PROJECTS.md for setup steps."
    ) from exc


SYSTEM_PROMPT = """\
You are a production-grade retail customer service agent evaluated by tau-bench.

Follow the domain policy exactly.
Use tools for factual customer/order/product information.
Never invent order, user, product, item, payment, address, or refund facts.

Execution policy:
- Decompose the user's request into all required read-only and write tasks.
- Prefer read-only tools before any write tool.
- For write tools, explain the exact planned mutation and wait for explicit
  user confirmation unless the domain policy says no confirmation is needed.
- For multi-item write tools, confirm each item separately in the confirmation
  message and execute only the item ids that the customer explicitly confirms
  after that message. If the confirmation changes or narrows the scope, use the
  latest confirmed scope.
- If a required slot is missing or ambiguous, ask a concise clarification.
- If a tool call fails, repair the parameter once when the error is recoverable;
  otherwise explain the limitation and follow the policy fallback.
- Transfer to a human only when policy or missing information prevents safe
  resolution.

This prompt intentionally mirrors the engineering contract of the local Olist
Agent project: LLM handles planning and language, tau2 tools handle facts and
side effects, and write actions require a confirmation boundary.

<domain_policy>
{domain_policy}
</domain_policy>
"""


class RetailBenchmarkState(BaseModel):
    system_messages: list[SystemMessage]
    messages: list[APICompatibleMessage] = Field(default_factory=list)
    turns: int = 0


class PolicyAwareRetailAgent(HalfDuplexAgent[RetailBenchmarkState]):
    """A tau2 HalfDuplexAgent with strict policy/tool/HITL instructions."""

    def __init__(
        self,
        tools: list[Tool],
        domain_policy: str,
        llm: str,
        llm_args: dict | None = None,
    ) -> None:
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.llm = llm
        self.llm_args = llm_args or {}

    def get_init_state(
        self,
        message_history: list[Message] | None = None,
    ) -> RetailBenchmarkState:
        system_prompt = SYSTEM_PROMPT.format(domain_policy=self.domain_policy)
        return RetailBenchmarkState(
            system_messages=[SystemMessage(role="system", content=system_prompt)],
            messages=list(message_history) if message_history else [],
        )

    def generate_next_message(
        self,
        message: ValidAgentInputMessage,
        state: RetailBenchmarkState,
    ) -> tuple[AssistantMessage, RetailBenchmarkState]:
        state.turns += 1
        if isinstance(message, MultiToolMessage):
            state.messages.extend(message.tool_messages)
        else:
            state.messages.append(message)

        response = generate(
            model=self.llm,
            tools=self.tools,
            messages=state.system_messages + state.messages,
            call_name="olist_policy_aware_retail_agent",
            **self.llm_args,
        )
        state.messages.append(response)
        return response, state


def create_policy_aware_retail_agent(tools, domain_policy, **kwargs):
    return PolicyAwareRetailAgent(
        tools=tools,
        domain_policy=domain_policy,
        llm=kwargs.get("llm", "openai/gpt-4.1-mini"),
        llm_args=kwargs.get("llm_args") or {"temperature": 0},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run tau2 retail subset with this adapter.")
    parser.add_argument("--agent-llm", default="openai/gpt-4.1-mini")
    parser.add_argument("--user-llm", default="openai/gpt-4.1-mini")
    parser.add_argument(
        "--judge-llm",
        default=None,
        help="LLM for tau2 natural-language assertion review; defaults to --user-llm.",
    )
    parser.add_argument("--num-tasks", type=int, default=5)
    parser.add_argument("--num-trials", type=int, default=1)
    parser.add_argument("--seed", type=int, default=300)
    parser.add_argument("--save-to", default="olist_agent_tau2_retail_subset")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--task-split-name", default="base")
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--auto-resume", action="store_true")
    args = parser.parse_args()
    judge_llm = args.judge_llm or args.user_llm

    import tau2.evaluator.evaluator_nl_assertions as nl_eval

    nl_eval.DEFAULT_LLM_NL_ASSERTIONS = judge_llm

    registry.register_agent_factory(create_policy_aware_retail_agent, "olist_policy_aware_retail")
    results = run_domain(
        TextRunConfig(
            domain="retail",
            agent="olist_policy_aware_retail",
            llm_agent=args.agent_llm,
            llm_user=args.user_llm,
            num_tasks=args.num_tasks,
            num_trials=args.num_trials,
            seed=args.seed,
            save_to=args.save_to,
            max_concurrency=args.max_concurrency,
            task_ids=args.task_id or None,
            task_split_name=args.task_split_name,
            timeout=args.timeout,
            auto_resume=args.auto_resume,
            review_model=judge_llm,
            hallucination_retries=0,
        )
    )
    rewards = [run.reward_info.reward for run in results.simulations if run.reward_info]
    if rewards:
        print(f"tau2 retail pass@1 subset: {sum(rewards) / len(rewards):.2%} ({sum(rewards)}/{len(rewards)})")


if __name__ == "__main__":
    main()
