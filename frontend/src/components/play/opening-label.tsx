import { moveLabel } from "@/lib/chess-board";
import type { BookExit, OpeningStep, PlayerColor } from "@/lib/game-api";

/** The opening so far as a quiet line, plus who left the book once the game has. */
export function OpeningLabel({
  step,
  bookExit,
  moves,
  playerColor,
}: {
  step: OpeningStep | undefined;
  bookExit: BookExit | null;
  moves: string[];
  playerColor: PlayerColor;
}) {
  const name = step?.eco
    ? [step.eco, step.opening, step.variation].filter(Boolean).join(" · ")
    : null;
  if (!name && !bookExit) return null;
  return (
    <div className="grid gap-0.5 text-xs text-muted-foreground">
      {name && (
        <p data-testid="opening-label" className="break-words font-medium">
          {name}
        </p>
      )}
      {bookExit && (
        <p>
          Left book at {moveLabel(bookExit.ply, moves[bookExit.ply - 1])} (
          {bookExit.side === playerColor ? "you" : "opponent"})
        </p>
      )}
    </div>
  );
}
