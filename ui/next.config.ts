import type { NextConfig } from "next"

const nextConfig: NextConfig = {
  poweredByHeader: false,
  devIndicators: false,
  // The repo root has its own package-lock.json (for the Atlas bundle); this app is self-contained.
  turbopack: { root: __dirname },
}

export default nextConfig
