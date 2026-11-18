# Build stage
FROM python:3.12-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY engine ./engine
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Final runtime image (shared by app and worker)
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid appuser --home-dir /app --no-create-home --shell /usr/sbin/nologin appuser && \
    mkdir -p /data && chown appuser:appuser /data

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser engine ./engine
COPY --chown=appuser:appuser app.py ./app.py
COPY --chown=appuser:appuser .streamlit ./.streamlit
COPY --chown=appuser:appuser fixtures ./fixtures
COPY --chown=appuser:appuser locales ./locales
COPY --chown=appuser:appuser packs ./packs
COPY --chown=appuser:appuser prompts ./prompts

USER appuser

EXPOSE 8501

CMD ["python", "-m", "engine"]
