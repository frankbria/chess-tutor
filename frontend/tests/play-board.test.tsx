import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GameOverDialog } from "@/components/play/game-over-dialog";
import { MoveList } from "@/components/play/move-list";
import { PlayBoard } from "@/components/play/play-board";
import {
  ApiError,
  parseStreamEvent,
  type GameApi,
  type GameState,
  type GameStreamHandlers,
} from "@/lib/game-api";
import {
  destinationsFrom,
  describeResult,
  isPromotion,
  lastMoveSquares,
} from "@/lib/chess-board";

// chessground needs real layout; this stand-in exposes what the board would be given.
vi.mock("@/components/play/chessboard", () => ({
  Chessboard: (props: {
    fen: string;
    orientation: string;
    destinations?: Map<string, string[]>;
    lastMove?: [string, string];
    proposal?: [string, string];
    onMove: (from: string, to: string) => void;
  }) => (
    <div
      data-testid="board"
      data-orientation={props.orientation}
      data-last-move={props.lastMove?.join("")}
      data-proposal={props.proposal?.join("")}
    >
      <span data-testid="locked">{props.destinations ? "open" : "locked"}</span>
      <button onClick={() => props.onMove("e2", "e4")}>drop e2e4</button>
      <button onClick={() => props.onMove("a7", "a8")}>drop a7a8</button>
    </div>
  ),
}));

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

function game(overrides: Partial<GameState> = {}): GameState {
  return {
    id: 1,
    mode: "regular",
    opponent_elo: 1200,
    player_color: "white",
    status: "active",
    result: null,
    fen: START_FEN,
    moves: [],
    side_to_move: "white",
    legal_moves: ["e2e4", "a7a8q", "a7a8n"],
    time_control: null,
    clock: null,
    ended_on_time: false,
    ...overrides,
  };
}

function fakeApi(initial: GameState, overrides: Partial<GameApi> = {}) {
  let handlers: GameStreamHandlers | undefined;
  const api: GameApi = {
    getGame: async () => initial,
    playMove: async () => initial,
    resign: async () => ({ ...initial, status: "finished", result: "0-1" }),
    getCoachMessages: async () => [],
    answerQuestion: async () => undefined,
    skipQuestion: async () => undefined,
    takeback: async () => initial,
    proposeMove: async (_id, uci) => ({ uci, san: "e4", ply: 1, rethinks: 0 }),
    rethink: async () => ({ rethinks: 1 }),
    lockIn: async () => initial,
    getPendingProposal: async () => null,
    subscribe: (_id, next) => {
      handlers = next;
      return () => undefined;
    },
    ...overrides,
  };
  return { api, stream: () => handlers };
}

