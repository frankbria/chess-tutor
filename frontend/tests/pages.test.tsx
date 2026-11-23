import { render, screen } from "@testing-library/react";
import { prerender } from "react-dom/static";
import type { ReactElement } from "react";
import { describe, expect, it } from "vitest";

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
    render(<HomePage />);
    expect(screen.getByRole("heading", { level: 1, name: "Chess Tutor" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play" })).toBeDisabled();
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
      <ReviewPage params={Promise.resolve({ id: "game-42" })} searchParams={Promise.resolve({})} />,
    );
    expect(html).toMatch(/<h1[^>]*>Review <!-- -->game-42<\/h1>/);
  });

  it("progress and settings render their headings", () => {
    render(<ProgressPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Progress" })).toBeInTheDocument();
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  });
});
