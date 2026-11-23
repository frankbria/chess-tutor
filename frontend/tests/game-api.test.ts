import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

let server: Server;
let httpGameApi: typeof import("@/lib/game-api").httpGameApi;
let httpLobbyApi: typeof import("@/lib/game-api").httpLobbyApi;
let ApiError: typeof import("@/lib/game-api").ApiError;
const requests: { method?: string; url?: string; body: string }[] = [];

beforeAll(async () => {
  server = createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => (body += chunk));
    request.on("end", () => {
      requests.push({ method: request.method, url: request.url, body });
      response.setHeader("Content-Type", "application/json");
      if (request.url === "/games/7/moves") {
        response.statusCode = 409;
        response.end(JSON.stringify({ detail: "it is not your turn" }));
      } else if (request.url === "/games/8") {
        response.statusCode = 503;
        response.end("not json");
      } else {
        response.end(JSON.stringify({ id: 7, moves: [] }));
      }
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", `http://127.0.0.1:${(server.address() as AddressInfo).port}`);
  ({ httpGameApi, httpLobbyApi, ApiError } = await import("@/lib/game-api"));
});

afterAll(() => {
  server.close();
  vi.unstubAllEnvs();
});

describe("httpGameApi", () => {
  it("fetches a game and posts moves as JSON", async () => {
    expect((await httpGameApi.getGame(7)).id).toBe(7);
    await httpGameApi.resign(7);
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual(["GET /games/7", "POST /games/7/resign"]);
  });

  it("throws an ApiError carrying the status and server detail", async () => {
    const error = await httpGameApi.playMove(7, "e2e4").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, message: "it is not your turn" });
    expect(requests.at(-1)?.body).toBe('{"uci":"e2e4"}');
  });

  it("falls back to the status text when the body is not JSON", async () => {
    await expect(httpGameApi.getGame(8)).rejects.toMatchObject({ status: 503 });
  });
});

describe("httpLobbyApi", () => {
  it("creates a game with a JSON body and lists active games", async () => {
    await httpLobbyApi.createGame({ mode: "active", color: "random", opponent_elo: 1400 });
    const post = requests.at(-1);
    expect([post?.method, post?.url]).toEqual(["POST", "/games"]);
    expect(JSON.parse(post?.body ?? "")).toEqual({ mode: "active", color: "random", opponent_elo: 1400 });
    await httpLobbyApi.listActiveGames();
    expect(requests.at(-1)?.url).toBe("/games?status=active");
    await httpLobbyApi.getPlayer();
    expect(requests.at(-1)?.url).toBe("/player");
  });
});
