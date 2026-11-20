import type { NextConfig } from "next";

// The browser talks only to Next.js; /api/* is proxied to the local FastAPI server.
// The browser itself sends X-Local-GUI, so the API's CSRF check still applies.
const API_URL = process.env.SCRAPER_API_URL ?? "http://127.0.0.1:8765";

const nextConfig: NextConfig = {
  // Publish is one long synchronous request; the default 30s proxy timeout resets the socket (ECONNRESET).
  experimental: { proxyTimeout: 10 * 60 * 1000 },
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
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
};

export default nextConfig;
