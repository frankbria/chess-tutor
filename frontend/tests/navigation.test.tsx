import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppNavigation, CurrentAppNavigation } from "@/components/app-navigation";

describe("AppNavigation", () => {
  it("links to every top-level screen", () => {
    render(<AppNavigation currentPath="/" />);

    expect(screen.getByRole("link", { name: "Home" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Progress" })).toHaveAttribute("href", "/progress");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("marks only the current screen as the current page", () => {
    render(<AppNavigation currentPath="/progress" />);

    expect(screen.getByRole("link", { name: "Progress" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
  });

  it("renders from the router's current path", () => {
    render(<CurrentAppNavigation />);

    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });
});
