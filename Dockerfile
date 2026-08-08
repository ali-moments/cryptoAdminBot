# Production Dockerfile for Crypto Admin Bot
FROM python:3.12-slim


WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        git \
        build-essential \
        libcairo2-dev \
        pkg-config \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*


COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv


ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON=/usr/local/bin/python3.12


COPY pyproject.toml uv.lock ./


RUN uv sync --frozen --no-dev


COPY . .


RUN mkdir -p sessions generated


RUN chmod +x scripts/*.py


HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import asyncio; import sys; sys.exit(0)" || exit 1


CMD ["uv", "run", "app/main.py"]