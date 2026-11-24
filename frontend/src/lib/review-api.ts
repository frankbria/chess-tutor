import { httpGameApi, request, type GameApi } from "@/lib/game-api";

export interface KeyMoment {
  ply: number;
  move_number: number;
  san: string;
  uci: string;
  fen_before: string;
  classification: string;
  win_probability_before: number | null;
  win_probability_after: number | null;
  best_move_uci: string;
  best_move_san: string;
}

export interface Review {
  accuracy: number | null;
  performance_rating: number | null;
  /** The player's win probability after each scored move. */
  win_probabilities: { ply: number; win_probability: number }[];
  key_moments: KeyMoment[];
  reasoning: { ply: number; answer: string; coach_reply: string | null }[];
  summary: { summary: string; key_moments: string[]; concepts: string[]; source: string } | null;
  concepts: { ply: number; concept: string; source: string }[];
}

/** What the review screen needs from the backend; tests supply their own implementation. */
export interface ReviewApi extends Pick<GameApi, "getGame" | "getCoachMessages"> {
  getReview(id: number): Promise<Review>;
  /** Asks the coach about the position after `ply`; best moves may be revealed in the answer. */
  askAboutPosition(id: number, ply: number, text: string): Promise<{ text: string; source: string }>;
}

export const httpReviewApi: ReviewApi = {
  getGame: httpGameApi.getGame,
  getCoachMessages: httpGameApi.getCoachMessages,
  getReview: (id) => request<Review>(`/games/${id}/review`, "GET"),
  askAboutPosition: (id, ply, text) =>
    request<{ text: string; source: string }>(`/games/${id}/review/questions`, "POST", { ply, text }),
};
