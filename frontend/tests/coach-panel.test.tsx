import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CoachPanel } from "@/components/play/coach-panel";
import { PlayBoard } from "@/components/play/play-board";
import type { CoachMessage, GameApi, GameState, GameStreamHandlers } from "@/lib/game-api";

vi.mock("@/components/play/chessboard", () => ({ Chessboard: () => <div data-testid="board" /> }));

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

const message = (overrides: Partial<CoachMessage> = {}): CoachMessage => ({
  ply: 7,
  role: "coach",
  kind: "comment",
  text: "That hands your opponent a piece.",
  classification: "blunder",
  ...overrides,
});

function game(overrides: Partial<GameState> = {}): GameState {
  return {
    id: 1,
    mode: "passive",
    opponent_elo: 1200,
    player_color: "white",
    status: "active",
    result: null,
    fen: START_FEN,
    moves: ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Nxe5"],
    side_to_move: "black",
    legal_moves: [],
    time_control: null,
    clock: null,
    ended_on_time: false,
    ...overrides,
  };
}

function fakeApi(initial: GameState, history: CoachMessage[] = []) {
  let handlers: GameStreamHandlers | undefined;
  const api: GameApi = {
    getGame: async () => initial,
    playMove: async () => initial,
    resign: async () => initial,
    getCoachMessages: async () => history,
    subscribe: (_id, next) => {
      handlers = next;
      return () => undefined;
    },
  };
  return { api, stream: () => handlers! };
}

describe("CoachPanel", () => {
  it.each([
    ["brilliant", "!!", "Brilliant"],
    ["great", "!", "Great move"],
    ["inaccuracy", "?!", "Inaccuracy"],
    ["mistake", "?", "Mistake"],
    ["blunder", "??", "Blunder"],
  ])("badges a %s as %s", (classification, symbol, label) => {
    render(<CoachPanel messages={[message({ classification })]} moves={[]} />);
    expect(screen.getByLabelText(label)).toHaveTextContent(symbol);
  });

  it("shows no badge for an unscored or ordinary move", () => {
    render(<CoachPanel messages={[message({ classification: null }), message({ classification: "good" })]} moves={[]} />);
    expect(screen.queryByTitle(/blunder|mistake/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/Move 7/)).toHaveLength(2);
  });

  it("names the move each message refers to and styles it by kind", () => {
    const moves = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6", "Nxe5"];
    render(
      <CoachPanel
        messages={[message(), message({ ply: 6, kind: "hint", text: "Look at f7.", classification: null })]}
        moves={moves}
      />,
    );
    const [comment, hint] = within(screen.getByRole("list", { name: "Coach messages" })).getAllByRole("listitem");
    expect(comment).toHaveAttribute("data-kind", "comment");
    expect(comment).toHaveTextContent("4. Nxe5");
    expect(hint).toHaveAttribute("data-kind", "hint");
    expect(hint).toHaveTextContent("3... Nf6");
  });

  it("collapses to the latest message with an unread count, and expands to the history", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<CoachPanel messages={[]} moves={[]} />);
    const toggle = screen.getByRole("button", { name: /Coach/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    rerender(<CoachPanel messages={[message({ text: "First thought" }), message({ text: "Second thought" })]} moves={[]} />);
    expect(screen.getByTestId("unread-badge")).toHaveTextContent("2 unread");
    expect(toggle).toHaveTextContent("Second thought");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByTestId("unread-badge")).not.toBeInTheDocument();

    await user.click(toggle);
    expect(screen.queryByTestId("unread-badge")).not.toBeInTheDocument();
    rerender(<CoachPanel messages={[message(), message(), message({ text: "Third" })]} moves={[]} />);
    expect(screen.getByTestId("unread-badge")).toHaveTextContent("1 unread");
  });

  it("closes on Escape and returns focus to the toggle", async () => {
    const user = userEvent.setup();
    render(<CoachPanel messages={[message()]} moves={[]} onSend={() => undefined} />);
    const toggle = screen.getByRole("button", { name: /Coach/ });
    await user.click(toggle);
    await user.click(screen.getByLabelText("Message the coach"));
    await user.keyboard("{Escape}");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveFocus();
  });

  it("announces the newest message through a polite live region", () => {
    const { rerender } = render(<CoachPanel messages={[]} moves={[]} />);
    const live = screen.getByTestId("coach-live-region");
    expect(live).toHaveAttribute("aria-live", "polite");
    rerender(<CoachPanel messages={[message()]} moves={[]} />);
    expect(live).toHaveTextContent("That hands your opponent a piece.");
  });

  it("keeps the input disabled with an explanation until onSend is wired", () => {
    render(<CoachPanel messages={[]} moves={[]} />);
    const input = screen.getByLabelText("Message the coach");
    expect(input).toBeDisabled();
    expect(input).toHaveAttribute("placeholder", expect.stringContaining("later update"));
  });

  it("sends the trimmed draft through onSend and clears it", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<CoachPanel messages={[]} moves={[]} onSend={onSend} />);
    await user.type(screen.getByLabelText("Message the coach"), "  why?  ");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(onSend).toHaveBeenCalledWith("why?");
    expect(screen.getByLabelText("Message the coach")).toHaveValue("");
  });
});

describe("PlayBoard coach panel", () => {
  it("restores the history on load", async () => {
    const { api } = fakeApi(game(), [message()]);
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByText("That hands your opponent a piece.", { selector: "p" })).toBeInTheDocument();
  });

  it("renders streamed comments as they arrive without repeating restored ones", async () => {
    const { api, stream } = fakeApi(game(), [message()]);
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByText("That hands your opponent a piece.", { selector: "p" });

    act(() => stream().onCoachComment({ ply: 7, text: "That hands your opponent a piece.", classification: "blunder" }));
    act(() => stream().onCoachComment({ ply: 9, text: "Nice recovery.", classification: "great" }));

    const items = within(screen.getByRole("list", { name: "Coach messages" })).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[1]).toHaveTextContent("Nice recovery.");
    expect(screen.getByTestId("coach-live-region")).toHaveTextContent("Nice recovery.");
  });

  it("keeps a comment that streams in while the history is still loading", async () => {
    let finishHistory: (history: CoachMessage[]) => void = () => undefined;
    const { api, stream } = fakeApi(game());
    const pending = new Promise<CoachMessage[]>((resolve) => (finishHistory = resolve));
    render(<PlayBoard gameId={1} api={{ ...api, getCoachMessages: () => pending }} />);
    await screen.findByTestId("board");

    act(() => stream().onCoachComment({ ply: 9, text: "Live one.", classification: "great" }));
    await act(async () => finishHistory([message()]));

    const items = within(screen.getByRole("list", { name: "Coach messages" })).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("hands your opponent"),
      expect.stringContaining("Live one."),
    ]);
  });

  it("is shown for active games", async () => {
    const { api } = fakeApi(game({ mode: "active" }));
    render(<PlayBoard gameId={1} api={api} />);
    expect(await screen.findByRole("region", { name: "Coach" })).toBeInTheDocument();
  });

  it("is hidden for regular games, which have no coach", async () => {
    const getCoachMessages = vi.fn(async () => []);
    const { api } = fakeApi(game({ mode: "regular" }));
    render(<PlayBoard gameId={1} api={{ ...api, getCoachMessages }} />);
    await screen.findByTestId("board");
    expect(screen.queryByRole("region", { name: "Coach" })).not.toBeInTheDocument();
    expect(getCoachMessages).not.toHaveBeenCalled();
  });
});
