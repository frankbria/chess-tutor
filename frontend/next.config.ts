import type { NextConfig } from "next";

// The browser reaches the API at the relative path /api when NEXT_PUBLIC_API_BASE_URL=/api;
// this proxies it to the backend, so one HTTPS origin serves the whole app.
const backendUrl = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:8000";
const publicHost = process.env.PUBLIC_HOST;

const nextConfig: NextConfig = {
  /* config options here */
  output: "standalone",
  allowedDevOrigins: publicHost ? [publicHost] : [],
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/:path*` }];
  },
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
