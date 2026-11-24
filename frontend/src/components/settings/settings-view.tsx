"use client";

import { Alert01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import type { GameMode } from "@/lib/game-api";
import {
  formatDollars,
  type CoachVerbosity,
  type MaiaModel,
  type Preferences,
  type SettingsState,
} from "@/lib/settings-api";

import { useSettingsApi } from "./settings-api-context";

const FIELD_CLASS =
  "h-9 w-full rounded-md border border-input bg-background px-3 text-sm outline-none transition-all focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50";

const VERBOSITY: CoachVerbosity[] = ["brief", "normal", "detailed"];
const MODES: { value: GameMode; label: string }[] = [
  { value: "regular", label: "Regular" },
  { value: "passive", label: "Passive tutor" },
  { value: "active", label: "Active tutor" },
];
const MAIA_MODELS: MaiaModel[] = ["3m", "5m", "23m", "79m"];

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1.5 text-sm font-medium">
      {label}
      {children}
    </label>
  );
}

export function SettingsView() {
  const api = useSettingsApi();
  const [state, setState] = useState<SettingsState | null>(null);
  const [draft, setDraft] = useState<Preferences | null>(null);
  const [ceilingText, setCeilingText] = useState("");
  const [failed, setFailed] = useState(false);
  const [saveStatus, setSaveStatus] = useState<"idle" | "saved" | "error">("idle");

  useEffect(() => {
    let current = true;
    api
      .getSettings()
      .then((loaded) => {
        if (!current) return;
        setState(loaded);
        setDraft(loaded.preferences);
        setCeilingText(String(loaded.preferences.monthly_cost_ceiling));
      })
      .catch(() => current && setFailed(true));
    return () => {
      current = false;
    };
  }, [api]);

  if (failed) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load your settings.
      </p>
    );
  }
  if (state === null || draft === null)
    return <p className="text-sm text-muted-foreground">Loading…</p>;

  const ceiling = Number(ceilingText);
  const ceilingValid = ceilingText.trim() !== "" && Number.isFinite(ceiling) && ceiling > 0;

  async function save(event: FormEvent) {
    event.preventDefault();
    if (draft === null || !ceilingValid) return;
    try {
      const saved = await api.saveSettings({ ...draft, monthly_cost_ceiling: ceiling });
      setState(saved);
      setDraft(saved.preferences);
      setSaveStatus("saved");
    } catch {
      setSaveStatus("error");
    }
  }

  function change(update: Partial<Preferences>) {
    setDraft({ ...draft!, ...update });
    setSaveStatus("idle");
  }

  const percentUsed = Math.round(
    (state.monthly_cost / state.preferences.monthly_cost_ceiling) * 100,
  );

  return (
    <>
      <section aria-labelledby="cost-heading" className="grid gap-3">
        <h2 id="cost-heading" className="text-lg font-semibold">
          Coach spending
        </h2>
        {state.budget_alert && (
          <p
            role="alert"
            className="flex items-center gap-2 rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm font-medium text-amber-900 dark:text-amber-200"
          >
            <HugeiconsIcon icon={Alert01Icon} size={18} aria-hidden />
            You have used {percentUsed}% of your monthly coach budget.
          </p>
        )}
        <p className="text-3xl font-bold">
          {formatDollars(state.monthly_cost)}{" "}
          <span className="text-base font-normal text-muted-foreground">
            this month of {formatDollars(state.preferences.monthly_cost_ceiling)}
          </span>
        </p>
        {state.recent_games.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Per-game coach costs will appear after your first game.
          </p>
        ) : (
          <ul className="grid gap-1 text-sm" aria-label="Cost per game">
            {state.recent_games.map((game) => (
              <li key={game.game_id} className="flex justify-between border-b py-1">
                <span>
                  Game {game.game_id} ·{" "}
                  {new Date(game.started_at).toLocaleDateString("en-US", {
                    month: "short",
                    day: "numeric",
                    timeZone: "UTC",
                  })}
                </span>
                <span className="font-medium">{formatDollars(game.llm_cost)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <form onSubmit={save} aria-labelledby="preferences-heading" className="grid max-w-md gap-4">
        <h2 id="preferences-heading" className="text-lg font-semibold">
          Preferences
        </h2>
        <Field label="Coach verbosity">
          <select
            className={FIELD_CLASS}
            value={draft.coach_verbosity}
            onChange={(event) => change({ coach_verbosity: event.target.value as CoachVerbosity })}
          >
            {VERBOSITY.map((value) => (
              <option key={value} value={value}>
                {value[0].toUpperCase() + value.slice(1)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Default mode">
          <select
            className={FIELD_CLASS}
            value={draft.default_mode}
            onChange={(event) => change({ default_mode: event.target.value as GameMode })}
          >
            {MODES.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Maia model size (applies after server restart)">
          <select
            className={FIELD_CLASS}
            value={draft.maia_model}
            onChange={(event) => change({ maia_model: event.target.value as MaiaModel })}
          >
            {MAIA_MODELS.map((value) => (
              <option key={value} value={value}>
                {value.toUpperCase()}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Monthly coach budget (USD)">
          <input
            className={FIELD_CLASS}
            type="number"
            min="0.01"
            step="0.01"
            value={ceilingText}
            aria-invalid={!ceilingValid}
            onChange={(event) => {
              setCeilingText(event.target.value);
              setSaveStatus("idle");
            }}
          />
        </Field>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!ceilingValid}>
            Save settings
          </Button>
          {saveStatus === "saved" && <p role="status" className="text-sm">Settings saved.</p>}
          {saveStatus === "error" && (
            <p role="alert" className="text-sm text-destructive">
              Could not save your settings.
            </p>
          )}
        </div>
      </form>
    </>
  );
}
