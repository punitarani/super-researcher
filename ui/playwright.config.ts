import { defineConfig } from "@playwright/test"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"

// A throwaway backend: empty storage, no API keys, and an agent that needs a key
// it doesn't have, so tests never use your Codex sign-in or the network.
process.env.E2E_STORAGE ??= mkdtempSync(path.join(tmpdir(), "superresearcher-e2e-"))
const storage = process.env.E2E_STORAGE
export const API_URL = "http://127.0.0.1:8799"
const WEB_URL = "http://127.0.0.1:3099"

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  // `next dev` compiles each route on first use, which can take a few seconds.
  expect: { timeout: 15_000 },
  workers: 1,
  use: {
    baseURL: WEB_URL,
    // Set CHROMIUM_PATH to use an already-installed Chromium instead of `npx playwright install chromium`.
    launchOptions: { executablePath: process.env.CHROMIUM_PATH || undefined },
  },
  webServer: [
    {
      command: "python3 ../run_app.py --host 127.0.0.1 --port 8799",
      url: `${API_URL}/api/config`,
      env: { SUPERRESEARCHER_STORAGE_ROOT: storage, SUPERRESEARCHER_API_KEYS: path.join(storage, "no-keys.txt"), SUPERRESEARCHER_AGENT: "gemini" },
    },
    {
      command: "npx next dev -H 127.0.0.1 -p 3099",
      url: `${WEB_URL}/agents`,
      timeout: 120_000,
      env: { SUPERRESEARCHER_API_URL: API_URL },
    },
  ],
})
