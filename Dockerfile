FROM python:3.11-slim

WORKDIR /app

# Install uv - fast Python package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies in a separate layer - cache hit when deps don't change
COPY pyproject.toml .
RUN uv pip install --system --no-cache \
    fastapi \
    "uvicorn[standard]" \
    pydantic \
    langgraph \
    langgraph-checkpoint-sqlite \
    aiosqlite \
    langchain-openai \
    mcp \
    python-dotenv

# Copy application code and runtime/eval data
COPY app/ app/
COPY scripts/ scripts/
COPY data/olist_derived/ data/olist_derived/
COPY data/bitext_derived/ data/bitext_derived/
COPY data/rescommons_derived/ data/rescommons_derived/
COPY data/knowledge_base/ data/knowledge_base/

# Run as non-root user
RUN useradd --create-home appuser && mkdir -p data && chown appuser:appuser data
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
