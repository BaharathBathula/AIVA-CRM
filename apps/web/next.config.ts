import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // The floating dev badge sits on top of the sidebar's account menu.
  devIndicators: false,
  // Let the dev server answer through GitHub Codespaces forwarded URLs
  // (https://<codespace>-3000.app.github.dev) and 127.0.0.1, not only localhost.
  allowedDevOrigins: ["*.app.github.dev", "127.0.0.1"],
  // Container deploys (deploy/Dockerfile) build a self-contained server.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" as const } : {}),
};

export default nextConfig;
