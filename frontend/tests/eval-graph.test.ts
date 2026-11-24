import { describe, expect, it } from "vitest";

import { formatPercentTick, PERCENT_DOMAIN, PERCENT_TICKS, toChartData } from "@/components/review/eval-graph";

// Deliberately unmocked: review.test.tsx stubs the graph, which let a x100 bug through.
describe("eval graph", () => {
  it("plots the API's 0-100 win probabilities as rounded percentages, unscaled", () => {
    expect(toChartData([{ ply: 1, win_probability: 52.48 }, { ply: 3, win_probability: 13.65 }])).toEqual([
      { ply: 1, chance: 52 },
      { ply: 3, chance: 14 },
    ]);
  });

  it("fixes the axis to 0-100 with quarter ticks suffixed with %", () => {
    expect(PERCENT_DOMAIN).toEqual([0, 100]);
    expect(PERCENT_TICKS.map(formatPercentTick)).toEqual(["0%", "25%", "50%", "75%", "100%"]);
  });
});
