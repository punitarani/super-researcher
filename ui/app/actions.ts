"use server"
// Every change the UI makes goes through these Server Actions. Next.js rejects
// cross-site calls (Origin must match Host); inputs are validated here again
// because actions can be called directly.
import { refresh } from "next/cache"
import { api, describeError } from "@/lib/backend"
import { agentId } from "@/lib/schemas"

export type ActionResult = { error: string | null }

async function perform(work: () => Promise<unknown>): Promise<ActionResult> {
  try {
    await work()
  } catch (error) {
    return { error: describeError(error) }
  }
  refresh()
  return { error: null }
}

export async function selectAgent(id: string): Promise<ActionResult> {
  const agent = agentId.safeParse(id)
  if (!agent.success) return { error: "Unknown agent." }
  return perform(() => api.selectAgent(agent.data))
}

export async function recheckAgents(): Promise<ActionResult> {
  return perform(() => api.agents(true))
}

export async function startCodexLogin(): Promise<ActionResult & { url: string | null }> {
  try {
    return { url: (await api.startCodexLogin()).url, error: null }
  } catch (error) {
    return { url: null, error: describeError(error) }
  }
}
