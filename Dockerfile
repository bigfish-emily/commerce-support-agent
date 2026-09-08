FROM python:3.11-slim

WORKDIR /app

# Install uv - fast Python package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install locked dependencies in a separate layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra redis --extra vector --no-install-project
ENV PATH="/app/.venv/bin:$PATH"

# Copy application code and runtime/eval data
COPY app/ app/
COPY frontend/ frontend/
COPY scripts/ scripts/
COPY data/olist_derived/ data/olist_derived/
COPY data/bitext_derived/ data/bitext_derived/
COPY data/rescommons_derived/ data/rescommons_derived/
COPY data/knowledge_base/ data/knowledge_base/

# Run as non-root user
RUN useradd --create-home appuser && mkdir -p data && chown -R appuser:appuser data frontend
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