describe("PlayBoard", () => {
  it("orients the board to the player's colour and unlocks it on their turn", async () => {
    const { api } = fakeApi(
      game({ player_color: "black", side_to_move: "black" }),
    );
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByTestId("board")).toHaveAttribute(
      "data-orientation",
      "black",
    );
    expect(screen.getByTestId("locked")).toHaveTextContent("open");
    expect(screen.getByText("Your move")).toBeInTheDocument();
  });

  it("restores the position on load, locks the board on the opponent's turn and lists the moves", async () => {
    const { api } = fakeApi(
      game({ moves: ["e4", "e5"], side_to_move: "black" }),
    );
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByTestId("board")).toHaveAttribute(
      "data-last-move",
      "e7e5",
    );
    expect(screen.getByTestId("locked")).toHaveTextContent("locked");
    expect(screen.getByRole("list", { name: "Moves" })).toHaveTextContent(
      "1.e4e5",
    );
  });

  it("shows the thinking indicator until the reply arrives", async () => {
    let finish: (state: GameState) => void = () => undefined;
    const reply = game({ moves: ["e4", "e5"] });
    const { api } = fakeApi(game(), {
      playMove: () => new Promise((resolve) => (finish = resolve)),
    });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop e2e4"));
    expect(
      await screen.findByText("Opponent is thinking…"),
    ).toBeInTheDocument();
    finish(reply);
    await waitFor(() =>
      expect(
        screen.queryByText("Opponent is thinking…"),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("list", { name: "Moves" })).toHaveTextContent("e5");
  });

  it("sends a promotion move with the chosen piece", async () => {
    const playMove = vi.fn(async () => game());
    const { api } = fakeApi(game(), { playMove });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop a7a8"));
    const picker = await screen.findByRole("dialog", {
      name: "Promote your pawn",
    });
    expect(playMove).not.toHaveBeenCalled();
    await userEvent.click(
      within(picker).getByRole("button", { name: /Knight/ }),
    );
    expect(playMove).toHaveBeenCalledWith(1, "a7a8n");
  });

  it("does not move when the promotion picker is dismissed", async () => {
    const playMove = vi.fn(async () => game());
    const { api } = fakeApi(game(), { playMove });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop a7a8"));
    await userEvent.keyboard("{Escape}");
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    expect(playMove).not.toHaveBeenCalled();
  });

  it("shows the server's message on a 409 and keeps the game state", async () => {
    const { api } = fakeApi(game({ moves: ["d4"] }), {
      playMove: async () => {
        throw new ApiError(409, "it is not your turn");
      },
    });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop e2e4"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "it is not your turn",
    );
    expect(screen.getByRole("list", { name: "Moves" })).toHaveTextContent("d4");
    expect(screen.getByTestId("locked")).toHaveTextContent("open");
  });

  it("applies opponent moves and game over from the stream", async () => {
    let current = game();
    const { api, stream } = fakeApi(game(), { getGame: async () => current });
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByTestId("board");
    current = game({ moves: ["e4", "e5"] });
    stream()?.onOpponentMove({ uci: "e7e5", san: "e5", fen: "", clock: null });
    await waitFor(() =>
      expect(screen.getByRole("list", { name: "Moves" })).toHaveTextContent(
        "e5",
      ),
    );
    current = game({
      moves: ["e4", "e5"],
      status: "finished",
      result: "0-1",
      legal_moves: [],
    });
    stream()?.onGameOver({ result: "0-1", reason: "checkmate" });
    expect(
      await screen.findByRole("dialog", { name: "You lost" }),
    ).toHaveTextContent("by checkmate");
  });

  it("resigns after confirmation and opens the game-over dialog", async () => {
    const { api } = fakeApi(game());
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Resign" }),
    );
    await userEvent.click(
      screen.getByRole("button", { name: "Confirm resign" }),
    );
    expect(
      await screen.findByRole("dialog", { name: "You lost" }),
    ).toBeInTheDocument();
  });

  it("offers a takeback in Passive games only and shows the shortened game afterwards", async () => {
    const played = game({ mode: "passive", moves: ["e4", "e5"] });
    const { api } = fakeApi(played, {
      takeback: async () => game({ mode: "passive" }),
    });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Takeback" }),
    );
    expect(await screen.findByText("No moves yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Takeback" })).toBeDisabled();
  });

  it.each(["regular", "active"] as const)(
    "does not offer a takeback in %s games",
    async (mode) => {
      const { api } = fakeApi(game({ mode, moves: ["e4", "e5"] }));
      render(<PlayBoard gameId={1} api={api} />);
      await screen.findByTestId("board");
      expect(
        screen.queryByRole("button", { name: "Takeback" }),
      ).not.toBeInTheDocument();
    },
  );

  it("shows the server's reason when a takeback is refused", async () => {
    const refuse = async () => {
      throw new ApiError(409, "the player has not moved yet");
    };
    const { api } = fakeApi(game({ mode: "passive", moves: ["e4", "e5"] }), {
      takeback: refuse,
    });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Takeback" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "the player has not moved yet",
    );
  });

  it("shows both clocks from the server state, the player's below the opponent's", async () => {
    const clock = {
      white_ms: 590_000,
      black_ms: 600_000,
      running: "white",
    } as const;
    const { api } = fakeApi(game({ time_control: "10+5", clock }));
    render(<PlayBoard gameId={1} api={api} />);
    const timers = await screen.findAllByRole("timer");
    expect(timers.map((timer) => timer.getAttribute("aria-label"))).toEqual([
      "Opponent clock",
      "Your clock",
    ]);
    expect(screen.getByRole("timer", { name: "Your clock" })).toHaveTextContent(
      "9:50",
    );
    expect(
      screen.getByRole("timer", { name: "Opponent clock" }),
    ).toHaveTextContent("10:00");
  });

  it("switches the running clock on an opponent move without waiting for a refetch", async () => {
    const timed = game({
      side_to_move: "black",
      clock: { white_ms: 590_000, black_ms: 600_000, running: "black" },
    });
    let loads = 0;
    const { api, stream } = fakeApi(timed, {
      getGame: () =>
        loads++ === 0 ? Promise.resolve(timed) : new Promise(() => undefined),
    });
    render(<PlayBoard gameId={1} api={api} />);
    expect(
      await screen.findByRole("timer", { name: "Opponent clock" }),
    ).toHaveAttribute("data-running", "true");

    act(() =>
      stream()?.onOpponentMove({
        uci: "e7e5",
        san: "e5",
        fen: START_FEN,
        clock: { white_ms: 590_000, black_ms: 605_000, running: "white" },
      }),
    );

    expect(screen.getByRole("timer", { name: "Your clock" })).toHaveAttribute(
      "data-running",
      "true",
    );
    expect(
      screen.getByRole("timer", { name: "Opponent clock" }),
    ).toHaveAttribute("data-running", "false");
    expect(
      screen.getByRole("timer", { name: "Opponent clock" }),
    ).toHaveTextContent("10:05");
  });

  it("shows no clocks in an untimed game", async () => {
    const { api } = fakeApi(game());
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByTestId("board");
    expect(screen.queryByRole("timer")).not.toBeInTheDocument();
  });

  it("says the game ended on time after a reload", async () => {
    const clock = { white_ms: 0, black_ms: 500_000, running: null } as const;
    const { api } = fakeApi(
      game({ status: "finished", result: "0-1", clock, ended_on_time: true }),
    );
    render(<PlayBoard gameId={1} api={api} />);
    expect(
      await screen.findByRole("dialog", { name: "You lost" }),
    ).toHaveTextContent("0-1 on time");
  });

  it("reports a network failure without losing state", async () => {
    const { api } = fakeApi(game(), {
      playMove: async () => {
        throw new TypeError("failed to fetch");
      },
    });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop e2e4"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not reach the game server",
    );
  });
});

