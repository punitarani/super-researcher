// The only code that talks to the Python backend. Runs on the Next.js server;
// every response is checked with Zod so the browser only sees known fields.
import "server-only"
import { z } from "zod"
import {
  agentsSchema,
  configSchema,
  errorSchema,
  jobSchema,
  loginSchema,
  pipelineStatusSchema as S,
  runSchema,
  runSummarySchema,
  type JobKind,
  type NewRun,
} from "./schemas"

export class BackendError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"])

/** The backend URL, which must point at this computer. */
export function backendUrl(raw = process.env.SUPERRESEARCHER_API_URL || "http://127.0.0.1:8765"): string {
  const url = new URL(raw)
  if (!LOOPBACK.has(url.hostname)) {
    throw new Error(`SUPERRESEARCHER_API_URL must point at this computer (127.0.0.1 or localhost), not ${url.hostname}.`)
  }
  return url.origin
}

export const BACKEND_URL = backendUrl()

async function call<T>(path: string, schema: z.ZodType<T>, body?: unknown, method = body === undefined ? "GET" : "POST"): Promise<T> {
  let response: Response
  try {
    response = await fetch(BACKEND_URL + path, {
      method,
      cache: "no-store",
      // The backend only accepts state changes sent as JSON from this computer.
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new BackendError(`Can't reach the SuperResearcher backend at ${BACKEND_URL}. Start it with \`npm run ui\` from the repo root.`, 503)
  }
  const json: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    throw new BackendError(errorSchema.safeParse(json).data?.error ?? `The backend answered with status ${response.status}.`, response.status)
  }
  const parsed = schema.safeParse(json)
  if (!parsed.success) throw new BackendError(`The backend sent an unexpected response for ${path.split("?")[0]}.`, 502)
  return parsed.data
}

const JOBS: Record<JobKind, { start: string; poll: string; force?: string }> = {
  postprocess: { start: "/api/postprocess/markdown", poll: "/api/postprocess/jobs/" },
  atlas: { start: "/api/atlas/build", poll: "/api/atlas/jobs/", force: "force" },
  topics: { start: "/api/topics/discover", poll: "/api/topics/jobs/", force: "force" },
  compose: { start: "/api/compose/build", poll: "/api/compose/jobs/", force: "force" },
  compile: { start: "/api/publish/compile", poll: "/api/publish/jobs/", force: "force_plan" },
}

const enc = encodeURIComponent
const orNull = <T,>(promise: Promise<T>) => promise.catch(() => null)

export const api = {
  config: () => call("/api/config", configSchema),
  agents: (refresh = false) => call(`/api/agents${refresh ? "?refresh=1" : ""}`, agentsSchema),
  selectAgent: (selected: string) => call("/api/agents", agentsSchema, { selected }, "PUT"),
  startCodexLogin: () => call("/api/agents/codex/login", loginSchema, {}),

  runs: () => call("/api/runs", z.array(runSummarySchema)),
  run: (runId: string) => call(`/api/runs/${enc(runId)}`, runSchema),
  startRun: (input: NewRun) => call("/api/runs", runSchema, input),
  stopRun: (runId: string) => call(`/api/runs/${enc(runId)}/stop`, runSchema, {}),

  job: (kind: JobKind, jobId: string) => call(JOBS[kind].poll + enc(jobId), jobSchema),
  startJob: (kind: JobKind, corpusId: string, force: boolean) => {
    const { start, force: forceKey } = JOBS[kind]
    return call(start, jobSchema, { corpus_id: corpusId, ...(forceKey && { [forceKey]: force }) })
  },

  /** What each pipeline stage has already produced for a corpus. Unknown parts read as not done. */
  async pipeline(corpusId: string) {
    const corpus = enc(corpusId)
    const [corpora, topics, compose, publish, atlasDeps] = await Promise.all([
      orNull(call("/api/corpora", S.corpora)),
      orNull(call(`/api/atlas/${corpus}/topics`, S.topics)),
      orNull(call(`/api/atlas/${corpus}/compose`, S.compose)),
      orNull(call(`/api/atlas/${corpus}/publish`, S.publish)),
      orNull(call("/api/atlas/dependencies", S.atlasDependencies)),
    ])
    const row = corpora?.corpora.find((item) => item.id === corpusId)
    return {
      markdownCount: row?.markdown_count ?? 0,
      atlasReady: row?.atlas_ready ?? false,
      chunkCount: row?.chunk_count ?? 0,
      atlasDeps,
      topics: topics?.raw.exists ?? false,
      curated: topics?.curated.exists ?? false,
      composed: compose?.generated.exists ?? false,
      finalized: compose?.finalized.exists ?? false,
      paperPath: publish?.paper.exists ? publish.paper.path : null,
    }
  },
}

export type PipelineStatus = Awaited<ReturnType<typeof api.pipeline>>

/** Turn any failure into a message that's safe and useful to show. */
export function describeError(error: unknown): string {
  return error instanceof BackendError ? error.message : "Something went wrong. Check the terminal running `npm run ui` for details."
}

/** Await a backend call and keep the error as a message instead of throwing. */
export async function attempt<T>(promise: Promise<T>): Promise<{ data: T; error: null } | { data: null; error: string }> {
  try {
    return { data: await promise, error: null }
  } catch (error) {
    return { data: null, error: describeError(error) }
  }
}
