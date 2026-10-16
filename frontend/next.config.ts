import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Every console page renders authoritative, per-user case state at request time, so there
  // is no static shell worth prerendering and nothing that may be served from a cache.
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
