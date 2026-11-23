import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SettingsApiProvider } from "@/components/settings/settings-api-context";
import { SettingsView } from "@/components/settings/settings-view";
import type { Preferences, SettingsApi, SettingsState } from "@/lib/settings-api";

const PREFERENCES: Preferences = {
  coach_verbosity: "normal",
  default_mode: "passive",
  maia_model: "5m",
  monthly_cost_ceiling: 10,
};

function stateWith(monthlyCost: number, preferences = PREFERENCES): SettingsState {
  return {
    preferences,
    monthly_cost: monthlyCost,
    budget_alert: monthlyCost >= 0.8 * preferences.monthly_cost_ceiling,
    recent_games: [{ game_id: 7, started_at: "2026-11-02T10:00:00Z", llm_cost: 0.1234 }],
  };
}

function renderView(initial: SettingsState, saved: Preferences[] = [], failSave = false) {
  const api: SettingsApi = {
    getSettings: async () => initial,
    saveSettings: async (preferences) => {
      if (failSave) throw new Error("boom");
      saved.push(preferences);
      return stateWith(initial.monthly_cost, preferences);
    },
  };
  return render(
    <SettingsApiProvider api={api}>
      <SettingsView />
    </SettingsApiProvider>,
  );
}

describe("SettingsView", () => {
  it("shows monthly and per-game cost without an alert under 80%", async () => {
    renderView(stateWith(7.99));

    expect(await screen.findByText(/\$7\.99/)).toBeInTheDocument();
    expect(screen.getByText("$0.12")).toBeInTheDocument();
    expect(screen.getByText(/Game 7/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("alerts at 80% of the ceiling", async () => {
    renderView(stateWith(8));

    expect(await screen.findByRole("alert")).toHaveTextContent("80%");
  });

  it("saves the changed preferences", async () => {
    const saved: Preferences[] = [];
    renderView(stateWith(1), saved);
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByLabelText(/Coach verbosity/), "brief");
    await user.selectOptions(screen.getByLabelText("Default mode"), "active");
    await user.selectOptions(screen.getByLabelText(/Maia model size/), "23m");
    await user.clear(screen.getByLabelText(/Monthly coach budget/));
    await user.type(screen.getByLabelText(/Monthly coach budget/), "4.5");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Settings saved.");
    expect(saved).toEqual([
      {
        coach_verbosity: "brief",
        default_mode: "active",
        maia_model: "23m",
        monthly_cost_ceiling: 4.5,
      },
    ]);
  });

  it("blocks saving a ceiling that is not a positive number", async () => {
    const saved: Preferences[] = [];
    renderView(stateWith(1), saved);
    const user = userEvent.setup();

    const field = await screen.findByLabelText(/Monthly coach budget/);
    await user.clear(field);
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();

    await user.type(field, "0");
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    expect(saved).toEqual([]);
  });

  it("reports a failed save", async () => {
    renderView(stateWith(1), [], true);
    const user = userEvent.setup();

    await user.click(await screen.findByRole("button", { name: "Save settings" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("Could not save your settings."),
    );
  });

  it("reports a failed load", async () => {
    render(
      <SettingsApiProvider
        api={{
          getSettings: async () => Promise.reject(new Error("down")),
          saveSettings: async () => Promise.reject(new Error("unused")),
        }}
      >
        <SettingsView />
      </SettingsApiProvider>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load your settings.");
  });
});
