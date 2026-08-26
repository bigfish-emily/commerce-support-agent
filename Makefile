.PHONY: build up down logs build-data test lint format eval eval-llm mcp clean

build:
	docker compose build

up:
	docker compose up

build-data:
	python scripts/build_olist_dataset.py
	python scripts/build_bitext_dataset.py
	python scripts/build_rescommons_dataset.py
	python scripts/build_v1rtucious_eval.py

down:
	docker compose down

logs:
	docker compose logs -f

test:
	python -m pytest tests/ -v

lint:
	ruff check app/ evaluation/ scripts/ tests/

format:
	ruff format app/ evaluation/ scripts/ tests/

eval:
	python -m evaluation.intent_eval
	python -m evaluation.multi_intent_eval
	python -m evaluation.task_eval
	python -m evaluation.rag_retrieval_eval
	python -m evaluation.hybrid_retrieval_eval
	python -m evaluation.v1rtucious_eval_profile
	python -m evaluation.knowledge_eval
	python -m evaluation.tool_repair_eval
	python -m evaluation.trajectory_eval
	python -m evaluation.performance_eval
	python -m evaluation.agent_metrics_report
	python -m evaluation.deepeval_export

eval-llm:
	python -m evaluation.intent_planner_eval
	RUN_LLM_ROUTER_EVAL=1 python -m evaluation.intent_eval

mcp:
	python -m app.mcp_server

clean:
	docker compose down -v
