import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { GameOverDialog } from "@/components/play/game-over-dialog";
import { MoveList } from "@/components/play/move-list";
import { PlayBoard } from "@/components/play/play-board";
import { ApiError, parseStreamEvent, type GameApi, type GameState, type GameStreamHandlers } from "@/lib/game-api";
import { destinationsFrom, describeResult, isPromotion, lastMoveSquares } from "@/lib/chess-board";

// chessground needs real layout; this stand-in exposes what the board would be given.
vi.mock("@/components/play/chessboard", () => ({
  Chessboard: (props: {
    fen: string;
    orientation: string;
    destinations?: Map<string, string[]>;
    lastMove?: [string, string];
    onMove: (from: string, to: string) => void;
  }) => (
    <div data-testid="board" data-orientation={props.orientation} data-last-move={props.lastMove?.join("")}>
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
    takeback: async () => initial,
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
    const { api } = fakeApi(game({ player_color: "black", side_to_move: "black" }));
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByTestId("board")).toHaveAttribute("data-orientation", "black");
    expect(screen.getByTestId("locked")).toHaveTextContent("open");
    expect(screen.getByText("Your move")).toBeInTheDocument();
  });

  it("restores the position on load, locks the board on the opponent's turn and lists the moves", async () => {
    const { api } = fakeApi(game({ moves: ["e4", "e5"], side_to_move: "black" }));
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByTestId("board")).toHaveAttribute("data-last-move", "e7e5");
    expect(screen.getByTestId("locked")).toHaveTextContent("locked");
    expect(screen.getByRole("list", { name: "Moves" })).toHaveTextContent("1.e4e5");
  });

  it("shows the thinking indicator until the reply arrives", async () => {
    let finish: (state: GameState) => void = () => undefined;
    const reply = game({ moves: ["e4", "e5"] });
    const { api } = fakeApi(game(), { playMove: () => new Promise((resolve) => (finish = resolve)) });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop e2e4"));
    expect(await screen.findByText("Opponent is thinking…")).toBeInTheDocument();
    finish(reply);
    await waitFor(() => expect(screen.queryByText("Opponent is thinking…")).not.toBeInTheDocument());
    expect(screen.getByRole("list", { name: "Moves" })).toHaveTextContent("e5");
  });

  it("sends a promotion move with the chosen piece", async () => {
    const playMove = vi.fn(async () => game());
    const { api } = fakeApi(game(), { playMove });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop a7a8"));
    const picker = await screen.findByRole("dialog", { name: "Promote your pawn" });
    expect(playMove).not.toHaveBeenCalled();
    await userEvent.click(within(picker).getByRole("button", { name: /Knight/ }));
    expect(playMove).toHaveBeenCalledWith(1, "a7a8n");
  });

  it("does not move when the promotion picker is dismissed", async () => {
    const playMove = vi.fn(async () => game());
    const { api } = fakeApi(game(), { playMove });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop a7a8"));
    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
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
    expect(await screen.findByRole("alert")).toHaveTextContent("it is not your turn");
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
    await waitFor(() => expect(screen.getByRole("list", { name: "Moves" })).toHaveTextContent("e5"));
    current = game({ moves: ["e4", "e5"], status: "finished", result: "0-1", legal_moves: [] });
    stream()?.onGameOver({ result: "0-1", reason: "checkmate" });
    expect(await screen.findByRole("dialog", { name: "You lost" })).toHaveTextContent("by checkmate");
  });

  it("resigns after confirmation and opens the game-over dialog", async () => {
    const { api } = fakeApi(game());
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: "Resign" }));
    await userEvent.click(screen.getByRole("button", { name: "Confirm resign" }));
    expect(await screen.findByRole("dialog", { name: "You lost" })).toBeInTheDocument();
  });

  it("offers a takeback in Passive games only and shows the shortened game afterwards", async () => {
    const played = game({ mode: "passive", moves: ["e4", "e5"] });
    const { api } = fakeApi(played, { takeback: async () => game({ mode: "passive" }) });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: "Takeback" }));
    expect(await screen.findByText("No moves yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Takeback" })).toBeDisabled();
  });

  it.each(["regular", "active"] as const)("does not offer a takeback in %s games", async (mode) => {
    const { api } = fakeApi(game({ mode, moves: ["e4", "e5"] }));
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByTestId("board");
    expect(screen.queryByRole("button", { name: "Takeback" })).not.toBeInTheDocument();
  });

  it("shows the server's reason when a takeback is refused", async () => {
    const refuse = async () => {
      throw new ApiError(409, "the player has not moved yet");
    };
    const { api } = fakeApi(game({ mode: "passive", moves: ["e4", "e5"] }), { takeback: refuse });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByRole("button", { name: "Takeback" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("the player has not moved yet");
  });

  it("shows both clocks from the server state, the player's below the opponent's", async () => {
    const clock = { white_ms: 590_000, black_ms: 600_000, running: "white" } as const;
    const { api } = fakeApi(game({ time_control: "10+5", clock }));
    render(<PlayBoard gameId={1} api={api} />);
    const timers = await screen.findAllByRole("timer");
    expect(timers.map((timer) => timer.getAttribute("aria-label"))).toEqual(["Opponent clock", "Your clock"]);
    expect(screen.getByRole("timer", { name: "Your clock" })).toHaveTextContent("9:50");
    expect(screen.getByRole("timer", { name: "Opponent clock" })).toHaveTextContent("10:00");
  });

  it("switches the running clock on an opponent move without waiting for a refetch", async () => {
    const timed = game({
      side_to_move: "black",
      clock: { white_ms: 590_000, black_ms: 600_000, running: "black" },
    });
    let loads = 0;
    const { api, stream } = fakeApi(timed, {
      getGame: () => (loads++ === 0 ? Promise.resolve(timed) : new Promise(() => undefined)),
    });
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByRole("timer", { name: "Opponent clock" })).toHaveAttribute("data-running", "true");

    act(() =>
      stream()?.onOpponentMove({
        uci: "e7e5",
        san: "e5",
        fen: START_FEN,
        clock: { white_ms: 590_000, black_ms: 605_000, running: "white" },
      }),
    );

    expect(screen.getByRole("timer", { name: "Your clock" })).toHaveAttribute("data-running", "true");
    expect(screen.getByRole("timer", { name: "Opponent clock" })).toHaveAttribute("data-running", "false");
    expect(screen.getByRole("timer", { name: "Opponent clock" })).toHaveTextContent("10:05");
  });

  it("shows no clocks in an untimed game", async () => {
    const { api } = fakeApi(game());
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByTestId("board");
    expect(screen.queryByRole("timer")).not.toBeInTheDocument();
  });

  it("says the game ended on time after a reload", async () => {
    const clock = { white_ms: 0, black_ms: 500_000, running: null } as const;
    const { api } = fakeApi(game({ status: "finished", result: "0-1", clock, ended_on_time: true }));
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByRole("dialog", { name: "You lost" })).toHaveTextContent("0-1 on time");
  });

  it("reports a network failure without losing state", async () => {
    const { api } = fakeApi(game(), {
      playMove: async () => {
        throw new TypeError("failed to fetch");
      },
    });
    render(<PlayBoard gameId={1} api={api} />);
    await userEvent.click(await screen.findByText("drop e2e4"));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not reach the game server");
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
    expect(await screen.findByRole("alert")).toHaveTextContent("game not found");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("board")).toBeInTheDocument();
  });

  it("shows a reconnecting status until the next event", async () => {
    const { api, stream } = fakeApi(game());
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByTestId("board");
    act(() => stream()?.onConnectionLost());
    expect(screen.getByText("Connection lost, reconnecting…")).toBeInTheDocument();
    act(() => stream()?.onSnapshot({ fen: START_FEN, status: "active" }));
    expect(screen.queryByText("Connection lost, reconnecting…")).not.toBeInTheDocument();
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
    expect(destinationsFrom(["e2e3", "e2e4", "g1f3"]).get("e2")).toEqual(["e3", "e4"]);
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
});
