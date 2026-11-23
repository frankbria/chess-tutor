import type { NextConfig } from "next";

// The browser reaches the API at the relative path /api when NEXT_PUBLIC_API_BASE_URL=/api;
// this proxies it to the backend, so one HTTPS origin serves the whole app. Opt-in: `make dev`
// sets BACKEND_INTERNAL_URL; builds without it (Docker) ship no proxy.
const backendUrl = process.env.BACKEND_INTERNAL_URL;
const publicHost = process.env.PUBLIC_HOST;

const nextConfig: NextConfig = {
  /* config options here */
  output: "standalone",
  allowedDevOrigins: publicHost ? [publicHost] : [],
  async rewrites() {
    return backendUrl ? [{ source: "/api/:path*", destination: `${backendUrl}/:path*` }] : [];
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
