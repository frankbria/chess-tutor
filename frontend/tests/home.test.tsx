import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConceptHighlights } from "@/components/home/concept-highlights";
import { LastGameCard } from "@/components/home/last-game-card";
import { PlayButton } from "@/components/home/play-button";
import { LobbyApiProvider } from "@/components/lobby/lobby-api-context";
import { ProgressApiProvider } from "@/components/progress/progress-api-context";
import { SettingsApiProvider } from "@/components/settings/settings-api-context";
import { ApiError, type GameState, type LastChoice, type LastGame, type LobbyApi, type NewGame } from "@/lib/game-api";
import type { ConceptSummary, ProgressApi } from "@/lib/progress-api";
import type { SettingsApi } from "@/lib/settings-api";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

beforeEach(() => push.mockClear());

const NO_CHOICE: LastChoice = { mode: null, color: null, opponent_elo: null, time_control: null };

function lobbyApi(overrides: Partial<LobbyApi> = {}) {
  const created: NewGame[] = [];
  const api: LobbyApi = {
    createGame: async (newGame) => {
      created.push(newGame);
      return { id: 42 } as GameState;
    },
    listActiveGames: async () => [],
    getPlayer: async () => ({ rating: 1200 }),
    getLastChoice: async () => NO_CHOICE,
    getLastGame: async () => null,
    ...overrides,
  };
  return { api, created };
}

function settingsApi(defaultMode: "regular" | "passive" | "active" | null): SettingsApi {
  return {
    getSettings: async () => {
      if (defaultMode === null) throw new Error("down");
      return {
        preferences: { coach_verbosity: "normal", default_mode: defaultMode, maia_model: "5m", monthly_cost_ceiling: 10 },
        monthly_cost: 0,
        budget_alert: false,
        recent_games: [],
      };
    },
    saveSettings: async () => Promise.reject(new Error("unused")),
  };
}

function renderPlay(api: LobbyApi, settings: SettingsApi) {
  render(
    <LobbyApiProvider api={api}>
      <SettingsApiProvider api={settings}>
        <PlayButton />
      </SettingsApiProvider>
    </LobbyApiProvider>,
  );
}

