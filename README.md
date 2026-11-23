# Chess Tutor

An AI-assisted chess tutor. Play a human-like engine (Maia-3) at your level while a coach — grounded in Stockfish analysis — flags blunders and great moves, asks what you were thinking, and points out what you might not have seen before you lock in a move. It learns your recurring weaknesses and adapts.

**Modes:** Regular play · Passive tutor · Active tutor

**Status:** design phase. See the [design spec](docs/specs/2026-11-23-chess-tutor-design.md) and the [issue tracker](https://github.com/frankbria/chess-tutor/issues) for the build plan.

**Stack:** FastAPI + python-chess + Stockfish + Maia-3 · Next.js PWA (shadcn/ui, chessground) · SQLite

## Quick start and configuration

```bash
cp .env.example .env     # set CHESS_TUTOR_LLM_API_KEY
make dev                 # backend :8000 + frontend :3000, Ctrl-C stops both
make test                # every package's tests
make lint                # every package's lint, format and type checks
```

`make dev` refuses to start, naming the variable to change, if a port is busy. The backend refuses to start without `CHESS_TUTOR_LLM_API_KEY` unless `CHESS_TUTOR_LLM_PROVIDER=none`. Settings come from the environment or the root `.env`. `make dev` does not read `.env`: its ports come only from the environment or the 8000/3000 defaults.

| Variable | Default | Purpose |
|---|---|---|
| `CHESS_TUTOR_BACKEND_PORT` | `8000` | Backend port (also used by `make dev`) |
| `FRONTEND_PORT` | `3000` | Frontend port (`make dev` only) |
| `CHESS_TUTOR_BACKEND_HOST` | `127.0.0.1` | Backend bind host |
| `CHESS_TUTOR_FRONTEND_ORIGINS` | localhost/127.0.0.1 on `FRONTEND_PORT` | JSON list of CORS origins |
| `NEXT_PUBLIC_API_BASE_URL` | `http://127.0.0.1:<backend port>` | API URL the frontend calls |
| `CHESS_TUTOR_DATABASE_PATH` | `<repo>/data/chess_tutor.db` | SQLite file (`data/` is gitignored) |
| `CHESS_TUTOR_STOCKFISH_PATH` | `/usr/games/stockfish` | Stockfish binary |
| `CHESS_TUTOR_MAIA_MODEL` / `CHESS_TUTOR_MAIA_DEVICE` | `maia-3` / `cpu` | Maia model and device |
| `CHESS_TUTOR_LLM_PROVIDER` | `anthropic` | `anthropic`, or `none` for no coach |
| `CHESS_TUTOR_LLM_API_KEY` | required | LLM API key |
| `CHESS_TUTOR_COACHING_MODEL` / `CHESS_TUTOR_CHEAP_MODEL` | `claude-sonnet-5-5` / `claude-haiku-5-5` | Model tiers |
| `CHESS_TUTOR_THRESHOLDS__...` | see `settings.py` | Classification, phase, concept-state and rating-weight thresholds |

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
pre-commit run --all-files   # ruff, black, mypy, eslint, tsc, gitleaks secrets scan, file hygiene (CI runs these too)
```

CI (`.github/workflows/ci.yml`) runs the same checks plus tests and the frontend build on every PR.
