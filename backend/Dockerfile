# Gravelord orchestrator container.
#
# Build with the monorepo's `backend/` as the build context:
#   docker build -t gravelord-backend backend/
#
# Deploy to Cloud Run with min-instances=1 so the daemon stays alive between
# poll ticks; scale-to-zero would kill the orchestrator loop.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN uv sync --frozen --no-dev || uv sync --no-dev

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "gravelord.main:app", "--host", "0.0.0.0", "--port", "8000"]
