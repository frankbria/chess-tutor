import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CoachPanel } from "@/components/play/coach-panel";
import { PlayBoard } from "@/components/play/play-board";
import { ApiError, type CoachMessage, type GameApi, type GameState, type GameStreamHandlers } from "@/lib/game-api";

vi.mock("@/components/play/chessboard", () => ({ Chessboard: () => <div data-testid="board" /> }));

const START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

const message = (overrides: Partial<CoachMessage> = {}): CoachMessage => ({
  ply: 7,
  role: "coach",
  kind: "comment",
  text: "That hands your opponent a piece.",
  classification: "blunder",
  phase: "middlegame",
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

function fakeApi(initial: GameState, history: CoachMessage[] = [], overrides: Partial<GameApi> = {}) {
  let handlers: GameStreamHandlers | undefined;
  const api: GameApi = {
    getGame: async () => initial,
    playMove: async () => initial,
    resign: async () => initial,
    getCoachMessages: async () => history,
    answerQuestion: async () => undefined,
    skipQuestion: async () => undefined,
    takeback: async () => initial,
    subscribe: (_id, next) => {
      handlers = next;
      return () => undefined;
    },
    ...overrides,
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

  it("labels each message with its game phase", () => {
    render(<CoachPanel messages={[message({ phase: "opening" }), message({ phase: "endgame" })]} moves={[]} />);
    const [first, second] = within(screen.getByRole("list", { name: "Coach messages" })).getAllByRole("listitem");
    expect(first).toHaveTextContent(/opening/);
    expect(second).toHaveTextContent(/endgame/);
  });

  it("keeps a player message distinct from a coach message with the same text", () => {
    render(<CoachPanel messages={[message(), message({ role: "player" })]} moves={[]} />);
    expect(within(screen.getByRole("list", { name: "Coach messages" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("offers the phone toggle as a button but a plain heading from md up", () => {
    render(<CoachPanel messages={[]} moves={[]} />);
    expect(screen.getByRole("button", { name: /Coach/ })).toHaveClass("md:hidden");
    expect(screen.getByRole("heading", { name: "Coach" })).toHaveClass("hidden", "md:flex");
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

const question = () => message({ kind: "question", text: "What were you thinking when you played Nxe5?" });

describe("PlayBoard coach questions", () => {
  it("renders a streamed question and unlocks the input and Skip for it", async () => {
    const { api, stream } = fakeApi(game());
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByTestId("board");
    expect(screen.getByLabelText("Message the coach")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();

    act(() => stream().onCoachComment({ ply: 7, text: question().text, classification: "blunder", phase: "middlegame", kind: "question" }));

    expect(screen.getByText("Question", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message the coach")).toBeEnabled();
    expect(screen.getByRole("button", { name: "Skip" })).toBeInTheDocument();
  });

  it("sends the answer, shows it, and locks the input until the coach reply arrives", async () => {
    const user = userEvent.setup();
    const answerQuestion = vi.fn(async () => undefined);
    const { api, stream } = fakeApi(game(), [question()], { answerQuestion });
    render(<PlayBoard gameId={1} api={api} />);
    await user.type(await screen.findByLabelText("Message the coach"), "I wanted a pawn");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(answerQuestion).toHaveBeenCalledWith(1, "I wanted a pawn");
    expect(await screen.findByText("I wanted a pawn", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByLabelText("Message the coach")).toBeDisabled();

    act(() => stream().onCoachComment({ ply: 7, text: "I see the idea, but look at e5.", classification: "blunder", phase: "middlegame", kind: "answer" }));
    expect(screen.getByText("I see the idea, but look at e5.", { selector: "p" })).toBeInTheDocument();
  });

  it("skips the question and shows that it was skipped", async () => {
    const user = userEvent.setup();
    const skipQuestion = vi.fn(async () => undefined);
    const { api } = fakeApi(game(), [question()], { skipQuestion });
    render(<PlayBoard gameId={1} api={api} />);
    await user.click(await screen.findByRole("button", { name: "Skip" }));

    expect(skipQuestion).toHaveBeenCalledWith(1);
    expect(await screen.findByText("You skipped this question.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("does not offer a question that history shows as closed, or once the game is over", async () => {
    const closed = [question(), message({ role: "player", kind: "answer", text: "A pawn" })];
    const { api } = fakeApi(game(), closed);
    const { unmount } = render(<PlayBoard gameId={1} api={api} />);
    await screen.findByText("A pawn", { selector: "p" });
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
    unmount();

    const over = fakeApi(game({ status: "finished", result: "0-1" }), [question()]);
    render(<PlayBoard gameId={1} api={over.api} />);
    await screen.findByText(question().text, { selector: "p" });
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("shows the server's reason when the answer is refused", async () => {
    const user = userEvent.setup();
    const refuse = async () => {
      throw new ApiError(409, "the coach has not asked a question");
    };
    const { api } = fakeApi(game(), [question()], { answerQuestion: refuse });
    render(<PlayBoard gameId={1} api={api} />);
    await user.type(await screen.findByLabelText("Message the coach"), "hi");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("the coach has not asked a question");
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

    act(() => stream().onCoachComment({ ply: 7, text: "That hands your opponent a piece.", classification: "blunder", phase: "middlegame" }));
    act(() => stream().onCoachComment({ ply: 9, text: "Nice recovery.", classification: "great", phase: "endgame" }));

    const items = within(screen.getByRole("list", { name: "Coach messages" })).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[1]).toHaveTextContent("Nice recovery.");
    expect(screen.getByTestId("coach-live-region")).toHaveTextContent("Nice recovery.");
  });

  it("does not merge a restored player message into a coach message with the same text", async () => {
    const { api, stream } = fakeApi(game(), [message({ role: "player" })]);
    render(<PlayBoard gameId={1} api={api} />);
    await screen.findByText("That hands your opponent a piece.", { selector: "p" });
    act(() => stream().onCoachComment({ ply: 7, text: "That hands your opponent a piece.", classification: "blunder", phase: "middlegame" }));
    expect(within(screen.getByRole("list", { name: "Coach messages" })).getAllByRole("listitem")).toHaveLength(2);
  });

  it("keeps a comment that streams in while the history is still loading", async () => {
    let finishHistory: (history: CoachMessage[]) => void = () => undefined;
    const { api, stream } = fakeApi(game());
    const pending = new Promise<CoachMessage[]>((resolve) => (finishHistory = resolve));
    render(<PlayBoard gameId={1} api={{ ...api, getCoachMessages: () => pending }} />);
    await screen.findByTestId("board");

    act(() => stream().onCoachComment({ ply: 9, text: "Live one.", classification: "great", phase: "endgame" }));
    await act(async () => finishHistory([message()]));

    const items = within(screen.getByRole("list", { name: "Coach messages" })).getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining("hands your opponent"),
      expect.stringContaining("Live one."),
    ]);
  });

  it("drops the messages of taken-back plies and keeps the Takeback button beside the panel", async () => {
    const user = userEvent.setup();
    let history = [message({ ply: 3, text: "Early thought" }), message()];
    const { api } = fakeApi(game());
    const takeback = async () => {
      history = [history[0]];
      return game({ moves: ["e4", "e5", "Nf3", "Nc6", "Bc4", "Nf6"], side_to_move: "white" });
    };
    render(<PlayBoard gameId={1} api={{ ...api, takeback, getCoachMessages: async () => history }} />);
    await screen.findByText("That hands your opponent a piece.", { selector: "p" });

    await user.click(screen.getByRole("button", { name: "Takeback" }));

    await waitFor(() => expect(screen.queryByText("That hands your opponent a piece.", { selector: "p" })).not.toBeInTheDocument());
    expect(screen.getByText("Early thought", { selector: "p" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Coach" })).toBeInTheDocument();
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
