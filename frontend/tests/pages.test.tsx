import { cleanup, render, screen } from "@testing-library/react";
import { prerender } from "react-dom/static";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (importOriginal) => ({
  ...(await importOriginal<typeof import("next/navigation")>()),
  useRouter: () => ({ push: vi.fn() }),
}));

import { LobbyApiProvider } from "@/components/lobby/lobby-api-context";
import HomePage from "@/app/page";
import PlayPage from "@/app/play/[id]/page";
import ProgressPage from "@/app/progress/page";
import ReviewPage from "@/app/review/[id]/page";
import SettingsPage from "@/app/settings/page";

// Dynamic pages are server components that suspend on `params`; prerendering waits for them.
async function prerenderToHtml(page: ReactElement): Promise<string> {
  const { prelude } = await prerender(page);
  return new Response(prelude).text();
}

describe("route placeholders", () => {
  it("home offers a Play action", () => {
    render(
      <LobbyApiProvider
        api={{ createGame: async () => Promise.reject(new Error("unused")), listActiveGames: async () => [], getPlayer: async () => ({ rating: 1200 }), getLastChoice: async () => ({ mode: null, color: null, opponent_elo: null, time_control: null }), getLastGame: async () => null }}
      >
        <HomePage />
      </LobbyApiProvider>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Chess Tutor" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play" })).toBeEnabled();
  });

  it("play shows the game id", async () => {
    const html = await prerenderToHtml(
      <PlayPage params={Promise.resolve({ id: "42" })} searchParams={Promise.resolve({})} />,
    );
    expect(html).toMatch(/<h1[^>]*>Game <!-- -->42<\/h1>/);
  });

  it("play rejects a non-numeric id with notFound", async () => {
    const html = await prerenderToHtml(
      <PlayPage params={Promise.resolve({ id: "abc" })} searchParams={Promise.resolve({})} />,
    );
    expect(html).toContain("NEXT_HTTP_ERROR_FALLBACK;404");
  });

  it("review shows the game id", async () => {
    const html = await prerenderToHtml(
      <ReviewPage params={Promise.resolve({ id: "42" })} searchParams={Promise.resolve({})} />,
    );
    expect(html).toMatch(/<h1[^>]*>Review <!-- -->42<\/h1>/);
  });

  it("review rejects a non-numeric id with notFound", async () => {
    const html = await prerenderToHtml(
      <ReviewPage params={Promise.resolve({ id: "abc" })} searchParams={Promise.resolve({})} />,
    );
    expect(html).toContain("NEXT_HTTP_ERROR_FALLBACK;404");
  });

  it("progress and settings render their headings", () => {
    render(<ProgressPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Progress" })).toBeInTheDocument();
    cleanup();
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  });
});