describe("PlayButton", () => {
  it("one tap starts a game with the last choice and opens it", async () => {
    const { api, created } = lobbyApi({ getLastChoice: async () => ({ mode: "active", color: "black", opponent_elo: 1450, time_control: null }) });
    renderPlay(api, settingsApi("passive"));
    await userEvent.setup().click(screen.getByRole("button", { name: "Play" }));

    await waitFor(() => expect(push).toHaveBeenCalledWith("/play/42"));
    expect(created).toEqual([{ mode: "active", color: "black", opponent_elo: 1450 }]);
  });

  it("falls back to the saved default mode, a random colour and the server's Elo", async () => {
    const { api, created } = lobbyApi();
    renderPlay(api, settingsApi("passive"));
    await userEvent.setup().click(screen.getByRole("button", { name: "Play" }));

    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(created).toEqual([{ mode: "passive", color: "random" }]);
  });

  it("falls back to Regular when neither the last choice nor settings load", async () => {
    const { api, created } = lobbyApi({ getLastChoice: async () => Promise.reject(new Error("down")) });
    renderPlay(api, settingsApi(null));
    await userEvent.setup().click(screen.getByRole("button", { name: "Play" }));

    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(created).toEqual([{ mode: "regular", color: "random" }]);
  });

  it("shows the API error and lets the player try again", async () => {
    const { api } = lobbyApi({ createGame: async () => Promise.reject(new ApiError(503, "Opponent engine is down")) });
    renderPlay(api, settingsApi("regular"));
    await userEvent.setup().click(screen.getByRole("button", { name: "Play" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Opponent engine is down");
    expect(push).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Play" })).toBeEnabled();
  });

  it("reports an unexpected failure generically", async () => {
    const { api } = lobbyApi({ createGame: async () => Promise.reject(new TypeError("network")) });
    renderPlay(api, settingsApi("regular"));
    await userEvent.setup().click(screen.getByRole("button", { name: "Play" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not start the game");
  });
});

const LAST_GAME: LastGame = {
  game_id: 9,
  mode: "passive",
  player_color: "black",
  opponent_elo: 1300,
  result: "0-1",
  accuracy: 82.4,
  summary: "You held the centre.",
};

function renderLastGame(game: LastGame | null | "error") {
  const { api } = lobbyApi({ getLastGame: async () => (game === "error" ? Promise.reject(new Error("down")) : game) });
  return render(
    <LobbyApiProvider api={api}>
      <LastGameCard />
    </LobbyApiProvider>,
  );
}

describe("LastGameCard", () => {
  it("recaps the result, accuracy and summary with a link to the review", async () => {
    renderLastGame(LAST_GAME);

    expect(await screen.findByText("Win")).toBeInTheDocument();
    expect(screen.getByText("Accuracy 82%")).toBeInTheDocument();
    expect(screen.getByText("You held the centre.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review game" })).toHaveAttribute("href", "/review/9");
  });

  it.each([
    ["1-0", "white", "Win"],
    ["1-0", "black", "Loss"],
    ["0-1", "white", "Loss"],
    ["1/2-1/2", "white", "Draw"],
    [null, "white", "Game over"],
  ] as const)("reads %s as %s for %s", async (result, color, expected) => {
    renderLastGame({ ...LAST_GAME, result, player_color: color });
    expect(await screen.findByText(expected)).toBeInTheDocument();
  });

  it("omits accuracy and summary when the game has none", async () => {
    renderLastGame({ ...LAST_GAME, accuracy: null, summary: null });

    await screen.findByText("Win");
    expect(screen.queryByText(/Accuracy/)).not.toBeInTheDocument();
    expect(screen.queryByText("You held the centre.")).not.toBeInTheDocument();
  });

  it.each([null, "error"] as const)("renders nothing for %s", async (game) => {
    renderLastGame(game);
    await waitFor(() => expect(screen.queryByText("Last game")).not.toBeInTheDocument());
  });
});

function concept(name: string, state: ConceptSummary["state"], updated: string, themes: string[] = []): ConceptSummary {
  return { concept: name, label: name, state, count: 3, recency_score: 1, practice_themes: themes, updated_at: updated };
}

function renderHighlights(concepts: ConceptSummary[] | "error") {
  const api: ProgressApi = {
    getPlayer: async () => ({ rating: 800, rating_deviation: 100 }),
    getRatingHistory: async () => [],
    getConcepts: async () => (concepts === "error" ? Promise.reject(new Error("down")) : concepts),
  };
  return render(
    <ProgressApiProvider api={api}>
      <ConceptHighlights />
    </ProgressApiProvider>,
  );
}

describe("ConceptHighlights", () => {
  it("shows the top focus concept with its first practice link", async () => {
    renderHighlights([
      concept("back_rank", "focus", "2026-11-20T00:00:00Z", ["backRankMate", "mateIn1"]),
      concept("missed_fork", "focus", "2026-11-21T00:00:00Z", ["fork"]),
    ]);

    const focus = await screen.findByRole("region", { name: "Today's focus" });
    expect(focus).toHaveTextContent("back_rank");
    expect(focus).not.toHaveTextContent("missed_fork");
    expect(screen.getByRole("link", { name: /Practice Back Rank Mate/ })).toHaveAttribute(
      "href",
      "https://lichess.org/training/backRankMate",
    );
  });

  it("follows the current concept states: with no focus concept it invites more games", async () => {
    renderHighlights([concept("back_rank", "recurring", "2026-11-20T00:00:00Z", ["backRankMate"])]);

    expect(await screen.findByText(/Play a few games/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Practice/ })).not.toBeInTheDocument();
  });

  it("lists the three most recently improved or mastered concepts, newest first", async () => {
    renderHighlights([
      concept("a", "improving", "2026-11-01T00:00:00Z"),
      concept("b", "mastered", "2026-11-04T00:00:00Z"),
      concept("c", "improving", "2026-11-03T00:00:00Z"),
      concept("d", "improving", "2026-11-02T00:00:00Z"),
      concept("e", "recurring", "2026-11-05T00:00:00Z"),
    ]);

    const section = await screen.findByRole("region", { name: "Recent improvements" });
    const items = Array.from(section.querySelectorAll("li")).map((item) => item.textContent);
    expect(items).toEqual(["bMastered", "cImproving", "dImproving"]);
  });

  it("hides recent improvements when there are none and renders nothing on failure", async () => {
    const { unmount } = renderHighlights([concept("a", "focus", "2026-11-01T00:00:00Z")]);
    await screen.findByRole("region", { name: "Today's focus" });
    expect(screen.queryByRole("region", { name: "Recent improvements" })).not.toBeInTheDocument();
    unmount();

    renderHighlights("error");
    await waitFor(() => expect(screen.queryByRole("region", { name: "Today's focus" })).not.toBeInTheDocument());
  });

  it("shows a focus concept without practice themes and no link", async () => {
    renderHighlights([concept("a", "focus", "2026-11-01T00:00:00Z")]);

    await screen.findByRole("region", { name: "Today's focus" });
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("PlayButton time control", () => {
  it("replays the last time control in Regular mode", async () => {
    const { api, created } = lobbyApi({
      getLastChoice: async () => ({ mode: "regular", color: "white", opponent_elo: 1200, time_control: "10+5" }),
    });
    renderPlay(api, settingsApi("regular"));
    await userEvent.setup().click(screen.getByRole("button", { name: "Play" }));

    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(created).toEqual([{ mode: "regular", color: "white", opponent_elo: 1200, time_control: "10+5" }]);
  });

  it("keeps tutor modes untimed", async () => {
    const { api, created } = lobbyApi({
      getLastChoice: async () => ({ mode: "passive", color: "white", opponent_elo: 1200, time_control: "10+5" }),
    });
    renderPlay(api, settingsApi("regular"));
    await userEvent.setup().click(screen.getByRole("button", { name: "Play" }));

    await waitFor(() => expect(push).toHaveBeenCalled());
    expect(created[0]).not.toHaveProperty("time_control");
  });
});
