import path from "node:path"
import { defineConfig } from "vitest/config"

export default defineConfig({
  resolve: {
    alias: {
      "@": import.meta.dirname,
      "server-only": path.join(import.meta.dirname, "node_modules/server-only/empty.js"),
    },
  },
  test: { include: ["**/*.test.ts"], exclude: ["node_modules/**", "e2e/**"] },
})
