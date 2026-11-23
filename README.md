# Chess Tutor

An AI-assisted chess tutor. Play a human-like engine (Maia-3) at your level while a coach — grounded in Stockfish analysis — flags blunders and great moves, asks what you were thinking, and points out what you might not have seen before you lock in a move. It learns your recurring weaknesses and adapts.

**Modes:** Regular play · Passive tutor · Active tutor

**Status:** design phase. See the [design spec](docs/specs/2026-11-23-chess-tutor-design.md) and the [issue tracker](https://github.com/frankbria/chess-tutor/issues) for the build plan.

**Stack:** FastAPI + python-chess + Stockfish + Maia-3 · Next.js PWA (shadcn/ui, chessground) · SQLite

## Quick start and configuration

```bash
cp .env.example .env     # optional; for the coach run `ant auth login` or set CHESS_TUTOR_LLM_API_KEY
make dev                 # backend :8000 + frontend :3000, Ctrl-C stops both
make test                # every package's tests
make lint                # every package's lint, format and type checks
```

`make dev` refuses to start, naming the variable to change, if a port is busy. Settings come from the environment or the root `.env`. `make dev` does not read `.env`: its ports come only from the environment or the 8000/3000 defaults.

| Variable | Default | Purpose |
|---|---|---|
| `CHESS_TUTOR_BACKEND_PORT` | `8000` | Backend port (also used by `make dev`) |
| `FRONTEND_PORT` | `3000` | Frontend port (`make dev` only) |
| `CHESS_TUTOR_BACKEND_HOST` | `127.0.0.1` | Backend bind host |
| `DEV_HOST` / `PUBLIC_HOST` | `127.0.0.1` / empty | Frontend bind interface, and the address a phone uses (`make dev` only; see [phone access](docs/phone-access.md)) |
| `CHESS_TUTOR_FRONTEND_ORIGINS` | localhost/127.0.0.1 on `FRONTEND_PORT` | JSON list of CORS origins |
| `NEXT_PUBLIC_API_BASE_URL` | `http://127.0.0.1:<backend port>`, or `/api` when `PUBLIC_HOST` is set | API URL the frontend calls |
| `CHESS_TUTOR_DATABASE_PATH` | `<repo>/data/chess_tutor.db` | SQLite file (`data/` is gitignored) |
| `CHESS_TUTOR_STOCKFISH_PATH` | `/usr/games/stockfish` | Stockfish binary |
| `CHESS_TUTOR_OPPONENT_STEERING_ENABLED` | `false` | Stretch: on some moves the opponent plays a Maia top-4 move that sets up your FOCUS-concept motifs |
| `CHESS_TUTOR_OPPONENT_STEERING_RATE` | `0.25` | Share of opponent moves that may be steered; the rest are plain Maia |
| `CHESS_TUTOR_OPPONENT_STEERING_MAX_EVAL_LOSS_CENTIPAWNS` | `30` | A steered move never costs more than this versus Maia's first choice (Stockfish quick) |
| `CHESS_TUTOR_MAIA_COMMAND` | `maia3-uci` | Maia-3 UCI executable |
| `CHESS_TUTOR_MAIA_MODEL` / `CHESS_TUTOR_MAIA_DEVICE` | `5m` / `cpu` | Maia-3 size (`3m`, `5m`, `23m`, `79m`) and torch device |
| `CHESS_TUTOR_LLM_PROVIDER` | `anthropic` | `anthropic`, or `none` for no coach |
| `CHESS_TUTOR_LLM_API_KEY` | unset | Optional API key. Unset, the Anthropic SDK uses `ANTHROPIC_API_KEY` or the profile from `ant auth login` |
| `CHESS_TUTOR_COACHING_MODEL` / `CHESS_TUTOR_CHEAP_MODEL` | `claude-sonnet-5-5` / `claude-haiku-5-5` | Model tiers |
| `CHESS_TUTOR_THRESHOLDS__...` | see `settings.py` | Classification, phase, concept-state and rating-weight thresholds |

## Maia-3

Run `scripts/install_maia3.sh` once (needs `uv`). It installs `maia3-uci` as an isolated uv tool, pinned to a commit, with CPU-only torch (about 900 MB on disk). Model weights download from Hugging Face on first use and are cached.

For `CHESS_TUTOR_MAIA_DEVICE=cuda`, install a CUDA torch build: `MAIA3_TORCH_INDEX=https://download.pytorch.org/whl/cu128 scripts/install_maia3.sh` (use the index matching your driver), or `MAIA3_TORCH_INDEX=` (empty) for PyPI's default build.

Maia-3 is AGPL-3 licensed. It runs as a separate process that the backend talks to over UCI, and it is deliberately not a Python dependency of this project.

### Alternate opponents

`CHESS_TUTOR_OPPONENT_ENGINE` picks the opponent: `maia3` (default), `maia2` or `stockfish` (Stockfish with `UCI_LimitStrength`, Elo clamped to the engine's range, 1320-3190 for Stockfish 17). Above `CHESS_TUTOR_OPPONENT_STOCKFISH_ABOVE_ELO` (default 2000) Stockfish plays even when a Maia is selected. Maia-2 (MIT) is the licensing fallback: run `scripts/install_maia2.sh` once; it creates an isolated venv with CPU-only torch and the backend talks to it through `tools/maia2_move`. Maia-2 weights download from Google Drive on first use into `~/.cache/maia2`.

## Database

Player data lives in a SQLite file at `CHESS_TUTOR_DATABASE_PATH` (default `data/chess_tutor.db`, created on first run). Alembic migrations (`backend/migrations`) run automatically on backend startup, which also seeds the default player once.

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

The app is an installable PWA. Browsers only offer install on `localhost` or over HTTPS, so to install on a phone, serve it over HTTPS with `tailscale serve`. Plain `http://` on the LAN loads the app but won't offer install. Setup, install steps and troubleshooting: [docs/phone-access.md](docs/phone-access.md).

## Contributing

Install the git hooks once per clone (requires [pre-commit](https://pre-commit.com/), plus the backend and frontend dependencies above):

```bash
pre-commit install
pre-commit run --all-files   # ruff, black, mypy, eslint, tsc, gitleaks secrets scan, file hygiene (CI runs these too)
```

CI (`.github/workflows/ci.yml`) runs the same checks plus tests and the frontend build on every PR.
