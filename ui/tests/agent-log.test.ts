import { describe, expect, it } from "vitest"
import { callFiles, parseAgentLog, summarizeCalls } from "@/lib/agent-log"

const entry = (call: number, outcome: string, extra: object = {}) =>
  JSON.stringify({ call, time: "2026-10-04T15:14:40", step: `Step ${call}`, agent: "codex", model: "plan default", outcome, sent: true, reason: null, duration_ms: 1200, prompt_chars: 900, reply_chars: 300, ...extra })

describe("parseAgentLog", () => {
  it("reads each call and tolerates odd or cut-off lines", () => {
    const text = [entry(1, "answered"), "garbage", entry(2, "fell_back", { sent: false, reason: "Codex isn't signed in." }), entry(3, "something_new"), '{"call": 4, "ti'].join("\n")
    const calls = parseAgentLog(text, true)
    expect(calls.map((call) => [call.call, call.outcome, call.sent])).toEqual([
      [1, "answered", true],
      [2, "fell_back", false],
      [3, "failed", true],
    ])
    expect(calls[1].reason).toBe("Codex isn't signed in.")
  })

  it("counts outcomes", () => {
    const calls = parseAgentLog([entry(1, "answered"), entry(2, "fell_back"), entry(3, "fell_back", { sent: false })].join("\n"), false)
    expect(summarizeCalls(calls)).toEqual({ total: 3, answered: 1, fell_back: 2, failed: 0, not_sent: 1 })
  })
})

describe("callFiles", () => {
  it("maps a call to its prompt and reply files, and the saved topic prompt to its files", () => {
    expect(callFiles("7")).toEqual({ prompt: "logs/agent-calls/0007-prompt.txt", reply: "logs/agent-calls/0007-reply.txt" })
    expect(callFiles("topics")).toEqual({ prompt: "atlas/topics/llm_prompt.md", reply: "atlas/topics/topic_tree.md" })
  })

  it("refuses anything else", () => {
    for (const bad of ["0", "-1", "1.5", "../1", "99999999", "topics/../x", ""]) expect(callFiles(bad)).toBeNull()
  })
})
