# Chess Tutor

An AI-assisted chess tutor. Play a human-like engine (Maia-3) at your level while a coach — grounded in Stockfish analysis — flags blunders and great moves, asks what you were thinking, and points out what you might not have seen before you lock in a move. It learns your recurring weaknesses and adapts.

**Modes:** Regular play · Passive tutor · Active tutor

**Status:** design phase. See the [design spec](docs/specs/2026-11-23-chess-tutor-design.md) and the [issue tracker](https://github.com/frankbria/chess-tutor/issues) for the build plan.

**Stack:** FastAPI + python-chess + Stockfish + Maia-3 · Next.js PWA (shadcn/ui, chessground) · SQLite

## Running the backend

Requires [uv](https://docs.astral.sh/uv/).

```bash
cd backend
uv sync
uv run uvicorn chess_tutor.main:create_app --factory --reload   # http://127.0.0.1:8000/health
uv run pytest                                                   # tests + coverage (fails under 85%)
uv run ruff check && uv run black --check . && uv run mypy src tests
```

## Running the frontend

Requires Node 24+.

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
npm test             # vitest + Testing Library
npm run lint && npm run typecheck && npm run build
```

The app is an installable PWA. Browsers only offer install on `localhost` or over HTTPS, so to install on a phone, serve it over HTTPS (for example `tailscale serve`). Plain `http://` on the LAN loads the app but won't offer install.

## Contributing

Install the git hooks once per clone (requires [pre-commit](https://pre-commit.com/), plus the backend and frontend dependencies above):

```bash
pre-commit install
pre-commit run --all-files   # ruff, black, mypy, eslint, tsc, secrets and file hygiene
```

CI (`.github/workflows/ci.yml`) runs the same checks plus tests and the frontend build on every PR.
