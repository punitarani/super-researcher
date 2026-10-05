"use server"
// Every change the UI makes goes through these Server Actions. Next.js rejects
// cross-site calls (Origin must match Host); inputs are validated here again
// because actions can be called directly.
import { refresh } from "next/cache"
import { redirect } from "next/navigation"
import { z } from "zod"
import { api, describeError } from "@/lib/backend"
import { agentId, jobKind, newRunSchema, streamTarget, type Job, type NewRun } from "@/lib/schemas"

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

export type StartRunState = {
  error: string | null
  fieldErrors: Partial<Record<keyof NewRun, string[]>>
  values: Record<string, string>
}

export async function startRun(_previous: StartRunState, form: FormData): Promise<StartRunState> {
  // Echo what was typed back, so a rejected form keeps its values.
  const values = Object.fromEntries([...form].filter((entry): entry is [string, string] => typeof entry[1] === "string"))
  const input = newRunSchema.safeParse(values)
  if (!input.success) {
    return { error: "Some fields need attention.", fieldErrors: z.flattenError(input.error).fieldErrors, values }
  }
  let runId: string
  try {
    runId = (await api.startRun(input.data)).run_id
  } catch (error) {
    return { error: describeError(error), fieldErrors: {}, values }
  }
  redirect(`/?run=${encodeURIComponent(runId)}`)
}

export async function stopRun(runId: string): Promise<ActionResult> {
  const id = streamTarget.shape.id.safeParse(runId)
  if (!id.success) return { error: "Unknown run." }
  return perform(() => api.stopRun(id.data))
}

const startJobInput = z.object({ kind: jobKind, corpusId: streamTarget.shape.id, force: z.boolean() })

export async function startJob(kind: string, corpusId: string, force: boolean): Promise<ActionResult & { job: Job | null }> {
  const input = startJobInput.safeParse({ kind, corpusId, force })
  if (!input.success) return { job: null, error: "Unknown pipeline stage or corpus." }
  try {
    return { job: await api.startJob(input.data.kind, input.data.corpusId, input.data.force), error: null }
  } catch (error) {
    return { job: null, error: describeError(error) }
  }
}
