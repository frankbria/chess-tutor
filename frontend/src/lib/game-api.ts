import { API_BASE_URL } from "@/lib/api";

export type PlayerColor = "white" | "black";
export type GameMode = "regular" | "passive" | "active";
export type ColorChoice = PlayerColor | "random";
export type GameResult = "1-0" | "0-1" | "1/2-1/2";

export interface ClockState {
  white_ms: number;
  black_ms: number;
  /** The side whose clock is ticking; null once the game is over. */
  running: PlayerColor | null;
}

export interface GameState {
  id: number;
  mode: GameMode;
  player_color: PlayerColor;
  opponent_elo: number;
  status: "active" | "finished";
  result: GameResult | null;
  fen: string;
  moves: string[];
  side_to_move: PlayerColor;
  legal_moves: string[];
  time_control: string | null;
  clock: ClockState | null;
  ended_on_time: boolean;
}

export type GamePhase = "opening" | "middlegame" | "endgame";

export type CoachMessageKind = "comment" | "question" | "hint" | "answer";

export interface CoachMessage {
  /** The 1-based ply of the move this message refers to. */
  ply: number;
  role: "coach" | "player";
  kind: CoachMessageKind;
  text: string;
  /** The move's label (e.g. "blunder"); null if it was not scored. */
  classification: string | null;
  /** The phase of the position before the move. */
  phase: GamePhase;
}

export interface GameStreamHandlers {
  onCoachComment: (event: { ply: number; text: string; classification: string; phase: GamePhase }) => void;
  onSnapshot: (event: { fen: string; status: string }) => void;
  onOpponentMove: (event: { uci: string; san: string; fen: string; clock: ClockState | null }) => void;
  onGameOver: (event: { result: GameResult; reason: string }) => void;
  /** EventSource reconnects by itself; the next event means the stream is back. */
  onConnectionLost: () => void;
}

/** What the play screen needs from the backend; tests supply their own implementation. */
export interface GameApi {
  getGame(id: number): Promise<GameState>;
  playMove(id: number, uci: string): Promise<GameState>;
  resign(id: number): Promise<GameState>;
  getCoachMessages(id: number): Promise<CoachMessage[]>;
  /** Passive games only: undoes the player's last move and the opponent's reply. */
  takeback(id: number): Promise<GameState>;
  /** Returns the unsubscribe function. */
  subscribe(id: number, handlers: GameStreamHandlers): () => void;
}

export interface NewGame {
  mode: GameMode;
  color: ColorChoice;
  /** Omitted: the server picks one from the player's rating (plus a stretch in tutor modes). */
  opponent_elo?: number;
  /** Regular mode only; omitted for an untimed game. */
  time_control?: string;
}

/** The player's previous new-game choice; each field is null until one has been made. */
export interface LastChoice {
  mode: GameMode | null;
  color: ColorChoice | null;
  opponent_elo: number | null;
  time_control: string | null;
}

export interface LastGame {
  game_id: number;
  mode: GameMode;
  player_color: PlayerColor;
  opponent_elo: number;
  result: GameResult | null;
  accuracy: number | null;
  summary: string | null;
}

export interface Player {
  rating: number;
}

/** What the home screen needs from the backend: starting and resuming games. */
export interface LobbyApi {
  createGame(newGame: NewGame): Promise<GameState>;
  listActiveGames(): Promise<GameState[]>;
  getPlayer(): Promise<Player>;
  getLastChoice(): Promise<LastChoice>;
  /** Null until the player has finished a game. */
  getLastGame(): Promise<LastGame | null>;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T = GameState>(path: string, method: "GET" | "POST", body?: object): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    cache: "no-store",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    const detail =
      payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
        ? payload.detail
        : response.statusText;
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

/** A malformed event is dropped so one bad payload cannot break the stream. */
export function parseStreamEvent<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    console.error("Ignoring malformed game event", data);
    return null;
  }
}

export const httpGameApi: GameApi = {
  getGame: (id) => request(`/games/${id}`, "GET"),
  playMove: (id, uci) => request(`/games/${id}/moves`, "POST", { uci }),
  resign: (id) => request(`/games/${id}/resign`, "POST"),
  getCoachMessages: (id) => request<CoachMessage[]>(`/games/${id}/coach/messages`, "GET"),
  takeback: (id) => request(`/games/${id}/takeback`, "POST"),
  subscribe(id, handlers) {
    const source = new EventSource(`${API_BASE_URL}/games/${id}/coach/stream`);
    const listen = <T,>(name: string, handle: (event: T) => void) =>
      source.addEventListener(name, (message) => {
        const event = parseStreamEvent<T>((message as MessageEvent<string>).data);
        if (event) handle(event);
      });
    source.onerror = handlers.onConnectionLost;
    listen("coach_comment", handlers.onCoachComment);
    listen("snapshot", handlers.onSnapshot);
    listen("opponent_move", handlers.onOpponentMove);
    listen("game_over", (event: { result: GameResult; reason: string }) => {
      handlers.onGameOver(event);
      source.close();
    });
    return () => source.close();
  },
};

export const httpLobbyApi: LobbyApi = {
  createGame: (newGame) => request("/games", "POST", newGame),
  listActiveGames: () => request<GameState[]>("/games?status=active", "GET"),
  getPlayer: () => request<Player>("/player", "GET"),
  getLastChoice: () => request<LastChoice>("/player/last-choice", "GET"),
  getLastGame: () => request<LastGame | null>("/player/last-game", "GET"),
};
