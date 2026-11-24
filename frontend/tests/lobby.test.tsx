import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LobbyApiProvider } from "@/components/lobby/lobby-api-context";
import { defaultElo, labelFor, MODES } from "@/components/lobby/game-options";
import { NewGameDialog } from "@/components/lobby/new-game-dialog";
import { SettingsApiProvider } from "@/components/settings/settings-api-context";
import { ResumeList } from "@/components/lobby/resume-list";
import type { SettingsApi, SettingsState } from "@/lib/settings-api";
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
    time_control: null,
    clock: null,
    ended_on_time: false,
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
    getLastChoice: async () => ({ mode: null, color: null, opponent_elo: null, time_control: null }),
    getLastGame: async () => null,
    ...overrides,
  };
  return { api, created };
}

export function wrapperFor(api: LobbyApi) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <LobbyApiProvider api={api}>{children}</LobbyApiProvider>;
  };
}

async function openDialog(api: LobbyApi) {
  const user = userEvent.setup();
  render(<NewGameDialog />, { wrapper: wrapperFor(api) });
  await user.click(screen.getByRole("button", { name: "Options" }));
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
    render(<NewGameDialog />, { wrapper: wrapperFor(api) });
    await user.click(screen.getByRole("button", { name: "Options" }));
    await waitFor(() => expect(screen.getByLabelText("Opponent Elo")).toHaveValue("1200"));
  });

  it("submits the Elo chosen on the slider", async () => {
    const { api, created } = fakeApi();
    const user = await openDialog(api);
    fireEvent.change(screen.getByLabelText("Opponent Elo"), { target: { value: "2000" } });
    await user.click(screen.getByRole("button", { name: "Start game" }));
    expect(created[0].opponent_elo).toBe(2000);
  });

  it("retries loading the rating on the next open after a failure", async () => {
    let calls = 0;
    const { api } = fakeApi({
      getPlayer: async () => {
        calls += 1;
        if (calls === 1) throw new ApiError(500, "boom");
        return { rating: 1337 };
      },
    });
    const user = userEvent.setup();
    render(<NewGameDialog />, { wrapper: wrapperFor(api) });
    await user.click(screen.getByRole("button", { name: "Options" }));
    await waitFor(() => expect(screen.getByLabelText("Opponent Elo")).toHaveValue("1200"));
    await user.keyboard("{Escape}");
    await user.click(screen.getByRole("button", { name: "Options" }));
    await waitFor(() => expect(screen.getByLabelText("Opponent Elo")).toHaveValue("1350"));
  });

  it("clamps the default Elo to the allowed range", async () => {
    const { api } = fakeApi({ getPlayer: async () => ({ rating: 100 }) });
    const user = userEvent.setup();
    render(<NewGameDialog />, { wrapper: wrapperFor(api) });
    await user.click(screen.getByRole("button", { name: "Options" }));
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
    render(<ResumeList />, { wrapper: wrapperFor(api) });
    expect(await screen.findByText("Passive tutor, Black")).toBeInTheDocument();
    expect(screen.getByText("Elo 1500, 3 moves")).toBeInTheDocument();
    expect(screen.getByText("Elo 1500, 1 move")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Resume game 7" })).toHaveAttribute("href", "/play/7");
    expect(screen.getByRole("link", { name: "Resume game 9" })).toHaveAttribute("href", "/play/9");
  });

  it("shows an empty state", async () => {
    render(<ResumeList />, { wrapper: wrapperFor(fakeApi().api) });
    expect(await screen.findByText(/No unfinished games/)).toBeInTheDocument();
  });

  it("shows an error when loading fails", async () => {
    const { api } = fakeApi({
      listActiveGames: async () => {
        throw new ApiError(500, "boom");
      },
    });
    render(<ResumeList />, { wrapper: wrapperFor(api) });
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your games");
  });
});

describe("game options", () => {
  it("clamps the default Elo to the top of the range", () => {
    expect(defaultElo(3400)).toBe(3000);
  });

  it("labels known values and falls back to the raw value", () => {
    expect(labelFor(MODES, "passive")).toBe("Passive tutor");
    expect(labelFor(MODES, "unknown" as "regular")).toBe("unknown");
  });
});

describe("NewGameDialog default mode", () => {
  function settingsApi(getSettings: SettingsApi["getSettings"]): SettingsApi {
    return { getSettings, saveSettings: async () => Promise.reject(new Error("unused")) };
  }

  function renderWith(settings: SettingsApi) {
    const { api } = fakeApi();
    render(<NewGameDialog />, {
      wrapper: ({ children }) => (
        <LobbyApiProvider api={api}>
          <SettingsApiProvider api={settings}>{children}</SettingsApiProvider>
        </LobbyApiProvider>
      ),
    });
  }

  it("preselects the saved default mode", async () => {
    renderWith(
      settingsApi(async () => ({
        preferences: {
          coach_verbosity: "normal",
          default_mode: "active",
          maia_model: "5m",
          monthly_cost_ceiling: 10,
        },
        monthly_cost: 0,
        budget_alert: false,
        recent_games: [],
      })),
    );
    await userEvent.setup().click(screen.getByRole("button", { name: "Options" }));

    await waitFor(() =>
      expect(screen.getByRole("radio", { name: /Active/ })).toBeChecked(),
    );
  });

  it("falls back to regular when settings cannot be loaded", async () => {
    renderWith(settingsApi(async () => Promise.reject(new Error("down"))));
    await userEvent.setup().click(screen.getByRole("button", { name: "Options" }));

    await screen.findByText("1350");
    expect(screen.getByRole("radio", { name: /Regular/ })).toBeChecked();
  });

  it("does not override a mode the user already chose", async () => {
    let resolveSettings: (state: SettingsState) => void = () => undefined;
    renderWith(
      settingsApi(() => new Promise<SettingsState>((resolve) => (resolveSettings = resolve))),
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Options" }));
    await user.click(screen.getByRole("radio", { name: /Passive/ }));

    resolveSettings({
      preferences: {
        coach_verbosity: "normal",
        default_mode: "active",
        maia_model: "5m",
        monthly_cost_ceiling: 10,
      },
      monthly_cost: 0,
      budget_alert: false,
      recent_games: [],
    });

    await screen.findByText("1350");
    await waitFor(() => expect(screen.getByRole("radio", { name: /Passive/ })).toBeChecked());
  });
});