describe("PlayBoard connection handling", () => {
  it("shows the load error with Retry instead of loading forever", async () => {
    let attempts = 0;
    const { api } = fakeApi(game(), {
      getGame: async () => {
        if (attempts++ === 0) throw new ApiError(404, "game not found");
        return game();
      },
    });
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "game not found",
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("board")).toBeInTheDocument();
  });

  it("shows a reconnecting status until the next event", async () => {
    const { api, stream } = fakeApi(game());
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByTestId("board");
    act(() => stream()?.onConnectionLost());
    expect(
      screen.getByText("Connection lost, reconnecting…"),
    ).toBeInTheDocument();
    act(() => stream()?.onSnapshot({ fen: START_FEN, status: "active" }));
    expect(
      screen.queryByText("Connection lost, reconnecting…"),
    ).not.toBeInTheDocument();
  });

  it("ignores malformed stream events", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(parseStreamEvent("{not json")).toBeNull();
    expect(parseStreamEvent<{ a: number }>('{"a":1}')).toEqual({ a: 1 });
  });
});

describe("GameOverDialog", () => {
  it.each([
    ["1-0", "white", "You won!"],
    ["1-0", "black", "You lost"],
    ["1/2-1/2", "white", "Draw"],
  ] as const)("titles %s for %s as %s and shows the reason", (result, color, title) => {
    render(<GameOverDialog gameId={7} open result={result} reason="threefold_repetition" playerColor={color} onClose={() => undefined} />);
    expect(screen.getByRole("dialog", { name: title })).toHaveTextContent("by threefold repetition");
  });

  it("words a timeout as on time", () => {
    render(<GameOverDialog gameId={7} open result="0-1" reason="timeout" playerColor="white" onClose={() => undefined} />);
    expect(screen.getByRole("dialog")).toHaveTextContent("0-1 on time");
  });

  it("links to the game's review", () => {
    render(<GameOverDialog gameId={7} open result="1-0" playerColor="white" onClose={() => undefined} />);
    expect(screen.getByRole("link", { name: "Review game" })).toHaveAttribute("href", "/review/7");
  });

  it("omits the reason when unknown", () => {
    render(<GameOverDialog gameId={7} open result="1-0" playerColor="white" onClose={() => undefined} />);
    expect(screen.getByRole("dialog")).not.toHaveTextContent(" by ");
  });
});

