import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReviewView } from "@/components/review/review-view";
import { positionAfter } from "@/lib/chess-board";
import { ApiError, type CoachMessage, type GameState } from "@/lib/game-api";
import type { Review, ReviewApi } from "@/lib/review-api";

// chessground needs real layout; this stand-in exposes the position the board would show.
vi.mock("@/components/play/chessboard", () => ({
  Chessboard: (props: { fen: string; orientation: string; destinations?: unknown }) => (
    <div data-testid="board" data-fen={props.fen} data-orientation={props.orientation} data-locked={String(!props.destinations)} />
  ),
}));
// recharts needs real layout too.
vi.mock("@/components/review/eval-graph", () => ({
  EvalGraph: (props: { ply: number; points: unknown[] }) => (
    <div data-testid="graph" data-ply={props.ply} data-points={props.points.length} />
  ),
}));

const MOVES = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nd4"];

const game: GameState = {
  id: 3,
  mode: "passive",
  opponent_elo: 1200,
  player_color: "white",
  status: "finished",
  result: "0-1",
  fen: "",
  moves: MOVES,
  side_to_move: "white",
  legal_moves: [],
  time_control: null,
  clock: null,
  ended_on_time: false,
};

const review: Review = {
  accuracy: 71.2,
  performance_rating: 1043,
  win_probabilities: [
    { ply: 1, win_probability: 50 },
    { ply: 3, win_probability: 55 },
    { ply: 5, win_probability: 20 },
  ],
  key_moments: [
    {
      ply: 5,
      move_number: 3,
      san: "Bc4",
      uci: "f1c4",
      fen_before: "",
      classification: "blunder",
      win_probability_before: 52.48,
      win_probability_after: 13.65,
      best_move_uci: "d2d4",
      best_move_san: "d4",
    },
  ],
  reasoning: [],
  summary: { summary: "You lost a tempo in the opening.", key_moments: [], concepts: [], source: "template" },
  concepts: [],
};

const notes: CoachMessage[] = [
  { ply: 5, role: "coach", kind: "comment", text: "That bishop move walked into a fork.", classification: "blunder", phase: "opening" },
];

function renderReview(overrides: Partial<ReviewApi> = {}) {
  const api: ReviewApi = {
    getGame: async () => game,
    getCoachMessages: async () => notes,
    getReview: async () => review,
    ...overrides,
  };
  render(<ReviewView gameId={3} api={api} />);
}

const plyOf = () => screen.getByTestId("graph").getAttribute("data-ply");
const fenOf = () => screen.getByTestId("board").getAttribute("data-fen");

describe("positionAfter", () => {
  it("replays the first ply moves and marks the last one", () => {
    const position = positionAfter(MOVES, 2);
    expect(position.lastMove).toEqual(["e7", "e5"]);
    expect(position.turnColor).toBe("white");
    expect(positionAfter(MOVES, 0).lastMove).toBeUndefined();
  });
});

describe("ReviewView", () => {
  it("opens on the final position, read-only, from the player's side", async () => {
    renderReview();
    await screen.findByTestId("board");
    expect(plyOf()).toBe("6");
    expect(fenOf()).toBe(positionAfter(MOVES, 6).fen);
    expect(screen.getByTestId("board")).toHaveAttribute("data-locked", "true");
    expect(screen.getByTestId("board")).toHaveAttribute("data-orientation", "white");
  });

  it("steps with the arrow keys and jumps with Home and End", async () => {
    renderReview();
    await screen.findByTestId("board");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(plyOf()).toBe("5");
    fireEvent.keyDown(window, { key: "Home" });
    expect(plyOf()).toBe("0");
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(plyOf()).toBe("0");
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(plyOf()).toBe("1");
    fireEvent.keyDown(window, { key: "End" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(plyOf()).toBe("6");
  });

  it("steps back on a swipe right and forward on a swipe left, ignoring short drags", async () => {
    renderReview();
    const board = (await screen.findByTestId("board")).parentElement as HTMLElement;
    const swipe = (from: number, to: number) => {
      fireEvent.pointerDown(board, { clientX: from, clientY: 0 });
      fireEvent.pointerUp(board, { clientX: to, clientY: 0 });
    };
    swipe(100, 220);
    expect(plyOf()).toBe("5");
    swipe(100, 130);
    expect(plyOf()).toBe("5");
    swipe(220, 100);
    expect(plyOf()).toBe("6");
  });

  it("jumps to a key moment and reveals the best move and the coach's note there", async () => {
    renderReview();
    await screen.findByTestId("board");
    expect(screen.queryByText(/Best was/)).not.toBeInTheDocument();
    fireEvent.click(within(screen.getByRole("list", { name: "Key moments" })).getByRole("button", { name: /Bc4/ }));
    expect(plyOf()).toBe("5");
    expect(fenOf()).toBe(positionAfter(MOVES, 5).fen);
    expect(screen.getByText(/Best was d4/)).toBeInTheDocument();
    expect(screen.getByText(/from 52% to 14%/)).toBeInTheDocument();
    expect(screen.getByText("That bishop move walked into a fork.")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Blunder");
  });

  it("shows the summary and accuracy", async () => {
    renderReview();
    expect(await screen.findByText("You lost a tempo in the opening.")).toBeInTheDocument();
    expect(screen.getByText(/71%/)).toBeInTheDocument();
    expect(screen.getByTestId("graph")).toHaveAttribute("data-points", "3");
  });

  it("keeps refetching with backoff until the post-game summary arrives, then stops", async () => {
    vi.useFakeTimers();
    try {
      const getReview = vi
        .fn<ReviewApi["getReview"]>()
        .mockResolvedValueOnce({ ...review, summary: null })
        .mockResolvedValueOnce({ ...review, summary: null })
        .mockResolvedValue(review);
      renderReview({ getReview });
      await act(() => vi.advanceTimersByTimeAsync(0));
      expect(getReview).toHaveBeenCalledTimes(1);
      expect(screen.queryByText("You lost a tempo in the opening.")).not.toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync(2000));
      expect(getReview).toHaveBeenCalledTimes(2);
      await act(() => vi.advanceTimersByTimeAsync(4000));
      expect(getReview).toHaveBeenCalledTimes(3);
      expect(screen.getByText("You lost a tempo in the opening.")).toBeInTheDocument();
      await act(() => vi.advanceTimersByTimeAsync(60000));
      expect(getReview).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("gives up after three retries when no summary ever comes", async () => {
    vi.useFakeTimers();
    try {
      const getReview = vi.fn<ReviewApi["getReview"]>().mockResolvedValue({ ...review, summary: null });
      renderReview({ getReview });
      for (const delay of [0, 2000, 4000, 8000, 60000]) await act(() => vi.advanceTimersByTimeAsync(delay));
      expect(getReview).toHaveBeenCalledTimes(4);
    } finally {
      vi.useRealTimers();
    }
  });

  it("copes with a game that has no key moments or summary", async () => {
    renderReview({ getReview: async () => ({ ...review, key_moments: [], summary: null, accuracy: null }) });
    await screen.findByTestId("board");
    expect(screen.getByText("No key moments: a clean game.")).toBeInTheDocument();
  });

  it("explains an unfinished game", async () => {
    renderReview({
      getReview: async () => {
        throw new ApiError(409, "the review is available once the game is over");
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("the review is available once the game is over");
  });

  it("falls back to a generic message when the server is unreachable", async () => {
    renderReview({
      getGame: async () => {
        throw new Error("down");
      },
    });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not load the review"));
    await act(async () => undefined);
  });
});
