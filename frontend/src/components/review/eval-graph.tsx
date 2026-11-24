"use client";

import { CartesianGrid, Line, LineChart, ReferenceLine, XAxis, YAxis } from "recharts";

import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";

const config = { chance: { label: "Your winning chances (%)", color: "var(--chart-1)" } } satisfies ChartConfig;

export function EvalGraph({
  points,
  totalPlies,
  ply,
}: {
  points: { ply: number; win_probability: number }[];
  totalPlies: number;
  ply: number;
}) {
  const data = points.map((point) => ({ ply: point.ply, chance: Math.round(point.win_probability * 100) }));
  return (
    <ChartContainer
      config={config}
      role="img"
      aria-label="Your winning chances after each of your moves"
      className="aspect-auto h-48 w-full"
    >
      <LineChart data={data} margin={{ left: 0, right: 8, top: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="ply" type="number" domain={[0, totalPlies]} tickLine={false} allowDecimals={false} />
        <YAxis domain={[0, 100]} width={36} tickLine={false} />
        <ChartTooltip
          content={<ChartTooltipContent labelFormatter={(_, items) => `Ply ${items[0]?.payload.ply}`} />}
        />
        <ReferenceLine x={ply} stroke="var(--color-chance)" strokeDasharray="4 4" />
        <ReferenceLine y={50} strokeOpacity={0.4} />
        <Line dataKey="chance" stroke="var(--color-chance)" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
      </LineChart>
    </ChartContainer>
  );
}
