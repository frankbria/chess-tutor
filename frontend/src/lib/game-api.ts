import { API_BASE_URL } from "@/lib/api";

export type PlayerColor = "white" | "black";
export type GameResult = "1-0" | "0-1" | "1/2-1/2";

export interface ClockState {
  white_ms: number;
  black_ms: number;
  /** The side whose clock is ticking; null once the game is over. */
  running: PlayerColor | null;
}

export interface GameState {
  id: number;
  mode: string;
  player_color: PlayerColor;
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

export interface GameStreamHandlers {
  onSnapshot: (event: { fen: string; status: string }) => void;
  onOpponentMove: (event: { uci: string; san: string; fen: string }) => void;
  onGameOver: (event: { result: GameResult; reason: string }) => void;
  /** EventSource reconnects by itself; the next event means the stream is back. */
  onConnectionLost: () => void;
}

/** What the play screen needs from the backend; tests supply their own implementation. */
export interface GameApi {
  getGame(id: number): Promise<GameState>;
  playMove(id: number, uci: string): Promise<GameState>;
  resign(id: number): Promise<GameState>;
  /** Returns the unsubscribe function. */
  subscribe(id: number, handlers: GameStreamHandlers): () => void;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request(path: string, method: "GET" | "POST", body?: object): Promise<GameState> {
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
  return (await response.json()) as GameState;
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
  subscribe(id, handlers) {
    const source = new EventSource(`${API_BASE_URL}/games/${id}/coach/stream`);
    const listen = <T,>(name: string, handle: (event: T) => void) =>
      source.addEventListener(name, (message) => {
        const event = parseStreamEvent<T>((message as MessageEvent<string>).data);
        if (event) handle(event);
      });
    source.onerror = handlers.onConnectionLost;
    listen("snapshot", handlers.onSnapshot);
    listen("opponent_move", handlers.onOpponentMove);
    listen("game_over", (event: { result: GameResult; reason: string }) => {
      handlers.onGameOver(event);
      source.close();
    });
    return () => source.close();
  },
};
