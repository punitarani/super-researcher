// Zod schemas for every boundary: backend responses (parsed on the server, unknown
// fields stripped before anything reaches the browser) and user input.
import { z } from "zod"

export const AGENT_IDS = ["codex", "gemini"] as const
export const agentId = z.enum(AGENT_IDS)

export const agentSchema = z.object({
  id: agentId,
  label: z.string(),
  state: z.enum(["ready", "not_installed", "outdated", "signed_out", "wrong_auth", "expired", "error", "missing_key"]),
  ready: z.boolean(),
  message: z.string(),
})
export type Agent = z.infer<typeof agentSchema>

export const agentsSchema = z.object({ selected: agentId, agents: z.array(agentSchema) })
export type Agents = z.infer<typeof agentsSchema>

export const loginSchema = z.object({ url: z.url({ protocol: /^https$/ }) })

// Only the names of configured keys are kept; values never leave the backend.
export const configSchema = z
  .object({
    depth_options: z.record(z.string(), z.number()),
    default_storage_root: z.string(),
    default_final_source_count: z.number(),
    max_final_source_count: z.number(),
    configured_api_keys: z.record(z.string(), z.unknown()),
  })
  .transform(({ configured_api_keys, ...config }) => ({ ...config, configuredKeys: Object.keys(configured_api_keys) }))
export type Config = z.infer<typeof configSchema>

export const ACTIVE_STATES = ["queued", "running"] as const
export const isActive = (state: string) => (ACTIVE_STATES as readonly string[]).includes(state)

const counts = z.record(z.string(), z.unknown()).catch({})
const timestamp = z.string().nullish().transform((value) => value ?? null)

export const RUN_STATES = ["queued", "running", "completed", "failed", "stopped", "interrupted"] as const
export const runState = z.enum(RUN_STATES)
export type RunState = z.infer<typeof runState>

export const runSummarySchema = z.object({
  run_id: z.string(),
  topic: z.string(),
  state: runState,
  milestone: z.string().nullish(),
  progress: z.number().catch(0),
  started_at: timestamp,
  completed_at: timestamp,
  error: z.string().nullish(),
  dossier_path: z.string(),
  counts,
  quality: z.object({ verdict: z.string(), warnings: z.array(z.string()).catch([]) }).nullish().catch(null),
  stop_requested: z.boolean().catch(false),
})
export type RunSummary = z.infer<typeof runSummarySchema>

// History is read from every saved run.json, so one unreadable run is skipped instead of hiding them all.
export const runListSchema = z
  .array(z.unknown())
  .transform((rows) => rows.flatMap((row) => runSummarySchema.safeParse(row).data ?? []))

export const runSchema = runSummarySchema.extend({
  events: z.array(z.object({ time: z.string(), message: z.string() })).catch([]),
  files: z.record(z.string(), z.string()).catch({}),
})
export type Run = z.infer<typeof runSchema>

export const JOB_KINDS = ["postprocess", "atlas", "topics", "compose", "compile"] as const
export const jobKind = z.enum(JOB_KINDS)
export type JobKind = z.infer<typeof jobKind>

export const jobSchema = z.object({
  job_id: z.string(),
  corpus_id: z.string(),
  state: z.enum(["queued", "running", "completed", "failed"]),
  stage: z.string().catch(""),
  progress: z.number().catch(0),
  counts,
  error: z.string().nullish(),
})
export type Job = z.infer<typeof jobSchema>

// Run, job and corpus ids: plain names, never "." or ".." (which a URL would resolve as a path).
export const ID_PATTERN = /^(?!\.\.?$)[\w.-]{1,200}$/

export const settingsSchema = z.object({ agent_log: z.boolean(), agent_log_location: z.string() })
export type Settings = z.infer<typeof settingsSchema>

// A run's brief, from its settings.json. Only the names of configured keys are kept, never values.
const briefText = z.string().catch("")
export const briefSchema = z
  .object({
    topic: briefText,
    context: briefText,
    depth: briefText,
    breadth: briefText,
    final_source_count: z.number().nullable().catch(null),
    audience: briefText,
    geographic_scope: briefText,
    time_horizon: briefText,
    objective: briefText,
    must_include: briefText,
    must_exclude: briefText,
    preferred_sources: briefText,
    disallowed_sources: briefText,
    agent: z.string().nullable().catch(null),
    configured_api_keys: z.record(z.string(), z.unknown()).catch({}),
  })
  .transform(({ configured_api_keys, ...brief }) => ({ ...brief, keyNames: Object.keys(configured_api_keys) }))
export type Brief = z.infer<typeof briefSchema>

// Things a stream can follow: a research run, or one of the corpus jobs.
export const streamTarget = z.object({
  kind: z.enum(["run", ...JOB_KINDS]),
  id: z.string().regex(ID_PATTERN),
})

const exists = z.object({ exists: z.boolean() }).catch({ exists: false })
export const pipelineStatusSchema = {
  corpora: z.object({
    corpora: z.array(z.object({ id: z.string(), atlas_ready: z.boolean(), chunk_count: z.number().catch(0), markdown_count: z.number().catch(0) })),
  }),
  topics: z.object({ raw: exists, curated: exists }),
  // Generated terms only count while they match the curated topics (`valid`), as in the classic UI.
  compose: z.object({ generated: z.object({ valid: z.boolean() }).catch({ valid: false }), finalized: exists }),
  publish: z.object({ paper: z.object({ exists: z.boolean(), path: z.string() }).catch({ exists: false, path: "" }) }),
  atlasDependencies: z.object({ ready_for_build: z.boolean(), install_commands: z.array(z.string()).catch([]) }),
}

export const errorSchema = z.object({ error: z.string() })

// User input for a new research run, mirroring the backend's own checks.
const text = (max: number) => z.string().trim().max(max, `Keep this under ${max} characters.`).default("")
export const DEPTHS = ["low", "medium", "high", "extra_high", "ludicrous"] as const
export const BREADTHS = ["low", "medium", "high"] as const
export const newRunSchema = z.object({
  topic: z.string().trim().min(1, "Enter a research topic.").max(500, "Keep the topic under 500 characters."),
  context: text(2000),
  depth: z.enum(DEPTHS, "Choose a depth."),
  breadth: z.enum(BREADTHS, "Choose a breadth."),
  final_source_count: z.coerce
    .number("Enter a number.")
    .int("Enter a whole number.")
    .min(1, "Use at least 1 source.")
    .max(500, "The maximum is 500 sources."),
  audience: text(300),
  geographic_scope: text(300),
  time_horizon: text(300),
  objective: text(500),
  must_include: text(2000),
  must_exclude: text(2000),
  preferred_sources: text(2000),
  disallowed_sources: text(2000),
})
export type NewRun = z.infer<typeof newRunSchema>
