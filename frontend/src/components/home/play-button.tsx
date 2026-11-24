"use client";

import { PlayIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { useLobbyApi } from "@/components/lobby/lobby-api-context";
import { useSettingsApi } from "@/components/settings/settings-api-context";
import { Button } from "@/components/ui/button";
import { ApiError, type NewGame } from "@/lib/game-api";

/** One tap: starts a game with the last choice, else the saved default mode, else Regular vs random colour. */
export function PlayButton() {
  const api = useLobbyApi();
  const settingsApi = useSettingsApi();
  const router = useRouter();
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function play() {
    setStarting(true);
    setError(null);
    try {
      const [last, settings] = await Promise.all([
        api.getLastChoice().catch(() => null),
        settingsApi.getSettings().catch(() => null),
      ]);
      const newGame: NewGame = {
        mode: last?.mode ?? settings?.preferences.default_mode ?? "regular",
        color: last?.color ?? "random",
      };
      if (last?.opponent_elo != null) newGame.opponent_elo = last.opponent_elo;
      const game = await api.createGame(newGame);
      router.push(`/play/${game.id}`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not start the game. Try again.");
      setStarting(false);
    }
  }

  return (
    <div className="grid w-full max-w-xs justify-items-center gap-2">
      <Button size="lg" className="h-14 w-full text-lg" onClick={play} disabled={starting}>
        <HugeiconsIcon icon={PlayIcon} size={24} aria-hidden="true" />
        {starting ? "Starting…" : "Play"}
      </Button>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
