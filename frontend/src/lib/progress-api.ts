import { API_BASE_URL } from "@/lib/api";

export interface PlayerRating {
  rating: number;
  rating_deviation: number;
}

export interface RatingHistoryEntry {
  game_id: number;
  rating_before: number;
  rating_after: number;
  deviation_before: number;
  deviation_after: number;
  weight: number;
  created_at: string;
  accuracy: number | null;
  performance_rating: number | null;
}

export type ConceptStatus =
  | "focus"
  | "recurring"
  | "improving"
  | "new"
  | "mastered";

export interface ConceptSummary {
  concept: string;
  label: string;
  state: ConceptStatus;
  count: number;
  recency_score: number;
  practice_themes: string[];
  /** When the state last changed. */
  updated_at: string;
}

/** What the progress screen needs from the backend; tests supply their own implementation. */
export interface ProgressApi {
  getPlayer(): Promise<PlayerRating>;
  getRatingHistory(): Promise<RatingHistoryEntry[]>;
  getConcepts(): Promise<ConceptSummary[]>;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} failed: ${response.status}`);
  return (await response.json()) as T;
}

export const httpProgressApi: ProgressApi = {
  getPlayer: () => get("/player"),
  getRatingHistory: () => get("/player/rating-history"),
  getConcepts: () => get("/player/concepts"),
};

const TREND_WINDOW = 5;

/** Screen-reader summary, e.g. "Rating 812, up 12 over the last 5 games". */
export function describeTrend(
  history: RatingHistoryEntry[],
  rating: number,
): string {
  const recent = history.slice(-TREND_WINDOW);
  if (recent.length === 0) return `Rating ${rating}`;
  const change =
    Math.round(recent[recent.length - 1].rating_after) -
    Math.round(recent[0].rating_before);
  const span =
    recent.length === 1 ? "the last game" : `the last ${recent.length} games`;
  if (change === 0) return `Rating ${rating}, unchanged over ${span}`;
  return `Rating ${rating}, ${change > 0 ? "up" : "down"} ${Math.abs(change)} over ${span}`;
}
