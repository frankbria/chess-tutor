"use client";

import { useEffect, useState } from "react";

import { describeTrend, type PlayerRating, type RatingHistoryEntry } from "@/lib/progress-api";

import { useProgressApi } from "./progress-api-context";
import { RatingChart } from "./rating-chart";

export function ProgressView() {
  const api = useProgressApi();
  const [data, setData] = useState<{ player: PlayerRating; history: RatingHistoryEntry[] } | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let current = true;
    Promise.all([api.getPlayer(), api.getRatingHistory()])
      .then(([player, history]) => current && setData({ player, history }))
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, [api]);

  if (failed) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load your progress.
      </p>
    );
  }
  if (data === null) return <p className="text-sm text-muted-foreground">Loading…</p>;

  const { player, history } = data;
  const rating = Math.round(player.rating);
  const trend = describeTrend(history, rating);

  return (
    <>
      <section aria-labelledby="rating-heading" className="grid gap-3">
        <h2 id="rating-heading" className="text-lg font-semibold">
          Rating
        </h2>
        <p className="text-3xl font-bold">
          {rating} <span className="text-base font-normal text-muted-foreground">± {Math.round(player.rating_deviation)}</span>
        </p>
        <p className="text-sm text-muted-foreground">
          {history.length} {history.length === 1 ? "game" : "games"} rated
        </p>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground">Finish a game and your rating chart will appear here.</p>
        ) : (
          <>
            <p className="text-sm">{trend}</p>
            <RatingChart history={history} description={trend} />
          </>
        )}
      </section>
      <section aria-labelledby="focus-heading" className="grid gap-2">
        <h2 id="focus-heading" className="text-lg font-semibold">
          Focus areas
        </h2>
        <p className="text-sm text-muted-foreground">Your focus areas will appear after a few games.</p>
      </section>
    </>
  );
}
