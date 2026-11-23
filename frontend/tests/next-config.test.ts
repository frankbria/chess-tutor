import { afterEach, describe, expect, it, vi } from "vitest";

async function loadConfig() {
  vi.resetModules();
  return (await import("../next.config")).default;
}

describe("next.config", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("proxies /api to the local backend by default", async () => {
    vi.stubEnv("BACKEND_INTERNAL_URL", undefined);
    const config = await loadConfig();
    expect(await config.rewrites?.()).toEqual([{ source: "/api/:path*", destination: "http://127.0.0.1:8000/:path*" }]);
  });

  it("proxies /api to BACKEND_INTERNAL_URL", async () => {
    vi.stubEnv("BACKEND_INTERNAL_URL", "http://127.0.0.1:9123");
    const config = await loadConfig();
    expect(await config.rewrites?.()).toEqual([{ source: "/api/:path*", destination: "http://127.0.0.1:9123/:path*" }]);
  });

  it("allows the dev server to be reached from PUBLIC_HOST", async () => {
    vi.stubEnv("PUBLIC_HOST", "192.168.1.20");
    expect((await loadConfig()).allowedDevOrigins).toEqual(["192.168.1.20"]);
  });

  it("allows no extra dev origins without PUBLIC_HOST", async () => {
    vi.stubEnv("PUBLIC_HOST", "");
    expect((await loadConfig()).allowedDevOrigins).toEqual([]);
  });
});
