import { Chess, type Color, type Square } from "chess.js";
import { expect, type Locator, type Page } from "@playwright/test";

const apiUrl = process.env.E2E_API_URL;

interface GameSnapshot {
  id: number;
  mode: string;
  player_color: "white" | "black";
  status: "active" | "finished";
  fen: string;
  moves: string[];
  side_to_move: "white" | "black";
  legal_moves: string[];
}

export const gameIdOf = (page: Page) => Number(/\/(?:play|review)\/(\d+)/.exec(page.url())?.[1]);

export async function snapshot(page: Page, gameId = gameIdOf(page)): Promise<GameSnapshot> {
  const response = await page.request.get(`${apiUrl}/games/${gameId}`);
  expect(response.ok()).toBe(true);
  return response.json();
}

type Mode = "regular" | "passive" | "active";
const MODE_LABEL: Record<Mode, string> = { regular: "Regular", passive: "Passive tutor", active: "Active tutor" };

/** Dashboard Options: pick the mode, play White (so the player moves first), start. */
export async function startFromOptions(page: Page, mode: Mode) {
  await page.goto("/");
  await page.getByRole("button", { name: "Options" }).click();
  const dialog = page.getByRole("dialog", { name: "New game" });
  await dialog.getByLabel(MODE_LABEL[mode]).check();
  await dialog.getByText("White", { exact: true }).click();
  await dialog.getByRole("button", { name: "Start game" }).click();
  await page.waitForURL(/\/play\/\d+/);
  await expect(page.getByTestId("chessboard")).toBeVisible();
  expect((await snapshot(page)).mode).toBe(mode);
}

/** Waits until it is the player's turn (the opponent may still be thinking, or opening as White). */
export async function waitForPlayerTurn(page: Page) {
  await expect
    .poll(
      async () => {
        const game = await snapshot(page);
        if (game.status === "finished") throw new Error("the game ended before the player's turn");
        return game.side_to_move === game.player_color;
      },
      { timeout: 60_000 },
    )
    .toBe(true);
  return snapshot(page);
}

const board = (page: Page): Locator => page.locator("cg-board");

/** Chessground only reacts to a real pointer sequence: down, move, up. */
export async function dragPiece(page: Page, uci: string, orientation: "white" | "black") {
  const surface = board(page);
  await surface.scrollIntoViewIfNeeded();
  const box = await surface.boundingBox();
  if (!box) throw new Error("the board has no layout");
  const size = box.width / 8;
  const centre = (square: string) => {
    const file = square.charCodeAt(0) - "a".charCodeAt(0);
    const rank = Number(square[1]);
    const column = orientation === "white" ? file : 7 - file;
    const row = orientation === "white" ? 8 - rank : rank - 1;
    return { x: box.x + (column + 0.5) * size, y: box.y + (row + 0.5) * size };
  };
  const from = centre(uci.slice(0, 2));
  const to = centre(uci.slice(2, 4));
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move((from.x + to.x) / 2, (from.y + to.y) / 2, { steps: 4 });
  await page.mouse.move(to.x, to.y, { steps: 4 });
  await page.mouse.up();
}

/** Any legal move that is not a promotion; the start position always has one. */
export function anyMove(game: GameSnapshot): string {
  const move = game.legal_moves.find((uci) => uci.length === 4);
  if (!move) throw new Error("no legal move to play");
  return move;
}

const VALUE: Record<string, number> = { p: 1, n: 3, b: 3, r: 5, q: 9, k: 0 };

/** A move that hangs the most valuable piece it can; failing that, a weakening f/g pawn or king move. */
export function blunderMove(game: GameSnapshot): string {
  const chess = new Chess(game.fen);
  const own = chess.turn();
  const opponent: Color = own === "w" ? "b" : "w";
  let best: { uci: string; score: number } | undefined;
  for (const move of chess.moves({ verbose: true })) {
    if (move.promotion) continue;
    chess.move(move);
    const allowsMate = chess.moves().some((reply) => reply.endsWith("#"));
    // Hanging: the opponent attacks the arrival square and nothing of ours defends it.
    const hanging =
      !move.captured && chess.isAttacked(move.to as Square, opponent) && !chess.isAttacked(move.to as Square, own)
        ? 10 + VALUE[move.piece]
        : 0;
    chess.undo();
    if (allowsMate) continue; // keep the game alive for the flows that follow
    const weakening = move.piece === "p" && "fg".includes(move.from[0]) ? 2 : move.piece === "k" ? 1 : 0;
    const score = hanging + weakening;
    if (!best || score > best.score) best = { uci: move.from + move.to, score };
  }
  if (!best) throw new Error("no legal move to play");
  return best.uci;
}

/** Plays one move chosen by `choose` and waits for the board to show it. */
export async function playMove(page: Page, choose: (game: GameSnapshot) => string) {
  const game = await waitForPlayerTurn(page);
  await dragPiece(page, choose(game), game.player_color);
  return game;
}
