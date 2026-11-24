import { API_BASE_URL } from "@/lib/api";
import type { GameMode } from "@/lib/game-api";

export type CoachVerbosity = "brief" | "normal" | "detailed";
export type MaiaModel = "3m" | "5m" | "23m" | "79m";

export interface Preferences {
  coach_verbosity: CoachVerbosity;
  default_mode: GameMode;
  maia_model: MaiaModel;
  monthly_cost_ceiling: number;
}

export interface GameCost {
  game_id: number;
  started_at: string;
  llm_cost: number;
}

export interface SettingsState {
  preferences: Preferences;
  monthly_cost: number;
  budget_alert: boolean;
  recent_games: GameCost[];
}

/** What the settings screen needs from the backend; tests supply their own implementation. */
export interface SettingsApi {
  getSettings(): Promise<SettingsState>;
  saveSettings(preferences: Preferences): Promise<SettingsState>;
}

async function request(init?: RequestInit): Promise<SettingsState> {
  const response = await fetch(`${API_BASE_URL}/settings`, { cache: "no-store", ...init });
  if (!response.ok) throw new Error(`/settings failed: ${response.status}`);
  return (await response.json()) as SettingsState;
}

export const httpSettingsApi: SettingsApi = {
  getSettings: () => request(),
  saveSettings: (preferences) =>
    request({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(preferences),
    }),
};

export const formatDollars = (amount: number) => `$${amount.toFixed(2)}`;
