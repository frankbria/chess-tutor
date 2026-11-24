# Opponent steering: self-play calibration (issue #96)

Question: does steering (`opponent_steering_rate = 0.25`, off by default) change how strong the
opponent is, measured by game results rather than the centipawn-loss proxy from #58?

## Method

- `scripts/steering_selfplay.py` (not in CI): steered Maia-3 against plain Maia-3 at the same Elo,
  colours alternating every game, sampled move by move (no fixed seed shared between sides).
- Steering settings are the shipped defaults: rate 0.25, at most 30 cp eval loss, top 4 moves,
  probability floor 0.05; Stockfish depth 10 for the loss check.
- Fixed focus set: `HANGING_PIECE` and `MISSED_FORK`, the two steerable concepts a beginner is
  most often given as FOCUS.
- Games run to the end, or 150 plies; capped games are adjudicated by Stockfish depth 16 (white
  eval at least +150 cp is a win, at most -150 cp a loss, otherwise a draw). Draws are claimed by
  threefold repetition and the fifty-move rule.
- Score is the steered side's; Elo difference is `-400 log10(1/score - 1)`; the 95% CI uses the
  per-game score variance (normal approximation) mapped through the same formula
  (`chess_tutor.engines.calibration`).
- One worker (Maia-3, its policy helper and one Stockfish), about 3 s per game. 500 games per Elo.

## Results (rate 0.25)

| Maia Elo | Games | W / D / L (steered) | Score | Elo difference | 95% CI | Adjudicated | Steered as white / black |
|---|---|---|---|---|---|---|---|
| 1200 | 500 | 275 / 58 / 167 | 0.608 | +76 | [+47, +106] | 28 | 0.622 / 0.594 |
| 1600 | 500 | 229 / 75 / 196 | 0.533 | +23 | [-5, +51] | 43 | 0.552 / 0.514 |

Total self-play time about 47 minutes (25 + 22).

## Conclusion

Steering does not weaken the opponent; it makes it slightly stronger, because steered moves are
chosen only among Maia's top human-likely moves within 30 cp of its first choice, so on steered
moves the tail blunders Maia would otherwise sample are replaced. The effect is +23 Elo at 1600
(CI includes 0) and +76 Elo at 1200 (CI [+47, +106], the upper end marginally past +100 and the
point estimate inside). Neither CI lies wholly outside +-100, so the rate is not tuned: the
default stays 0.25. At 1200 the opponent plays about one 75-Elo notch stronger than its nominal
level when steering is on; a lower rate would shrink this roughly in proportion if that ever
matters, but it was not needed to meet the +-100 bound. The tight intervals (about +-30 Elo)
come from 500 games per Elo.