describe("MoveList", () => {
  it("pairs white and black moves per row", () => {
    render(<MoveList moves={["e4", "e5", "Nf3"]} />);
    const list = screen.getByRole("list", { name: "Moves" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(list).toHaveTextContent("2.Nf3");
  });

  it("says so when empty", () => {
    render(<MoveList moves={[]} />);
    expect(screen.getByText("No moves yet.")).toBeInTheDocument();
  });
});

describe("board helpers", () => {
  it("groups destinations by origin square", () => {
    expect(destinationsFrom(["e2e3", "e2e4", "g1f3"]).get("e2")).toEqual([
      "e3",
      "e4",
    ]);
  });

  it("detects promotion only for five-character moves", () => {
    expect(isPromotion(["a7a8q"], "a7", "a8")).toBe(true);
    expect(isPromotion(["a2a3"], "a2", "a3")).toBe(false);
  });

  it("finds the last move squares from SAN", () => {
    expect(lastMoveSquares(["e4", "e5", "Nf3"])).toEqual(["g1", "f3"]);
    expect(lastMoveSquares([])).toBeUndefined();
  });

  it("describes results from the player's side", () => {
    expect(describeResult("0-1", "black")).toBe("won");
  });

  describe("Active mode", () => {
    const active = () => game({ mode: "active" });

    it("proposes a board move instead of playing it and keeps the position where it was", async () => {
      const playMove = vi.fn(async () => active());
      const proposeMove = vi.fn(async (_id: number, uci: string) => ({
        uci,
        san: "e4",
        ply: 1,
        rethinks: 0,
      }));
      const { api } = fakeApi(active(), { playMove, proposeMove });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      expect(proposeMove).toHaveBeenCalledWith(1, "e2e4");
      expect(playMove).not.toHaveBeenCalled();
      expect(await screen.findByText("Proposed move: e4")).toBeInTheDocument();
      expect(screen.getByTestId("board")).toHaveAttribute(
        "data-proposal",
        "e2e4",
      );
      expect(screen.getByTestId("locked")).toHaveTextContent("locked");
      expect(screen.getByText("No moves yet.")).toBeInTheDocument();
    });

    it("locks the board while a proposal is being fetched", async () => {
      const { api } = fakeApi(active(), {
        proposeMove: () => new Promise(() => undefined),
      });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await waitFor(() =>
        expect(screen.getByTestId("locked")).toHaveTextContent("locked"),
      );
    });

    it("offers large Rethink and Lock in buttons by touch only while a move is proposed", async () => {
      const { api } = fakeApi(active());
      render(<PlayBoard gameId={1} api={api} />);
      await screen.findByTestId("board");
      expect(
        screen.queryByRole("button", { name: "Lock in" }),
      ).not.toBeInTheDocument();
      await userEvent.click(screen.getByText("drop e2e4"));
      for (const name of ["Rethink", "Lock in"]) {
        expect(await screen.findByRole("button", { name })).toHaveClass(
          "min-h-14",
        );
      }
    });

    it("restores the pre-move position on Rethink and lets the player try again", async () => {
      const rethink = vi.fn(async () => ({ rethinks: 1 }));
      const { api } = fakeApi(active(), { rethink });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await userEvent.pointer([
        {
          keys: "[TouchA>]",
          target: await screen.findByRole("button", { name: "Rethink" }),
        },
        { keys: "[/TouchA]" },
      ]);
      expect(rethink).toHaveBeenCalledWith(1);
      await waitFor(() =>
        expect(screen.queryByText("Proposed move: e4")).not.toBeInTheDocument(),
      );
      expect(screen.getByTestId("board")).not.toHaveAttribute("data-proposal");
      expect(screen.getByTestId("locked")).toHaveTextContent("open");
    });

    it("commits the move on Lock in and shows the opponent's reply", async () => {
      const lockIn = vi.fn(async () =>
        game({ mode: "active", moves: ["e4", "e5"] }),
      );
      const { api } = fakeApi(active(), { lockIn });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await userEvent.click(
        await screen.findByRole("button", { name: "Lock in" }),
      );
      expect(lockIn).toHaveBeenCalledWith(1);
      await waitFor(() =>
        expect(screen.getByRole("list", { name: "Moves" })).toHaveTextContent(
          "e5",
        ),
      );
      expect(
        screen.queryByRole("button", { name: "Lock in" }),
      ).not.toBeInTheDocument();
    });

    it("keeps Rethink and Lock in when locking in fails but the server still holds the proposal", async () => {
      const held = { uci: "e2e4", san: "e4", ply: 1, rethinks: 0 };
      const { api } = fakeApi(active(), {
        lockIn: async () => {
          throw new TypeError("network down");
        },
        getPendingProposal: async () => held,
      });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await userEvent.click(
        await screen.findByRole("button", { name: "Lock in" }),
      );
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Lock in" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Rethink" })).toBeEnabled();
    });

    it("shows the server's reason and clears the proposal when locking in fails", async () => {
      const lockIn = async () => {
        throw new ApiError(409, "no move has been proposed");
      };
      const { api } = fakeApi(active(), { lockIn });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await userEvent.click(
        await screen.findByRole("button", { name: "Lock in" }),
      );
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "no move has been proposed",
      );
      expect(
        screen.queryByRole("button", { name: "Lock in" }),
      ).not.toBeInTheDocument();
    });

    it("shows a failed proposal's reason without a proposal", async () => {
      const proposeMove = async () => {
        throw new ApiError(409, "it is not your turn");
      };
      const { api } = fakeApi(active(), { proposeMove });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        "it is not your turn",
      );
      expect(
        screen.queryByRole("button", { name: "Rethink" }),
      ).not.toBeInTheDocument();
    });

    it("shows the live hint inline with its level, as a hint rather than a comment", async () => {
      const { api, stream } = fakeApi(active());
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await screen.findByRole("button", { name: "Lock in" });
      act(() =>
        stream()?.onCoachComment({
          ply: 1,
          text: "Check what your knight guards.",
          classification: "hint",
          hint_level: 1,
          phase: "opening",
        }),
      );
      const inline = await screen.findByTestId("proposal-hint");
      expect(inline).toHaveTextContent("Hint 1");
      expect(inline).toHaveTextContent("Check what your knight guards.");
      expect(document.querySelector("[data-kind='hint']")).toHaveTextContent(
        "Hint 1",
      );
    });

    it("shows a remark inline without a level and ignores hints for other plies", async () => {
      const { api, stream } = fakeApi(active());
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await screen.findByRole("button", { name: "Lock in" });
      act(() => {
        stream()?.onCoachComment({
          ply: 3,
          text: "Old news.",
          classification: "hint",
          hint_level: 1,
          phase: "opening",
        });
        stream()?.onCoachComment({
          ply: 1,
          text: "The centre is open.",
          classification: "remark",
          hint_level: null,
          phase: "opening",
        });
      });
      const inline = await screen.findByTestId("proposal-hint");
      expect(inline).toHaveTextContent("The centre is open.");
      expect(inline).not.toHaveTextContent("Old news.");
      expect(inline).not.toHaveTextContent("Hint");
    });

    it("restores a pending proposal after a reload so it can still be rethought or locked in", async () => {
      const { api } = fakeApi(active(), {
        getPendingProposal: async () => ({
          uci: "e2e4",
          san: "e4",
          ply: 1,
          rethinks: 1,
        }),
      });
      render(<PlayBoard gameId={1} api={api} />);
      expect(await screen.findByText("Proposed move: e4")).toBeInTheDocument();
      expect(screen.getByTestId("board")).toHaveAttribute(
        "data-proposal",
        "e2e4",
      );
      expect(screen.getByRole("button", { name: "Lock in" })).toBeEnabled();
    });

    it("does not show a rethought move's hint under the next proposal at the same ply", async () => {
      let proposals = 0;
      const { api, stream } = fakeApi(active(), {
        proposeMove: async (_id, uci) => ({
          uci,
          san: "e4",
          ply: 1,
          rethinks: proposals++,
        }),
      });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await screen.findByRole("button", { name: "Rethink" });
      act(() =>
        stream()?.onCoachComment({
          ply: 1,
          text: "About e4.",
          classification: "hint",
          hint_level: 1,
          phase: "opening",
        }),
      );
      expect(await screen.findByTestId("proposal-hint")).toHaveTextContent(
        "About e4.",
      );
      await userEvent.click(screen.getByRole("button", { name: "Rethink" }));
      await waitFor(() =>
        expect(screen.queryByTestId("proposal-hint")).not.toBeInTheDocument(),
      );
      await userEvent.click(screen.getByText("drop e2e4")); // the mock board always drops e2e4; the fake API answers
      await screen.findByRole("button", { name: "Rethink" });
      expect(screen.queryByTestId("proposal-hint")).not.toBeInTheDocument();
      act(() =>
        stream()?.onCoachComment({
          ply: 1,
          text: "About the second try.",
          classification: "hint",
          hint_level: 2,
          phase: "opening",
        }),
      );
      expect(await screen.findByTestId("proposal-hint")).toHaveTextContent(
        "About the second try.",
      );
    });

    it("on restore shows only the hint that matches the pending proposal's rung", async () => {
      const hint = (text: string, hint_level: number) =>
        ({
          ply: 1,
          role: "coach",
          kind: "hint",
          text,
          classification: "hint",
          phase: "opening",
          hint_level,
        }) as const;
      const { api } = fakeApi(active(), {
        getPendingProposal: async () => ({
          uci: "d2d4",
          san: "d4",
          ply: 1,
          rethinks: 1,
        }),
        getCoachMessages: async () => [
          hint("Current pointer.", 2),
          hint("Stale nudge.", 1),
        ],
      });
      render(<PlayBoard gameId={1} api={api} />);
      expect(await screen.findByTestId("proposal-hint")).toHaveTextContent(
        "Current pointer.",
      );
    });

    it("hides a rethought hint even when the next proposal sits on the same top rung", async () => {
      let proposals = 2;
      const { api, stream } = fakeApi(active(), {
        proposeMove: async (_id, uci) => ({
          uci,
          san: "e4",
          ply: 1,
          rethinks: proposals++,
        }),
      });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await screen.findByRole("button", { name: "Rethink" });
      act(() =>
        stream()?.onCoachComment({
          ply: 1,
          text: "Old rung three.",
          classification: "hint",
          hint_level: 3,
          phase: "opening",
        }),
      );
      await screen.findByTestId("proposal-hint");
      await userEvent.click(screen.getByRole("button", { name: "Rethink" }));
      await userEvent.click(await screen.findByText("drop e2e4"));
      await screen.findByRole("button", { name: "Rethink" });
      expect(screen.queryByTestId("proposal-hint")).not.toBeInTheDocument();
    });

    it("disables both buttons while either request is running", async () => {
      let finish: (value: { rethinks: number }) => void = () => undefined;
      const lockIn = vi.fn(async () => active());
      const { api } = fakeApi(active(), {
        rethink: () => new Promise((resolve) => (finish = resolve)),
        lockIn,
      });
      render(<PlayBoard gameId={1} api={api} />);
      await userEvent.click(await screen.findByText("drop e2e4"));
      await userEvent.click(
        await screen.findByRole("button", { name: "Rethink" }),
      );
      expect(screen.getByRole("button", { name: "Lock in" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Rethink" })).toBeDisabled();
      finish({ rethinks: 1 });
      await waitFor(() =>
        expect(
          screen.queryByRole("button", { name: "Lock in" }),
        ).not.toBeInTheDocument(),
      );
      expect(lockIn).not.toHaveBeenCalled();
    });

    it("swaps the provisional template line for the final text under the same key", async () => {
      const { api, stream } = fakeApi(active());
      render(<PlayBoard gameId={1} api={api} />);
      await screen.findByTestId("board");
      const comment = {
        ply: 1,
        classification: "hint",
        hint_level: 1,
        phase: "opening" as const,
        message_key: "k1",
      };
      act(() =>
        stream()?.onCoachComment({
          ...comment,
          text: "Ask yourself what your opponent can do.",
          provisional: true,
        }),
      );
      const list = () => screen.getByRole("list", { name: "Coach messages" });
      const draft = await within(list()).findByText(
        "Ask yourself what your opponent can do.",
      );
      expect(draft.closest("li")).toHaveAttribute("data-provisional", "true");

      act(() =>
        stream()?.onCoachComment({ ...comment, text: "Check what Nc6 guards." }),
      );
      await within(list()).findByText("Check what Nc6 guards.");
      expect(
        within(list()).queryByText("Ask yourself what your opponent can do."),
      ).not.toBeInTheDocument();
      expect(document.querySelectorAll("[data-kind='hint']")).toHaveLength(1);
      expect(document.querySelector("[data-provisional]")).toBeNull();
    });

    it("keeps two coach messages that differ only in hint level", async () => {
      const { api, stream } = fakeApi(active());
      render(<PlayBoard gameId={1} api={api} />);
      await screen.findByTestId("board");
      act(() => {
        stream()?.onCoachComment({
          ply: 1,
          text: "Same words.",
          classification: "hint",
          hint_level: 1,
          phase: "opening",
        });
        stream()?.onCoachComment({
          ply: 1,
          text: "Same words.",
          classification: "hint",
          hint_level: 2,
          phase: "opening",
        });
      });
      await waitFor(() =>
        expect(document.querySelectorAll("[data-kind='hint']")).toHaveLength(2),
      );
    });
  });
});
