import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    coverage: {
      include: ["src/**/*.{ts,tsx}"],
      // Vendored shadcn primitives are covered upstream; the root layout needs next/font,
      // which only runs inside Next, so `next build` and the browser demo cover it.
      exclude: ["src/components/ui/**", "src/app/layout.tsx"],
      thresholds: { lines: 85 },
    },
  },
});
