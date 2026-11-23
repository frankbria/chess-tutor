# Chess Tutor — Design

Date: 2026-11-23 · Status: draft for approval

## 1. Intent

A personal, AI-assisted chess tutor that runs on a local web server (VPS later). One player — the owner — starting around 800 Elo (rusty; knows the rules and basic tactics).

Success is two things:

1. **The player keeps coming back.** The tutor must challenge, not bore. "Pushing" means raising the bar and recognising when the player is stuck on a concept — not nagging notifications, and never repeating the same advice for the same mistake.
2. **The player's rating rises over time**, visibly.

The product may later be sold or open-sourced, so licensing and a clean path to multi-user matter now, even though v1 is single-user.

The core idea: an open-source engine does the calculating; an AI coach reads the engine's verified facts and talks to the player — including asking *"what was your thought here?"* and responding to the player's reasoning, not just the move.

## 2. Goals, non-goals, anti-goals

**Goals (v1)**
- Three play modes: **Regular** (opponent at a chosen Elo), **Passive tutor** (flags blunders / great moves after they're played), **Active tutor** (comments on a proposed move before it is locked in, without revealing the best move).
- Coaching that differs by game phase (opening / middlegame / endgame).
- Two-way coaching: the coach asks for the player's reasoning at key moments; the player can ask questions at any time (typed).
- A persistent player model: estimated rating, recurring weaknesses, reasoning patterns, past-game memory.
- Escalating, fading guidance: explain → hint/question → name it as a focus + recommend practice; drop the topic once mastered.
- Practice recommendations by tactic/puzzle *theme* (links out; no built-in puzzles).
- Works well on desktop and phone (installable PWA).

**Non-goals (v1)**
- Accounts, sign-up, multi-tenant (phase 2).
- Voice input/output (phase 2).
- Proactive push notifications (phase 2; v1 has in-app "today's focus" only).
- A learn-the-rules tutorial (phase 3).
- Built-in puzzles.
- Online play against other humans.

**Anti-goals (a failure even if it "works")**
- The coach states chess facts the engine did not verify (hallucinated threats, illegal or invented lines).
- Active mode becomes a cheat — it tells the player the best move before lock-in.
- The coach repeats the same explanation for the same recurring mistake.
- Opponent plays like a broken engine (perfect moves interleaved with random blunders).
- Per-move latency or LLM cost makes it unpleasant to use.

## 3. Constraints

- **Single player, local first.** Runs on the owner's machine; reached from phone over LAN or Tailscale. VPS deployment must be a config change, not a rewrite. Shared VPS ⇒ port configurable, no hard-coded host/port.
- **Multi-user-ready data.** Every row keys to a `player_id`, even though there is one player. No auth in v1.
- **Licensing.**
  - Stockfish: GPL-3 (run as a separate process via UCI).
  - Maia-3: **AGPL-3** — fine for open source; a closed-source *hosted* product would have to publish source. Maia-2 (MIT) is the fallback. The opponent engine therefore sits behind a swappable adapter; the licensing decision is deferred to phase 2.
  - Opening names: lichess `chess-openings` dataset (CC0).
- **LLM is swappable.** Provider behind an interface; default Anthropic Claude. Cost and latency must be bounded (see §6.6).
- **No mocking in integration/E2E tests** (owner's standard): tests run real Stockfish/Maia; a small tagged suite hits the real LLM.

## 4. Approaches considered

| | Approach | Trade-off | Verdict |
|---|---|---|---|
| **A** | Python FastAPI backend (python-chess, Stockfish + Maia-3 as UCI subprocesses) + Next.js/shadcn PWA frontend, SQLite | Two languages/processes; matches owner's stack; best chess tooling; Maia-3 is native Python | **Chosen** |
| B | All-TypeScript, Stockfish WASM + chess.js in browser | Maia-3 needs model conversion (risky); deep analysis slow on phone; weaker chess-fact tooling | Rejected |
| C | Python backend + HTMX server-rendered UI | Fastest to start; poor fit for streaming two-way coach chat and touch board | Rejected |

## 5. Architecture

```
 Browser (Next.js PWA)                         FastAPI backend
 ┌───────────────────────┐   REST + SSE   ┌──────────────────────────────────────┐
 │ Board (chessground)   │ ─────────────▶ │ Game service (python-chess)          │
 │ Coach panel / chat    │ ◀── stream ─── │  ├─ Opponent engine adapter ─ Maia-3 │
 │ Dashboard / Review    │                │  ├─ Analysis engine ───────── Stockfish
 └───────────────────────┘                │  ├─ Position facts (deterministic)   │
                                          │  ├─ Move classifier                  │
                                          │  ├─ Coach (LLM, grounded)            │
                                          │  ├─ Player model (rating, weaknesses)│
                                          │  └─ SQLite                           │
                                          └──────────────────────────────────────┘
```

Monorepo layout: `backend/` (uv, FastAPI, pytest + pytest-bdd, ruff + black), `frontend/` (Next.js, shadcn/ui, Tailwind, Hugeicons), `docs/`.

### 5.1 Engines
- **Stockfish** — all analysis: evaluation, MultiPV candidate lines, verifying every concrete claim. Pool of long-lived UCI processes; analysis requests carry a depth/time budget.
- **Maia-3** (`maia3-uci`; supports low ratings — lichess runs Maia-3 bots at 600/800/1000 — so it covers the 800 start; 23M model on the GPU when available, 5M on CPU as fallback) — (a) plays the opponent at a target Elo; (b) predicts *what a human at the player's rating would likely play* — used to judge whether a missed resource was "findable" at this level.
- **Move probabilities** come from a small out-of-process policy helper (`tools/maia3_policy`, AGPL, run with the maia3 tool's interpreter), not from UCI: `maia3-uci` ranks MultiPV lines by policy but never prints the probabilities.
- **OpponentEngine adapter** — interface `choose_move(board, elo) -> Move`; implementations: Maia-3 (default), Maia-2, Stockfish-limited (above Maia's useful range). Selected by config.
- Opponent move delay: small humanised delay, longer when Maia's policy is spread across many moves (position is "hard").

### 5.2 Position facts layer (deterministic, no LLM)
Pure functions over `python-chess` boards + Stockfish output, producing a structured **fact packet**. This is the only source of chess truth the coach may use.

- Material balance; pieces hanging (losing material by static exchange: both sides capture cheapest-first, may stop, x-rays included).
- Tactical motifs: forks, pins, skewers, discovered attacks, back-rank weakness, mate threats, overloaded defenders, trapped pieces.
- **Threats:** what the opponent threatens right now (null-move probe + Stockfish best reply).
- King safety: castled?, pawn shield, open lines toward king.
- Pawn structure: isolated, doubled, backward, passed pawns.
- Development & centre (opening), piece activity and open files (middlegame), king activity / passed pawns / opposition (endgame).
- **Move delta:** for a played or proposed move — eval before/after, best line, what the move allows (opponent's best reply and why), which pre-existing threats it ignored.

Each detector is unit-tested with FEN fixtures.

### 5.3 Game phase detection
- **Opening:** within the opening book (lichess dataset match) or move ≤ ~12 with incomplete development.
- **Endgame:** queens off with limited non-pawn material, or total non-pawn material below threshold.
- **Middlegame:** otherwise.
Thresholds in config. Phase selects the coaching focus list:
- Opening: development, centre control, king safety/castling, not moving a piece twice, named opening + its typical plans.
- Middlegame: tactics (the motif detectors), plans, weak squares, piece activity, trades.
- Endgame: king activity, passed pawns, opposition, rook placement, converting material.

### 5.4 Move classification
Based on **win-probability loss** (lichess-style logistic on centipawns) so a 200cp drop in a won position isn't treated like one in an equal position.
- Blunder / Mistake / Inaccuracy / Good / Best.
- **Great (!)** — the only move (or one of very few) that keeps the advantage/holds when alternatives lose significantly.
- **Brilliant (!!)** — a best-or-near-best move that sacrifices material.
Thresholds in config. Per-game accuracy % is derived from the same numbers.

### 5.5 Mistake → concept tagging
Every Mistake/Blunder is tagged with one or more **concepts** from a fixed taxonomy (e.g. `hanging_piece`, `missed_fork`, `missed_opponent_threat`, `back_rank`, `pin_blindness`, `early_queen`, `uncastled_king`, `passive_rook_endgame`, `pawn_race`, …), derived from the fact packet's move delta. Taxonomy lives in one module; each concept maps to a phase and to one or more **practice themes** (lichess puzzle theme names, e.g. `fork`, `pin`, `backRankMate`, `rookEndgame`).

## 6. Tutoring behaviour

### 6.1 Modes
| Mode | Flow |
|---|---|
| Regular | Play normally. No live coaching. Post-game review available. |
| Passive tutor | After each player move, classify it. Comment only on notable moves (Inaccuracy or worse, Great, Brilliant). May ask "what was your thought?" on key moments (skippable). |
| Active tutor | Player makes a move → it is *proposed*, not committed. Coach comments on what might be unseen; player can **Lock in** or **Rethink** (unlimited rethinks). Coach never names the best move before lock-in. |

### 6.2 Active-mode hint ladder (the "unseen" detector)
On a proposed move, compute the move delta. If the move is fine and nothing notable is ignored → brief or silent ("Looks solid." / no comment). Otherwise escalate per rethink:
1. **Nudge:** direction only — "Before you commit, what can your opponent do after this?"
2. **Pointer:** a square/piece — "Look at what the knight on e5 can reach."
3. **Consequence:** the concrete verified outcome — "After this, …Nxe5 wins a piece."
Never: "play Bxf7". The best move is revealed only after lock-in (if asked) or in the post-game review.
Also comments on board features worth noticing even when the move is fine (phase focus list), rate-limited so it doesn't chatter.

### 6.3 "What was your thought here?"
The coach asks at key moments only:
- Active mode: on a proposed move that drops significantly, or a move whose purpose isn't clear.
- Passive mode: after a Mistake/Blunder or a Great/Brilliant move (skippable).
- Not more than once every few moves (configurable).

The player types a short answer. The coach responds to the reasoning using the fact packet: *"I see the idea — but look here, …"* / *"Not a bad idea, but you're missing this angle that wins more…"* / *"Exactly right."*
The player's stated reasoning is also classified (LLM, constrained to a fixed **reasoning-pattern** taxonomy, e.g. `attack_without_king_safety`, `ignored_opponent_threat`, `material_greed`, `correct_plan_wrong_execution`) and stored in the profile.

The player can also ask freely ("why is that bad?", "what's he threatening?"). In Active mode before lock-in, answers follow the hint ladder — no best-move reveal.

### 6.4 Grounding rule (anti-hallucination)
- The coach returns **structured output**: `{"text": "...", "fact_ids": [...]}`. Every fact in the packet has an id; the coach must cite the facts its text relies on. *(Builder-check call: SAN-only checking misses prose hallucinations like "the e5 knight is hanging".)*
- The LLM receives: fact packet (JSON), phase + focus list, mode, hint-ladder level, relevant player-profile summary, recent coach messages for this game, and the player's message if any.
- It may only reference moves/lines/squares present in the packet. The validator extracts every SAN move, square (`e5`) and piece+square reference from `text` and checks each appears in a *cited* fact; SAN moves must also be legal and in a verified line. Uncited `fact_ids` that don't exist fail validation. General strategic language without concrete squares/moves ("keep developing") is allowed. On failure: one regeneration with the error, then fall back to a deterministic template message.
- Before lock-in in Active mode, the best move is *withheld from the packet entirely* — the LLM cannot leak what it doesn't have.

### 6.5 Personalisation, escalation and fading
- **Weakness model:** per concept — occurrence count, exponentially decayed recency score, last-seen game, trend. States: `new → recurring → focus → improving → mastered`. Promotion/demotion thresholds in config.
- **Escalation:** `new` → explain fully. `recurring` → shorter, Socratic ("What does his knight attack after this?"), and references history ("4th game in a row"). `focus` → named as current focus on the dashboard + practice-theme recommendation.
- **Fading:** concepts in `improving` get less commentary; `mastered` concepts are not mentioned unless the mistake recurs (which demotes the concept).
- **No-repeat rule:** coach receives the last N phrasings used for a concept and must vary.
- **Memory:** short structured summaries of recent games (opening, result, key moments, concepts) are available to the coach ("You lost to this same pattern on Tuesday").

### 6.6 LLM cost and latency
- Coach is called only on notable events (not every move). Deterministic facts are computed first; LLM is the last step.
- Responses stream to the UI (SSE).
- Model tiers via config: a stronger model for coaching dialogue, a cheaper/faster one for classification (reasoning-pattern tagging, game summaries). Prompt caching for the static system prompt + taxonomy.
- Per-game token/cost counter stored and visible in settings.
- Targets: coach first token ≤ ~2 s after a proposed move (instant template line shown while the LLM streams); cost ≈ $0.10–0.25 per tutored game (~$3–8/month at a game a day). Budget alert in settings when a configurable monthly ceiling is approached.

## 7. Rating

- **Scale:** ratings are on the **lichess** scale (Maia is trained on lichess games). The starting 800 is a guess; early games calibrate it.
- **Glicko-2**, start 800, high initial RD so it converges within ~10–15 games.
- One rating pool across time controls (v1 games are mostly untimed).
- Opponent strength = Maia's configured Elo (treated as the opponent rating).
- Weighting: Regular games count fully; Passive tutor games at reduced weight; Active tutor games at lower weight (assistance was given). Weights in config.
- Supplementary **performance estimate** per game from accuracy (shown alongside, not mixed into Glicko) so progress is visible even in losses.
- Default opponent Elo: Regular = player's choice (defaulting to current rating); Tutor modes = current rating + a stretch offset (default +100).

## 8. Engagement (v1)

- **One-tap start** from the dashboard ("Play" uses last mode/settings).
- **Resume** an unfinished game.
- **Clocks:** games are **untimed by default**. Regular mode may optionally use a clock (preset controls, e.g. 10+5, 15+10); clocks are server-authoritative and flag on timeout. Tutor modes are always untimed (rethinks and coach chat would otherwise fight the clock).
- **Dashboard:** rating chart, current focus concept(s), last game recap, "today's focus" (a concept + practice-theme link), recent wins in concepts that moved to `improving`/`mastered`.
- **Post-game review:** key moments (biggest swings, Great/Brilliant moves, the player's own reasoning answers), coach summary, best moves revealed here.
- **Practice recommendations:** links to lichess puzzle themes for focus concepts.
- Takebacks: allowed in Passive tutor (counted; affects rating weight); not offered in Regular; unnecessary in Active (that's what Rethink is).

## 9. Data model (SQLite, SQLAlchemy + Alembic)

- `player(id, display_name, rating, rating_deviation, volatility, created_at)`
- `game(id, player_id, mode, player_color, opponent_engine, opponent_elo, time_control, status, result, pgn, opening_eco, opening_name, accuracy, started_at, ended_at, llm_cost)`
- `move(id, game_id, ply, san, uci, fen_before, eval_before, eval_after, classification, phase, proposed_attempts, took_back)`
- `coach_message(id, game_id, ply, role[coach|player], kind[comment|question|answer|hint], hint_level, text, concepts[], created_at)`
- `concept_event(id, player_id, game_id, ply, concept, source[move|reasoning])`
- `concept_state(player_id, concept, state, count, recency_score, last_seen_game_id, updated_at)`
- `rating_history(id, player_id, game_id, rating_before, rating_after, weight)`
- `game_summary(game_id, summary_json)`
- `settings(player_id, key, value)`

## 10. API (FastAPI)

- `POST /games` (mode, color, opponent_elo, time_control) · `GET /games/{id}` · `GET /games?status=`
- `POST /games/{id}/moves` — Regular/Passive: commit. Active: `{"propose": true}` returns analysis id; `POST /games/{id}/moves/lock` commits.
- `POST /games/{id}/resign` · `POST /games/{id}/takeback`
- `GET /games/{id}/coach/stream` (SSE) — coach comments, questions, opponent moves.
- `POST /games/{id}/coach/messages` — player's answer or question.
- `GET /games/{id}/review` · `GET /games/{id}/pgn`
- `GET /player` · `GET /player/profile` (concepts, focus, recommendations) · `GET /player/rating-history`
- `GET/PUT /settings`
- `GET /health` (engines + LLM reachable)

## 11. UI

Screens: **Dashboard**, **Play**, **Review**, **Progress**, **Settings**. Modern, colourful (not grey, not template-looking), shadcn/ui + Tailwind, Hugeicons. Board: chessground.

Play — desktop:
```
┌──────────────────────────────┬─────────────────────────┐
│                              │ Coach  · Middlegame     │
│          BOARD               │ "Before you commit —    │
│                              │  what can Black do      │
│                              │  after this?"           │
│                              │ [ type your thought… ]  │
│  [Rethink]   [Lock in ✓]     │ Moves: 1.e4 e5 2.Nf3 …  │
└──────────────────────────────┴─────────────────────────┘
```
Play — phone: board full width on top; coach panel as a bottom sheet that expands for chat; Lock in / Rethink as large thumb buttons.

## 12. Deployment

- Local: one command (`make dev` or `docker compose up`) starts backend + frontend. Stockfish via system package or bundled binary; Maia-3 installed into the backend venv; model weights downloaded on first run.
- Ports configurable via env; check for conflicts on the shared VPS.
- Phone access via LAN or Tailscale. VPS later: Docker Compose behind a reverse proxy; CORS origins from env.
- Secrets (LLM API key) via env / `.env` (gitignored).

## 13. Testing & quality

- TDD; backend pytest + pytest-bdd; >85% coverage; ruff + black; pre-commit hooks.
- Fact detectors: FEN-fixture unit tests (known forks, pins, back-rank, etc.).
- Engines: integration tests against real Stockfish/Maia-3 processes.
- Coach: (1) grounding validator unit-tested with crafted outputs; (2) a **coaching eval set** — ~30 curated positions with expected concepts to mention and forbidden claims — run against the real LLM in a tagged suite.
- Frontend: Playwright E2E for core flows (start game, propose/rethink/lock, answer coach question, finish game, review).
- CI: GitHub Actions running lint, type check, unit + integration (engines installed in CI). Real-LLM suite manual/nightly.

## 14. Phases

- **Phase 1 (this spec):** everything above.
- **Phase 2:** accounts/multi-user, voice on/off, push notifications/nudges, VPS hosting hardening, licensing decision (Maia-3 vs Maia-2).
- **Phase 3:** rules tutorial for beginners.
- **Stretch (v1 if time allows):** opponent steering — Maia samples among its top human-likely moves, biased toward lines that test the player's focus concept.

## 15. Left to the builder

- Exact thresholds (classification, phase, concept-state transitions, rating weights) — start from lichess conventions, keep in config.
- Prompt wording and template fallbacks.
- Visual design details (palette, typography) within the "modern, colourful" brief.
- Stockfish depth/time budgets per call type, tuned for responsiveness.
- Choice of Maia-3 model size by hardware. Reference machine: 8 cores, 15 GB RAM, RTX 4070 laptop GPU (8 GB), WSL2 Linux. Native `make dev` is the primary path; Docker Compose is for the VPS.
