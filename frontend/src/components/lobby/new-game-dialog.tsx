"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useSettingsApi } from "@/components/settings/settings-api-context";
import { ApiError, type ColorChoice, type GameMode } from "@/lib/game-api";

import { useLobbyApi } from "./lobby-api-context";
import {
  COLORS,
  defaultElo,
  ELO_STEP,
  FALLBACK_ELO,
  MAXIMUM_ELO,
  MINIMUM_ELO,
  MODES,
} from "./game-options";

const choiceClass =
  "flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-all has-[:checked]:border-primary has-[:checked]:bg-primary/10 has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50";

export function NewGameDialog() {
  const api = useLobbyApi();
  const settingsApi = useSettingsApi();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<GameMode>("regular");
  const [color, setColor] = useState<ColorChoice>("random");
  const [elo, setElo] = useState<number | null>(null);
  const [ratingLoaded, setRatingLoaded] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || ratingLoaded) return;
    let current = true;
    api
      .getPlayer()
      .then((player) => {
        if (!current) return;
        setRatingLoaded(true);
        setElo((previous) => previous ?? defaultElo(player.rating));
      })
      .catch(() => current && setElo((previous) => previous ?? FALLBACK_ELO));
    return () => {
      current = false;
    };
  }, [open, api, ratingLoaded]);

  useEffect(() => {
    let current = true;
    settingsApi
      .getSettings()
      .then((state) => current && setMode(state.preferences.default_mode))
      .catch(() => undefined); // keep the built-in default when settings are unavailable
    return () => {
      current = false;
    };
  }, [settingsApi]);

  function openDialog() {
    if (!ratingLoaded) setElo(null); // a fallback Elo from a failed load must not stick; reload it
    setOpen(true);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (elo === null) return;
    setSubmitting(true);
    setError(null);
    try {
      const game = await api.createGame({ mode, color, opponent_elo: elo });
      router.push(`/play/${game.id}`);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Could not start the game. Try again.");
      setSubmitting(false);
    }
  }

  return (
    <>
      <Button size="lg" className="w-full max-w-xs" onClick={openDialog}>
        Play
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto">
          <DialogTitle>New game</DialogTitle>
          <DialogDescription>Choose how you want to play.</DialogDescription>
          <form onSubmit={submit} className="grid gap-5">
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-sm font-medium">Mode</legend>
              {MODES.map((option) => (
                <label key={option.value} className={choiceClass}>
                  <input
                    type="radio"
                    name="mode"
                    value={option.value}
                    checked={mode === option.value}
                    onChange={() => setMode(option.value)}
                    className="mt-1 accent-primary"
                  />
                  <span className="grid">
                    <span className="font-medium">{option.label}</span>
                    <span className="text-sm text-muted-foreground">{option.description}</span>
                  </span>
                </label>
              ))}
            </fieldset>
            <fieldset className="grid grid-cols-3 gap-2">
              <legend className="mb-1 text-sm font-medium">Colour</legend>
              {COLORS.map((option) => (
                <label key={option.value} className={`${choiceClass} justify-center`}>
                  <input
                    type="radio"
                    name="color"
                    value={option.value}
                    checked={color === option.value}
                    onChange={() => setColor(option.value)}
                    className="sr-only"
                  />
                  {option.label}
                </label>
              ))}
            </fieldset>
            <div className="grid gap-2">
              <div className="flex justify-between text-sm font-medium">
                <label htmlFor="opponent-elo">Opponent Elo</label>
                <output htmlFor="opponent-elo" className="tabular-nums">
                  {elo ?? "…"}
                </output>
              </div>
              <input
                id="opponent-elo"
                type="range"
                min={MINIMUM_ELO}
                max={MAXIMUM_ELO}
                step={ELO_STEP}
                value={elo ?? FALLBACK_ELO}
                disabled={elo === null}
                onChange={(event) => setElo(Number(event.target.value))}
                className="accent-primary"
              />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" disabled={elo === null || submitting}>
              {submitting ? "Starting…" : "Start game"}
            </Button>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
