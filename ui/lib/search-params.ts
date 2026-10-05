// URL state for the runs page, shared by server pages and client hooks.
import { createLoader, createSerializer, parseAsArrayOf, parseAsInteger, parseAsString, parseAsStringLiteral } from "nuqs/server"
import { ACTIVE_STATES } from "./schemas"

export const RUN_FILTERS = ["all", "active", "completed", "failed", "stopped"] as const
export type RunFilter = (typeof RUN_FILTERS)[number]
export const RUN_TABS = ["activity", "artifacts", "pipeline"] as const
export type RunTab = (typeof RUN_TABS)[number]

export const runsParams = {
  q: parseAsString.withDefault(""),
  state: parseAsStringLiteral(RUN_FILTERS).withDefault("all"),
  run: parseAsString,
  tab: parseAsStringLiteral(RUN_TABS).withDefault("activity"),
  // Pipeline jobs being watched, as "kind:jobId", so a refresh keeps following them.
  jobs: parseAsArrayOf(parseAsString).withDefault([]),
  // Artifacts tab: the open document (a path inside the run folder) and how many times "Show more" was used.
  doc: parseAsString,
  more: parseAsInteger.withDefault(0),
}

export const loadRunsParams = createLoader(runsParams)
export const runsHref = createSerializer(runsParams)

const FILTER_STATES: Record<RunFilter, readonly string[] | null> = {
  all: null,
  active: ACTIVE_STATES,
  completed: ["completed"],
  failed: ["failed"],
  stopped: ["stopped", "interrupted"],
}

export function matchesFilter(run: { topic: string; state: string }, filter: RunFilter, query: string) {
  const states = FILTER_STATES[filter]
  return (!states || states.includes(run.state)) && run.topic.toLowerCase().includes(query.trim().toLowerCase())
}
