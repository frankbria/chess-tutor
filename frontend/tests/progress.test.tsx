import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProgressApiProvider } from "@/components/progress/progress-api-context";
import { ProgressView } from "@/components/progress/progress-view";
import { describeTrend, type ProgressApi, type RatingHistoryEntry } from "@/lib/progress-api";

function entry(before: number, after: number, performance: number | null = null): RatingHistoryEntry {
  return {
    game_id: after,
    rating_before: before,
    rating_after: after,
    deviation_before: 80,
    deviation_after: 75,
    weight: 1,
    created_at: "2026-11-23T00:00:00Z",
    accuracy: null,
    performance_rating: performance,
  };
}

function renderView(history: RatingHistoryEntry[], player = { rating: 812.4, rating_deviation: 74.6 }) {
  const api: ProgressApi = { getPlayer: async () => player, getRatingHistory: async () => history };
  return render(
    <ProgressApiProvider api={api}>
      <ProgressView />
    </ProgressApiProvider>,
  );
}

describe("describeTrend", () => {
  it("reports the change over the last five games", () => {
    const history = [entry(700, 710), entry(710, 790), entry(790, 800), entry(800, 790), entry(790, 795), entry(795, 800), entry(800, 812)];
    expect(describeTrend(history, 812)).toBe("Rating 812, up 22 over the last 5 games");
  });

  it("handles a drop, no change, one game and no games", () => {
    expect(describeTrend([entry(1200, 1180)], 1180)).toBe("Rating 1180, down 20 over the last game");
    expect(describeTrend([entry(1200, 1200)], 1200)).toBe("Rating 1200, unchanged over the last game");
    expect(describeTrend([], 1200)).toBe("Rating 1200");
  });
});

describe("ProgressView", () => {
  it("shows rating, deviation, game count, trend and the chart with null performance", async () => {
    renderView([entry(800, 805, null), entry(805, 812, 900)]);
    expect(await screen.findByText("812")).toBeInTheDocument();
    expect(screen.getByText("± 75")).toBeInTheDocument();
    expect(screen.getByText("2 games rated")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Rating 812, up 12 over the last 2 games" })).toBeInTheDocument();
  });

  it("shows an empty state with no games", async () => {
    renderView([]);
    expect(await screen.findByText(/rating chart will appear here/)).toBeInTheDocument();
    expect(screen.getByText("0 games rated")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("keeps the focus areas section as an empty state", async () => {
    renderView([entry(800, 805)]);
    expect(await screen.findByText("Your focus areas will appear after a few games.")).toBeInTheDocument();
  });

  it("reports a load failure", async () => {
    const api: ProgressApi = { getPlayer: async () => Promise.reject(new Error("down")), getRatingHistory: async () => [] };
    render(
      <ProgressApiProvider api={api}>
        <ProgressView />
      </ProgressApiProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your progress.");
  });
});
