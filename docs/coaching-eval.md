# Coaching eval

31 curated positions (opening, middlegame, endgame) run through the real coach pipeline: Stockfish
builds the fact packet and move delta, the real model replies through `coach_reply_with_grounding`,
and each reply is scored. It covers every mode and event: passive comments, active hints at levels
1 to 3 before lock-in, replies to the player's stated thought, and free questions (passive, and
active before lock-in).

```bash
cd backend
uv run pytest -m eval --no-cov    # needs credentials (`ant auth login`); skipped without them
```

It is excluded from the default run (`uv run pytest`) and from CI. The end of the run prints the
pass rates, cost, time and every failing case with its reply; per-case results go to
`backend/eval-report.json` (gitignored).

## Files

- `backend/tests/eval/cases.json`: the cases. Each has a FEN, the move or proposal, the mode and
  event (plus `hint_level`, `player_message`, `locked_in`), `expect_any`, and `forbid`.
- `backend/tests/eval/eval_scoring.py`: context building and scoring (unit-tested in
  `backend/tests/eval/test_scoring.py`, part of the normal suite).
- `backend/tests/eval/test_coaching_eval.py`: the parametrized real-model suite.

## Scoring

A case passes only when all four dimensions hold:

1. **Grounded on the first attempt**: the first model reply passed the validator (no regeneration,
   no template fallback).
2. **Passes the validator**: the final reply passes `validate_reply`.
3. **Mentions an expected concept**: any entry of `expect_any` matches. An entry that is a fact
   type (`fork`, `back_rank_weakness`, ...) matches when the reply cites a fact of that type; any
   other entry is a case-insensitive regex on the reply text.
4. **No forbidden claim**, checked independently of the validator:
   - the best move before lock-in (unless it is the student's own proposal);
   - a square outside the facts' `allowed_references` (the student's own move squares are allowed);
   - a piece on a square that neither the position, the position after the move, nor a fact shows;
   - any square at hint level 1, more than one other square at level 2;
   - correction or data meta-talk ("good catch", "let me redo", "the engine", "the facts", ...);
   - per-case `forbid` patterns (for example "fork" when the fork fact is hidden by the best-move
     filter).

## Baseline

Run on 2026-11-23, `claude-sonnet-5-5` (coaching), Stockfish 1 pool, effort low.

| Measure | Result |
| --- | --- |
| Cases passing all dimensions | 29/31 (94%) |
| Grounded on first attempt | 29/31 (94%) |
| Passes validator | 31/31 (100%) |
| Mentions expected concept | 31/31 (100%) |
| No forbidden claim | 31/31 (100%) |
| Total cost | $0.088 |
| Time in model calls | 62 s (101 s wall clock including Stockfish) |

Both misses were first-attempt grounding failures that the regeneration then fixed: a reply to a
thought answer named f7 without a fact citing it, and a free question asked before lock-in.

Replies vary between runs. An earlier run, scored by a stricter draft of the forbidden-claim checks,
failed 6 cases; its extra failures were scorer bugs (counting the student's own move square) and a
real "the engine's top choice" meta-talk leak that did not recur. Treat a one-case swing as noise
and watch the trend across a few runs.
