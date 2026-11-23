import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ClockDisplay, formatClock } from "@/components/play/clock-display";

describe("ClockDisplay", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("formats minutes and seconds, rounding up", () => {
    expect(formatClock(600_000)).toBe("10:00");
    expect(formatClock(61_001)).toBe("1:02");
    expect(formatClock(0)).toBe("0:00");
  });

  it("counts down locally while running", () => {
    render(<ClockDisplay label="Your clock" remainingMs={300_000} running />);
    expect(screen.getByRole("timer", { name: "Your clock" })).toHaveTextContent("5:00");
    act(() => void vi.advanceTimersByTime(12_000));
    expect(screen.getByRole("timer")).toHaveTextContent("4:48");
  });

  it("holds still when its side is not running", () => {
    render(<ClockDisplay label="Opponent clock" remainingMs={300_000} running={false} />);
    act(() => void vi.advanceTimersByTime(12_000));
    expect(screen.getByRole("timer")).toHaveTextContent("5:00");
  });

  it("re-syncs to a new server value", () => {
    const { rerender } = render(<ClockDisplay label="c" remainingMs={300_000} running />);
    act(() => void vi.advanceTimersByTime(20_000));
    rerender(<ClockDisplay label="c" remainingMs={290_000} running />);
    expect(screen.getByRole("timer")).toHaveTextContent("4:50");
  });

  it("styles low time and stops at zero", () => {
    render(<ClockDisplay label="c" remainingMs={31_000} running />);
    expect(screen.getByRole("timer")).not.toHaveClass("text-destructive");
    act(() => void vi.advanceTimersByTime(2000));
    expect(screen.getByRole("timer")).toHaveClass("text-destructive");
    act(() => void vi.advanceTimersByTime(60_000));
    expect(screen.getByRole("timer")).toHaveTextContent("0:00");
  });
});
