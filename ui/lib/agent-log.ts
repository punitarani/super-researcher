import { z } from "zod"

// The per-run agent log written by superresearcher/agent_log.py.

export const AGENT_LOG_INDEX = "logs/agent-log.jsonl"
export const TOPIC_PROMPT = "atlas/topics/llm_prompt.md"

const outcome = z.enum(["answered", "fell_back", "failed"]).catch("failed")
const callSchema = z.object({
  call: z.number().int().positive(),
  time: z.string().catch(""),
  step: z.string().catch("Prompt"),
  agent: z.string().catch(""),
  model: z.string().catch(""),
  outcome,
  sent: z.boolean().catch(true),
  reason: z.string().nullish().catch(null),
  duration_ms: z.number().catch(0),
  prompt_chars: z.number().catch(0),
  reply_chars: z.number().catch(0),
})
export type AgentCall = z.infer<typeof callSchema>

export function parseAgentLog(text: string, truncated: boolean): AgentCall[] {
  const lines = text.split("\n").slice(0, truncated ? -1 : undefined)
  const calls: AgentCall[] = []
  for (const line of lines) {
    try {
      const parsed = callSchema.safeParse(JSON.parse(line))
      if (parsed.success) calls.push(parsed.data)
    } catch {
      // not a log line
    }
  }
  return calls
}

export function summarizeCalls(calls: AgentCall[]) {
  const count = (state: AgentCall["outcome"]) => calls.filter((call) => call.outcome === state).length
  return { total: calls.length, answered: count("answered"), fell_back: count("fell_back"), failed: count("failed"), not_sent: calls.filter((call) => !call.sent).length }
}

/** The files behind ?call=: a logged call's prompt and reply, or topic discovery's saved prompt and outline. */
export function callFiles(call: string): { prompt: string; reply: string } | null {
  if (call === "topics") return { prompt: TOPIC_PROMPT, reply: "atlas/topics/topic_tree.md" }
  if (!/^[1-9]\d{0,5}$/.test(call)) return null
  const number = call.padStart(4, "0")
  return { prompt: `logs/agent-calls/${number}-prompt.txt`, reply: `logs/agent-calls/${number}-reply.txt` }
}
