import { Chess } from "chess.js";

import type { GameState, PlayerColor } from "@/lib/game-api";

export type Destinations = Map<string, string[]>;

export function destinationsFrom(legalMoves: string[]): Destinations {
  const destinations: Destinations = new Map();
  for (const move of legalMoves) {
    const origin = move.slice(0, 2);
    destinations.set(origin, [
      ...(destinations.get(origin) ?? []),
      move.slice(2, 4),
    ]);
  }
  return destinations;
}

export function isPromotion(
  legalMoves: string[],
  from: string,
  to: string,
): boolean {
  return legalMoves.some(
    (move) => move.length === 5 && move.startsWith(`${from}${to}`),
  );
}

export function isInCheck(fen: string): boolean {
  return new Chess(fen).isCheck();
}

export function lastMoveSquares(moves: string[]): [string, string] | undefined {
  if (moves.length === 0) return undefined;
  const replay = new Chess();
  let last: { from: string; to: string } | undefined;
  for (const san of moves) last = replay.move(san);
  return last ? [last.from, last.to] : undefined;
}

export function movesInPairs(
  moves: string[],
): { number: number; white: string; black?: string }[] {
  const rows: { number: number; white: string; black?: string }[] = [];
  for (let index = 0; index < moves.length; index += 2) {
    rows.push({
      number: index / 2 + 1,
      white: moves[index],
      black: moves[index + 1],
    });
  }
  return rows;
}

export function describeResult(
  result: string,
  playerColor: PlayerColor,
): "won" | "lost" | "drawn" {
  if (result === "1/2-1/2") return "drawn";
  return (result === "1-0") === (playerColor === "white") ? "won" : "lost";
}

/** The position after the first `ply` moves, for stepping through a finished game. */
export function positionAfter(moves: string[], ply: number) {
  const replay = new Chess();
  let last: { from: string; to: string } | undefined;
  for (const san of moves.slice(0, ply)) last = replay.move(san);
  return {
    fen: replay.fen(),
    turnColor: (replay.turn() === "w" ? "white" : "black") as PlayerColor,
    inCheck: replay.isCheck(),
    lastMove: last ? ([last.from, last.to] as [string, string]) : undefined,
  };
}

/** "3. Bc4" for White's ply, "3... Nf6" for Black's. */
export const moveLabel = (ply: number, san: string) =>
  ply % 2 === 1
    ? `${Math.ceil(ply / 2)}. ${san}`
    : `${Math.ceil(ply / 2)}... ${san}`;

/** The game as it will stand once the server accepts `uci`, shown before it answers. */
export function gameAfterMove(game: GameState, uci: string): GameState | null {
  const replay = new Chess(game.fen);
  try {
    const played = replay.move({
      from: uci.slice(0, 2),
      to: uci.slice(2, 4),
      promotion: uci.slice(4) || undefined,
    });
    return {
      ...game,
      fen: replay.fen(),
      moves: [...game.moves, played.san],
      side_to_move: replay.turn() === "w" ? "white" : "black",
      legal_moves: [],
    };
  } catch {
    return null; // not legal on what is shown; the server's answer decides
  }
}
