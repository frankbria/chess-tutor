import { afterEach, describe, expect, it, vi } from "vitest";

describe("API_BASE_URL", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("reads NEXT_PUBLIC_API_BASE_URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://127.0.0.1:9123");
    const { API_BASE_URL } = await import("@/lib/api");
    expect(API_BASE_URL).toBe("http://127.0.0.1:9123");
  });

  it("defaults to the local backend", async () => {
    delete process.env.NEXT_PUBLIC_API_BASE_URL;
    const { API_BASE_URL } = await import("@/lib/api");
    expect(API_BASE_URL).toBe("http://127.0.0.1:8000");
  });
});
