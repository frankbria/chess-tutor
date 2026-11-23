import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Chess Tutor",
    short_name: "Chess Tutor",
    description: "Play at your level with an AI coach that notices what you miss.",
    start_url: "/",
    display: "standalone",
    background_color: "#f7f5ff",
    theme_color: "#5b3fd6",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}
