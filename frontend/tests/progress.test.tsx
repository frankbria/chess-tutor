import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProgressApiProvider } from "@/components/progress/progress-api-context";
import { ProgressView } from "@/components/progress/progress-view";
import {
  describeTrend,
  type ConceptSummary,
  type ProgressApi,
  type RatingHistoryEntry,
} from "@/lib/progress-api";

function entry(
  before: number,
  after: number,
  performance: number | null = null,
): RatingHistoryEntry {
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

function concept(
  name: string,
  state: ConceptSummary["state"],
  count: number,
  recency: number,
  themes: string[] = [],
): ConceptSummary {
  return {
    concept: name,
    label: name.replace("_", " "),
    state,
    count,
    recency_score: recency,
    practice_themes: themes,
  };
}

function renderView(
  history: RatingHistoryEntry[],
  player = { rating: 812.4, rating_deviation: 74.6 },
  concepts: ConceptSummary[] = [],
) {
  const api: ProgressApi = {
    getPlayer: async () => player,
    getRatingHistory: async () => history,
    getConcepts: async () => concepts,
  };
  return render(
    <ProgressApiProvider api={api}>
      <ProgressView />
    </ProgressApiProvider>,
  );
}

describe("describeTrend", () => {
  it("reports the change over the last five games", () => {
    const history = [
      entry(700, 710),
      entry(710, 790),
      entry(790, 800),
      entry(800, 790),
      entry(790, 795),
      entry(795, 800),
      entry(800, 812),
    ];
    expect(describeTrend(history, 812)).toBe(
      "Rating 812, up 22 over the last 5 games",
    );
  });

  it("handles a drop, no change, one game and no games", () => {
    expect(describeTrend([entry(1200, 1180)], 1180)).toBe(
      "Rating 1180, down 20 over the last game",
    );
    expect(describeTrend([entry(1200, 1200)], 1200)).toBe(
      "Rating 1200, unchanged over the last game",
    );
    expect(describeTrend([], 1200)).toBe("Rating 1200");
  });
});

describe("ProgressView", () => {
  it("shows rating, deviation, game count, trend and the chart with null performance", async () => {
    renderView([entry(800, 805, null), entry(805, 812, 900)]);
    expect(await screen.findByText("812")).toBeInTheDocument();
    expect(screen.getByText("± 75")).toBeInTheDocument();
    expect(screen.getByText("2 games rated")).toBeInTheDocument();
    expect(
      screen.getByRole("img", {
        name: "Rating 812, up 12 over the last 2 games",
      }),
    ).toBeInTheDocument();
  });

  it("shows an empty state with no games", async () => {
    renderView([]);
    expect(
      await screen.findByText(/rating chart will appear here/),
    ).toBeInTheDocument();
    expect(screen.getByText("0 games rated")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("keeps the focus areas section as an empty state", async () => {
    renderView([entry(800, 805)]);
    expect(
      await screen.findByText(
        "Your focus areas will appear after a few games.",
      ),
    ).toBeInTheDocument();
  });

  it("reports a load failure", async () => {
    const api: ProgressApi = {
      getPlayer: async () => Promise.reject(new Error("down")),
      getRatingHistory: async () => [],
      getConcepts: async () => [],
    };
    render(
      <ProgressApiProvider api={api}>
        <ProgressView />
      </ProgressApiProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load your progress.",
    );
  });

  it("groups concepts by state with focus first and exact lichess practice links", async () => {
    renderView([entry(800, 805)], undefined, [
      // Themes as the backend taxonomy maps them (concepts/taxonomy.py).
      concept("hanging_piece", "focus", 4, 2.5, ["hangingPiece"]),
      concept("missed_fork", "recurring", 2, 0.5, ["fork"]),
      concept("back_rank", "improving", 3, 0.2, ["backRankMate"]),
      concept("pin_blindness", "new", 1, 1, ["pin"]),
    ]);
    await screen.findByRole("heading", { name: "Focus" });
    const headings = screen
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(headings).toEqual(["Focus", "Recurring", "Improving", "New"]);
    expect(screen.getByText("seen 4×, recently")).toBeInTheDocument();
    expect(screen.getByText("seen 2×")).toBeInTheDocument();
    const hanging = screen.getByRole("link", {
      name: "Practice Hanging Piece puzzles on Lichess (opens in a new tab)",
    });
    expect(hanging).toHaveAttribute(
      "href",
      "https://lichess.org/training/hangingPiece",
    );
    expect(hanging).toHaveAttribute("target", "_blank");
    expect(hanging).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getByRole("link", { name: /Fork puzzles/ })).toHaveAttribute(
      "href",
      "https://lichess.org/training/fork",
    );
    expect(screen.getAllByRole("link")).toHaveLength(2);
  });

  it("lists mastered concepts as encouragement", async () => {
    renderView([entry(800, 805)], undefined, [
      concept("skewer_blindness", "mastered", 5, 0.1, ["skewer"]),
    ]);
    expect(
      await screen.findByRole("heading", { name: "Mastered" }),
    ).toBeInTheDocument();
    expect(screen.getByText("skewer blindness")).toBeInTheDocument();
    expect(screen.getByText("seen 5×")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(
      screen.queryByText("Your focus areas will appear after a few games."),
    ).not.toBeInTheDocument();
  });

  it("reports a failure to load concepts", async () => {
    const api: ProgressApi = {
      getPlayer: async () => ({ rating: 800, rating_deviation: 70 }),
      getRatingHistory: async () => [],
      getConcepts: async () => Promise.reject(new Error("down")),
    };
    render(
      <ProgressApiProvider api={api}>
        <ProgressView />
      </ProgressApiProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load your progress.",
    );
  });
});
