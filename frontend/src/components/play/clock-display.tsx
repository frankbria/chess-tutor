"use client";

import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

const LOW_TIME_MS = 30_000;

export function formatClock(milliseconds: number): string {
  const totalSeconds = Math.ceil(milliseconds / 1000);
  const seconds = String(totalSeconds % 60).padStart(2, "0");
  return `${Math.floor(totalSeconds / 60)}:${seconds}`;
}

/** Counts down locally from the last server value; a new `remainingMs` re-syncs it. */
export function ClockDisplay(props: { label: string; remainingMs: number; running: boolean }) {
  // Remounting on a new server value restarts the local countdown from it.
  return <Countdown key={`${props.remainingMs}-${props.running}`} {...props} />;
}

function Countdown({ label, remainingMs, running }: { label: string; remainingMs: number; running: boolean }) {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!running) return;
    const startedAt = Date.now();
    const timer = setInterval(() => setElapsedMs(Date.now() - startedAt), 250);
    return () => clearInterval(timer);
  }, [remainingMs, running]);

  const shownMs = Math.max(0, remainingMs - elapsedMs);
  return (
    <div
      role="timer"
      aria-label={label}
      data-slot="clock"
      data-running={running}
      className={cn(
        "flex items-center justify-between rounded-lg border px-3 py-2 font-mono text-2xl tabular-nums transition-all",
        running ? "border-primary bg-primary/10 text-primary" : "bg-muted/40 text-muted-foreground",
        shownMs < LOW_TIME_MS && "border-destructive bg-destructive/10 text-destructive",
      )}
    >
      <span className="font-sans text-sm">{label}</span>
      {formatClock(shownMs)}
    </div>
  );
}
