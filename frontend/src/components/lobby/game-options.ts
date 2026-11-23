import type { ColorChoice, GameMode } from "@/lib/game-api";

export const MODES: { value: GameMode; label: string; description: string }[] = [
  { value: "regular", label: "Regular", description: "Just play. No coaching during the game." },
  { value: "passive", label: "Passive tutor", description: "The coach watches and reviews with you afterwards." },
  { value: "active", label: "Active tutor", description: "The coach speaks up when you miss something." },
];

export const COLORS: { value: ColorChoice; label: string }[] = [
  { value: "white", label: "White" },
  { value: "black", label: "Black" },
  { value: "random", label: "Random" },
];

export const MINIMUM_ELO = 400;
export const MAXIMUM_ELO = 3000;
export const ELO_STEP = 50;
export const FALLBACK_ELO = 1200;

export function defaultElo(rating: number): number {
  const rounded = Math.round(rating / ELO_STEP) * ELO_STEP;
  return Math.min(MAXIMUM_ELO, Math.max(MINIMUM_ELO, rounded));
}

export function labelFor<T extends string>(options: { value: T; label: string }[], value: T): string {
  return options.find((option) => option.value === value)?.label ?? value;
}
