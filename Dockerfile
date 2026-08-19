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
        fontconfig \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install application fonts
COPY fonts/*.ttf /usr/local/share/fonts/
RUN fc-cache -f -v

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON=/usr/local/bin/python3.12

COPY pyproject.toml uv.lock README.md ./

RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN uv sync --frozen --no-dev

RUN mkdir -p sessions generated logs

CMD ["uv", "run", "--frozen", "--no-dev", "python", "-m", "app.main"]
