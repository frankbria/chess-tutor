# Chess Tutor

An AI-assisted chess tutor. Play a human-like engine (Maia-3) at your level while a coach — grounded in Stockfish analysis — flags blunders and great moves, asks what you were thinking, and points out what you might not have seen before you lock in a move. It learns your recurring weaknesses and adapts.

**Modes:** Regular play · Passive tutor · Active tutor

**Status:** design phase. See the [design spec](docs/specs/2026-11-23-chess-tutor-design.md) and the [issue tracker](https://github.com/frankbria/chess-tutor/issues) for the build plan.

**Stack:** FastAPI + python-chess + Stockfish + Maia-3 · Next.js PWA (shadcn/ui, chessground) · SQLite
