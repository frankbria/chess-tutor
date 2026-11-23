import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NewGameDialog } from "@/components/lobby/new-game-dialog";
import { ResumeList } from "@/components/lobby/resume-list";
import { ApiError, type GameState, type LobbyApi, type NewGame } from "@/lib/game-api";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

function game(overrides: Partial<GameState> = {}): GameState {
  return {
    id: 7,
    mode: "regular",
    opponent_elo: 1500,
    player_color: "white",
    status: "active",
    result: null,
    fen: "",
    moves: [],
    side_to_move: "white",
    legal_moves: [],
    ...overrides,
  };
}

function fakeApi(overrides: Partial<LobbyApi> = {}) {
  const created: NewGame[] = [];
  const api: LobbyApi = {
    createGame: async (newGame) => {
      created.push(newGame);
      return game({ id: 42 });
    },
    listActiveGames: async () => [],
    getPlayer: async () => ({ rating: 1337 }),
    ...overrides,
  };
  return { api, created };
}

async function openDialog(api: LobbyApi) {
  const user = userEvent.setup();
  render(<NewGameDialog api={api} />);
  await user.click(screen.getByRole("button", { name: "Play" }));
  await screen.findByText("1350");
  return user;
}

beforeEach(() => push.mockClear());

describe("NewGameDialog", () => {
  it("defaults the Elo to the player rating rounded to 50", async () => {
    const { api } = fakeApi();
    await openDialog(api);
    expect(screen.getByLabelText("Opponent Elo")).toHaveValue("1350");
  });

  it.each([
    ["Regular", "regular"],
    ["Passive tutor", "passive"],
    ["Active tutor", "active"],
  ])("creates a %s game", async (label, mode) => {
    const { api, created } = fakeApi();
    const user = await openDialog(api);
    await user.click(screen.getByRole("radio", { name: new RegExp(label) }));
    await user.click(screen.getByRole("radio", { name: "Black" }));
    await user.click(screen.getByRole("button", { name: "Start game" }));
    expect(created).toEqual([{ mode, color: "black", opponent_elo: 1350 }]);
  });

  it.each(["White", "Black", "Random"])("sends the %s colour choice", async (label) => {
    const { api, created } = fakeApi();
    const user = await openDialog(api);
    await user.click(screen.getByRole("radio", { name: label }));
    await user.click(screen.getByRole("button", { name: "Start game" }));
    expect(created[0].color).toBe(label.toLowerCase());
  });

  it("navigates to the new game on success", async () => {
    const { api } = fakeApi();
    const user = await openDialog(api);
    await user.click(screen.getByRole("button", { name: "Start game" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/play/42"));
  });

  it("shows the API error inline and stays open on a 422", async () => {
    const { api } = fakeApi({
      createGame: async () => {
        throw new ApiError(422, "Opponent Elo must be between 400 and 3000");
      },
    });
    const user = await openDialog(api);
    await user.click(screen.getByRole("button", { name: "Start game" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Opponent Elo must be between 400 and 3000");
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Start game" })).toBeEnabled();
  });

  it("falls back to a default Elo when the rating cannot be loaded", async () => {
    const { api } = fakeApi({
      getPlayer: async () => {
        throw new ApiError(500, "boom");
      },
    });
    const user = userEvent.setup();
    render(<NewGameDialog api={api} />);
    await user.click(screen.getByRole("button", { name: "Play" }));
    await waitFor(() => expect(screen.getByLabelText("Opponent Elo")).toHaveValue("1200"));
  });

  it("clamps the default Elo to the allowed range", async () => {
    const { api } = fakeApi({ getPlayer: async () => ({ rating: 100 }) });
    const user = userEvent.setup();
    render(<NewGameDialog api={api} />);
    await user.click(screen.getByRole("button", { name: "Play" }));
    await waitFor(() => expect(screen.getByLabelText("Opponent Elo")).toHaveValue("400"));
  });

  it("reports an unexpected failure generically", async () => {
    const { api } = fakeApi({
      createGame: async () => {
        throw new TypeError("network");
      },
    });
    const user = await openDialog(api);
    await user.click(screen.getByRole("button", { name: "Start game" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not start the game");
  });
});

describe("ResumeList", () => {
  it("lists unfinished games with a link to resume each", async () => {
    const { api } = fakeApi({
      listActiveGames: async () => [
        game({ id: 7, mode: "passive", player_color: "black", opponent_elo: 1500, moves: ["e4", "e5", "Nf3"] }),
        game({ id: 9, mode: "regular", moves: ["d4"] }),
      ],
    });
    render(<ResumeList api={api} />);
    expect(await screen.findByText("Passive tutor, Black")).toBeInTheDocument();
    expect(screen.getByText("Elo 1500, 3 moves")).toBeInTheDocument();
    expect(screen.getByText("Elo 1500, 1 move")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Resume game 7" })).toHaveAttribute("href", "/play/7");
    expect(screen.getByRole("link", { name: "Resume game 9" })).toHaveAttribute("href", "/play/9");
  });

  it("shows an empty state", async () => {
    render(<ResumeList api={fakeApi().api} />);
    expect(await screen.findByText(/No unfinished games/)).toBeInTheDocument();
  });

  it("shows an error when loading fails", async () => {
    const { api } = fakeApi({
      listActiveGames: async () => {
        throw new ApiError(500, "boom");
      },
    });
    render(<ResumeList api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your games");
  });
});
