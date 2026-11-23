import { movesInPairs } from "@/lib/chess-board";

export function MoveList({ moves }: { moves: string[] }) {
  if (moves.length === 0) return <p className="text-sm text-muted-foreground">No moves yet.</p>;
  return (
    <ol aria-label="Moves" className="grid max-h-64 grid-cols-[auto_1fr_1fr] gap-x-3 gap-y-1 overflow-y-auto font-mono text-sm">
      {movesInPairs(moves).map((row) => (
        <li key={row.number} className="contents">
          <span className="text-muted-foreground">{row.number}.</span>
          <span className="font-semibold">{row.white}</span>
          <span className="font-semibold">{row.black}</span>
        </li>
      ))}
    </ol>
  );
}
