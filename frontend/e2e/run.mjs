// Boots the real backend and frontend on OS-assigned ports with a throwaway database, runs the
// Playwright suite against them, then stops both. LLM calls stay in template-fallback mode unless
// E2E_LLM=1 (opt-in, uses the operator's real credentials).
import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const frontendDir = resolve(import.meta.dirname, "..");
const backendDir = resolve(frontendDir, "../backend");
const host = "127.0.0.1";
const useRealLlm = process.env.E2E_LLM === "1";

function freePort() {
  return new Promise((done, fail) => {
    const probe = createServer();
    probe.once("error", fail);
    probe.listen(0, host, () => {
      const { port } = probe.address();
      probe.close(() => done(port));
    });
  });
}

async function waitFor(url, label, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // not listening yet
    }
    await new Promise((later) => setTimeout(later, 500));
  }
  throw new Error(`${label} did not become ready at ${url}`);
}

const [backendPort, frontendPort, unreachablePort] = [await freePort(), await freePort(), await freePort()];
const backendUrl = `http://${host}:${backendPort}`;
const frontendUrl = `http://${host}:${frontendPort}`;
const databaseDirectory = mkdtempSync(join(tmpdir(), "chess-tutor-e2e-"));

// With no model reachable the coach falls back to its templates. A throwaway key pointed at a
// closed local port keeps CI offline and stops a developer's own `ant auth login` being used.
const llmEnvironment = useRealLlm
  ? {}
  : {
      CHESS_TUTOR_LLM_API_KEY: "e2e-unused-key",
      ANTHROPIC_BASE_URL: `http://${host}:${unreachablePort}`,
    };

const backend = spawn(
  "uv",
  ["run", "uvicorn", "chess_tutor.main:create_app", "--factory", "--host", host, "--port", String(backendPort)],
  {
    cwd: backendDir,
    stdio: "inherit",
    detached: true, // its own process group, so stop() also reaches uvicorn's children
    env: {
      ...process.env,
      ...llmEnvironment,
      CHESS_TUTOR_DATABASE_PATH: join(databaseDirectory, "e2e.db"),
      CHESS_TUTOR_FRONTEND_ORIGINS: JSON.stringify([frontendUrl]),
      CHESS_TUTOR_OPPONENT_MIN_THINK_SECONDS: "0",
      CHESS_TUTOR_OPPONENT_MAX_THINK_SECONDS: "0.5",
      CHESS_TUTOR_THOUGHT_QUESTION_EVERY_N_MOVES: "1",
      ...(process.env.STOCKFISH_PATH ? { CHESS_TUTOR_STOCKFISH_PATH: process.env.STOCKFISH_PATH } : {}),
    },
  },
);

let frontend;
let exitCode = 1;
async function stop() {
  // Let uvicorn shut its engines down itself before the rest of its process group is swept.
  const backendExited = new Promise((done) => backend.once("exit", done));
  backend.kill("SIGINT");
  await Promise.race([backendExited, new Promise((later) => setTimeout(later, 10_000))]);
  for (const server of [backend, frontend]) {
    try {
      if (server?.pid) process.kill(-server.pid, "SIGTERM");
    } catch {
      // the group already exited
    }
  }
  rmSync(databaseDirectory, { recursive: true, force: true });
}
process.on("SIGINT", async () => {
  await stop();
  process.exit(130);
});

try {
  const frontendEnvironment = { ...process.env, NEXT_PUBLIC_API_BASE_URL: backendUrl };
  // The API address is inlined at build time, so every run builds for its own ports.
  const build = spawnSync("npx", ["next", "build"], { cwd: frontendDir, stdio: "inherit", env: frontendEnvironment });
  if (build.status !== 0) throw new Error("next build failed");
  frontend = spawn("npx", ["next", "start", "--hostname", host, "--port", String(frontendPort)], {
    cwd: frontendDir,
    stdio: "inherit",
    detached: true,
    env: frontendEnvironment,
  });
  await Promise.all([
    waitFor(`${backendUrl}/health`, "backend", 180_000),
    waitFor(frontendUrl, "frontend", 60_000),
  ]);
  const tests = spawnSync("npx", ["playwright", "test", "--config", "e2e/playwright.config.ts", ...process.argv.slice(2)], {
    cwd: frontendDir,
    stdio: "inherit",
    env: { ...process.env, E2E_BASE_URL: frontendUrl, E2E_API_URL: backendUrl, E2E_LLM: useRealLlm ? "1" : "" },
  });
  exitCode = tests.status ?? 1;
} catch (failure) {
  console.error(failure);
} finally {
  await stop();
}
process.exit(exitCode);
