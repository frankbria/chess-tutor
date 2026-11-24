import { expect, test, type Page } from "@playwright/test";

import {
  anyMove,
  blunderMove,
  dragPiece,
  playMove,
  snapshot,
  startFromOptions,
  waitForPlayerTurn,
} from "./helpers";

/** Plays a move and waits for the opponent's reply to land in the move list. */
async function playAndAwaitReply(page: Page, choose: Parameters<typeof playMove>[1]) {
  const before = await playMove(page, choose);
  await expect
    .poll(async () => (await snapshot(page)).moves.length, { timeout: 60_000 })
    .toBeGreaterThanOrEqual(before.moves.length + 2);
}

const question = (page: Page) => page.locator('li[data-kind="question"]');

/** Blunders until the coach asks its question (a mistake or blunder is a key moment). */
async function blunderUntilQuestion(page: Page, alreadyAsked: number) {
  for (let attempt = 0; attempt < 12; attempt++) {
    if ((await question(page).count()) > alreadyAsked) return;
    await playAndAwaitReply(page, blunderMove);
    await page.waitForTimeout(1000); // the coach scores the move in the background
  }
  await expect(question(page)).toHaveCount(alreadyAsked + 1);
}

test("one tap on Play starts a game in the default mode", async ({ page }) => {
  const settings = await (await page.request.get(`${process.env.E2E_API_URL}/settings`)).json();
  await page.goto("/");
  await page.getByRole("button", { name: "Play", exact: true }).click();
  await page.waitForURL(/\/play\/\d+/);
  await expect(page.getByTestId("chessboard")).toBeVisible();
  expect((await snapshot(page)).mode).toBe(settings.preferences.default_mode);
});

test("Regular: the opponent answers each move", async ({ page }) => {
  await startFromOptions(page, "regular");
  await playAndAwaitReply(page, anyMove);
  await playAndAwaitReply(page, anyMove);
  await expect(page.getByRole("list", { name: "Moves" }).getByRole("listitem")).toHaveCount(2);
});

test("Passive: a blunder prompts the coach question, which can be answered and skipped", async ({ page }) => {
  await startFromOptions(page, "passive");

  await blunderUntilQuestion(page, 0);
  await expect(question(page)).toContainText(/What were you thinking/);
  await page.getByLabel("Message the coach").fill("I wanted to develop quickly.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator('li[data-kind="answer"]').first()).toContainText("develop quickly");

  await blunderUntilQuestion(page, 1);
  await page.getByRole("button", { name: "Skip" }).click();
  await expect(page.locator('li[data-kind="skipped"]')).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Skip" })).toHaveCount(0);
});

test("Active: propose, Rethink restores the position, Lock in commits", async ({ page }) => {
  await startFromOptions(page, "active");
  const start = await waitForPlayerTurn(page);
  const proposed = anyMove(start);
  const arrow = page.locator("svg.cg-shapes line");

  await dragPiece(page, proposed, start.player_color);
  const proposal = page.getByRole("region", { name: "Proposed move" });
  await expect(proposal).toBeVisible();
  await expect(arrow).toHaveCount(1);
  expect((await snapshot(page)).moves).toHaveLength(0);

  await page.getByRole("button", { name: "Rethink" }).click();
  await expect(proposal).toHaveCount(0);
  await expect(arrow).toHaveCount(0);
  expect((await snapshot(page)).fen).toBe(start.fen);

  await dragPiece(page, proposed, start.player_color);
  await expect(proposal).toBeVisible();
  await page.getByRole("button", { name: "Lock in" }).click();
  await expect(proposal).toHaveCount(0);
  await expect.poll(async () => (await snapshot(page)).moves.length, { timeout: 60_000 }).toBeGreaterThanOrEqual(2);
});

test("Finish a game by resigning, then review it", async ({ page }) => {
  await startFromOptions(page, "regular");
  for (let move = 0; move < 5; move++) await playAndAwaitReply(page, blunderMove);

  await page.getByRole("button", { name: "Resign" }).click();
  await page.getByRole("button", { name: "Confirm resign" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("You lost");
  await dialog.getByRole("link", { name: "Review game" }).click();
  await page.waitForURL(/\/review\/\d+/);

  const position = page.locator("p[aria-live=polite]");
  const last = await position.textContent();
  await page.keyboard.press("ArrowLeft");
  await expect(position).not.toHaveText(last ?? "");
  await page.keyboard.press("Home");
  await expect(position).toHaveText("Start position");
  await page.keyboard.press("ArrowRight");
  await expect(position).toHaveText(/^1\. \S+$/);
  await page.keyboard.press("End");
  await expect(page.getByRole("button", { name: "Next move" })).toBeDisabled();

  const moments = page.getByRole("list", { name: "Key moments" }).getByRole("button");
  await expect(moments.first()).toBeVisible();
  await moments.first().click();
  await expect(moments.first()).toHaveAttribute("aria-current", "true");
  await expect(page.getByText(/^Best was /)).toBeVisible();
});
