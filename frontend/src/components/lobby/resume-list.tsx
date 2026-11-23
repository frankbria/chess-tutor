"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import type { GameState } from "@/lib/game-api";

import { useLobbyApi } from "./lobby-api-context";
import { COLORS, labelFor, MODES } from "./game-options";

export function ResumeList() {
  const api = useLobbyApi();
  const [games, setGames] = useState<GameState[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let current = true;
    api
      .listActiveGames()
      .then((loaded) => current && setGames(loaded))
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, [api]);

  return (
    <section aria-labelledby="resume-heading" className="grid w-full max-w-sm gap-3 text-left">
      <h2 id="resume-heading" className="text-lg font-semibold">
        Unfinished games
      </h2>
      {failed ? (
        <p role="alert" className="text-sm text-destructive">
          Could not load your games.
        </p>
      ) : games === null ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : games.length === 0 ? (
        <p className="text-sm text-muted-foreground">No unfinished games. Start a new one above.</p>
      ) : (
        <ul className="grid gap-2">
          {games.map((game) => (
            <li key={game.id} className="flex items-center justify-between gap-3 rounded-lg border p-3">
              <span className="grid text-sm">
                <span className="font-medium">
                  {labelFor(MODES, game.mode)}, {labelFor(COLORS, game.player_color)}
                </span>
                <span className="text-muted-foreground">
                  Elo {game.opponent_elo}, {game.moves.length} {game.moves.length === 1 ? "move" : "moves"}
                </span>
              </span>
              <Button asChild size="sm" variant="outline">
                <Link href={`/play/${game.id}`} aria-label={`Resume game ${game.id}`}>
                  Resume
                </Link>
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
