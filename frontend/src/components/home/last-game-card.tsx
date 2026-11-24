"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useLobbyApi } from "@/components/lobby/lobby-api-context";
import { COLORS, labelFor, MODES } from "@/components/lobby/game-options";
import { Button } from "@/components/ui/button";
import type { LastGame } from "@/lib/game-api";

/** The game result from the player's side. */
function outcome(game: LastGame): string {
  if (game.result === null) return "Game over";
  if (game.result === "1/2-1/2") return "Draw";
  const whiteWon = game.result === "1-0";
  return whiteWon === (game.player_color === "white") ? "Win" : "Loss";
}

export function LastGameCard() {
  const api = useLobbyApi();
  const [game, setGame] = useState<LastGame | null>(null);

  useEffect(() => {
    let current = true;
    api
      .getLastGame()
      .then((loaded) => current && setGame(loaded))
      .catch(() => undefined); // the recap is optional; the Play button still works
    return () => {
      current = false;
    };
  }, [api]);

  if (game === null) return null;
  return (
    <section aria-labelledby="last-game-heading" data-slot="last-game" className="grid w-full max-w-sm gap-2 rounded-lg border p-4 text-left">
      <h2 id="last-game-heading" className="text-lg font-semibold">
        Last game
      </h2>
      <p className="flex items-baseline justify-between gap-2">
        <span className="text-xl font-bold">{outcome(game)}</span>
        <span className="text-sm text-muted-foreground">
          {labelFor(MODES, game.mode)}, {labelFor(COLORS, game.player_color)}, Elo {game.opponent_elo}
        </span>
      </p>
      {game.accuracy !== null && <p className="text-sm">Accuracy {Math.round(game.accuracy)}%</p>}
      {game.summary && <p className="text-sm text-muted-foreground">{game.summary}</p>}
      <Button asChild size="sm" variant="outline" className="justify-self-start">
        <Link href={`/review/${game.game_id}`}>Review game</Link>
      </Button>
    </section>
  );
}
