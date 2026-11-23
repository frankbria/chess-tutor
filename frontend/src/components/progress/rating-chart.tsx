"use client";

import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { RatingHistoryEntry } from "@/lib/progress-api";

const config = {
  rating: { label: "Rating", color: "var(--chart-1)" },
  performance: { label: "Game performance", color: "var(--chart-2)" },
} satisfies ChartConfig;

export function RatingChart({ history, description }: { history: RatingHistoryEntry[]; description: string }) {
  const data = history.map((entry, index) => ({
    game: index + 1,
    rating: Math.round(entry.rating_after),
    performance: entry.performance_rating,
  }));
  return (
    <ChartContainer config={config} role="img" aria-label={description} className="aspect-auto h-64 w-full">
      <LineChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="game" tickLine={false} allowDecimals={false} />
        <YAxis domain={["dataMin - 50", "dataMax + 50"]} width={44} tickLine={false} />
        <ChartTooltip content={<ChartTooltipContent labelFormatter={(_, items) => `Game ${items[0]?.payload.game}`} />} />
        <Line dataKey="rating" stroke="var(--color-rating)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
        {/* A null performance leaves a gap; with no line the remaining points show as dots. */}
        <Line dataKey="performance" stroke="none" dot={{ r: 4, fill: "var(--color-performance)" }} isAnimationActive={false} />
      </LineChart>
    </ChartContainer>
  );
}
