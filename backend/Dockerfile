# Build from the repository root: docker build -f backend/Dockerfile .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    HF_HOME=/data/huggingface \
    CHESS_TUTOR_DATABASE_PATH=/data/chess_tutor.db \
    CHESS_TUTOR_STOCKFISH_PATH=/usr/games/stockfish \
    PORT=8000

# git: the pinned Maia-3 commit is fetched from GitHub by scripts/install_maia3.sh.
RUN apt-get update \
    && apt-get install -y --no-install-recommends stockfish git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /uvx /usr/local/bin/

WORKDIR /app
# The repository layout is kept so the backend finds tools/ relative to its source.
COPY scripts/install_maia3.sh scripts/install_maia3.sh
RUN scripts/install_maia3.sh && rm -rf /root/.cache

COPY backend/pyproject.toml backend/uv.lock backend/.python-version backend/alembic.ini backend/
COPY backend/src backend/src
COPY backend/migrations backend/migrations
COPY tools tools
RUN cd backend && uv sync --locked --no-dev

RUN useradd --system --create-home app && mkdir -p /data && chown app /data
USER app
VOLUME /data

WORKDIR /app/backend
ENV PATH="/app/backend/.venv/bin:$PATH"
CMD ["sh", "-c", "exec uvicorn chess_tutor.main:create_app --factory --host 0.0.0.0 --port $PORT"]
