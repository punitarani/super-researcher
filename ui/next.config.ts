import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  // The repo root has its own package-lock.json (for the Atlas bundle); this app is self-contained.
  turbopack: { root: __dirname },
  // Next allows one dev server per build folder, so the e2e server uses its own and can run next to `npm run ui`.
  distDir: process.env.NEXT_DIST_DIR || ".next",
}

export default nextConfig
