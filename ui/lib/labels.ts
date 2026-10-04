import type { Agent } from "./schemas"

export type Tone = "ok" | "busy" | "warn" | "bad" | "idle"

export const STATE_LABELS: Record<string, { label: string; tone: Tone }> = {
  queued: { label: "Queued", tone: "idle" },
  running: { label: "Running", tone: "busy" },
  completed: { label: "Completed", tone: "ok" },
  failed: { label: "Failed", tone: "bad" },
  stopped: { label: "Stopped", tone: "warn" },
  interrupted: { label: "Interrupted", tone: "warn" },
}

export const AGENT_STATE_LABELS: Record<Agent["state"], string> = {
  ready: "Ready",
  not_installed: "Not installed",
  outdated: "Update needed",
  signed_out: "Signed out",
  wrong_auth: "Not using ChatGPT",
  expired: "Sign-in expired",
  error: "Check failed",
  missing_key: "No API key",
}

/** Codex states that "Sign in with ChatGPT" fixes. */
export const SIGN_IN_STATES = new Set<Agent["state"]>(["signed_out", "wrong_auth", "expired"])

export const SEARCH_PROVIDERS = [
  { key: "EXA_API_KEY", label: "Exa" },
  { key: "SERPER_API_KEY", label: "Serper" },
  { key: "SERP_API_KEY", label: "SerpAPI" },
] as const

/** Backend timestamps are local ISO strings; show them without time-zone surprises. */
export function formatTime(iso: string | null | undefined) {
  return iso ? iso.replace("T", " ").slice(0, 16) : "—"
}
