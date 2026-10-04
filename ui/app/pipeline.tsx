import { api, attempt, BACKEND_URL, type PipelineStatus } from "@/lib/backend"
import { AGENT_STATE_LABELS } from "@/lib/labels"
import { isActive, jobKind, streamTarget, type JobKind, type Run } from "@/lib/schemas"
import { STAGES } from "@/lib/stages"
import { JobCard, type StageNotes } from "./job-card"

/** The corpus stages after a run: each one is a background job on the backend. */
export async function Pipeline({ run, jobs }: { run: Run; jobs: string[] }) {
  if (run.state !== "completed") {
    return (
      <p className="py-6 text-sm text-muted-foreground">
        {isActive(run.state)
          ? "The pipeline opens once this run completes."
          : "Only completed runs go through the pipeline. Start a new run to build a full corpus."}
      </p>
    )
  }
  const watched = watchedJobs(jobs)
  const [status, agents, ...initial] = await Promise.all([
    api.pipeline(run.run_id),
    attempt(api.agents()),
    ...STAGES.map((stage) => (watched[stage.kind] ? attempt(api.job(stage.kind, watched[stage.kind]!)) : null)),
  ])
  const agent = agents.data?.agents.find((item) => item.id === agents.data.selected)
  const agentProblem = agent && !agent.ready ? `${agent.label}: ${AGENT_STATE_LABELS[agent.state]}` : null
  const notes = stageNotes(status)

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        Each stage builds on the one before. Curating topics, picking terms, and exporting the report happen in the{" "}
        <a href={BACKEND_URL} target="_blank" rel="noopener noreferrer" className="font-medium text-foreground underline underline-offset-4">
          classic UI
        </a>
        .
      </p>
      <ol className="space-y-3">
        {STAGES.map((stage, index) => (
          <li key={stage.kind}>
            <JobCard
              stage={stage}
              step={index + 1}
              corpusId={run.run_id}
              notes={notes[stage.kind]}
              agentProblem={stage.needsAgent ? agentProblem : null}
              classicUrl={BACKEND_URL}
              initialJob={initial[index]?.data ?? null}
              lostJob={!!initial[index]?.error}
            />
          </li>
        ))}
      </ol>
    </div>
  )
}

function watchedJobs(entries: string[]): Partial<Record<JobKind, string>> {
  const watched: Partial<Record<JobKind, string>> = {}
  for (const entry of entries) {
    const [kind, id] = entry.split(":", 2)
    const parsedKind = jobKind.safeParse(kind)
    if (parsedKind.success && streamTarget.shape.id.safeParse(id).success) watched[parsedKind.data] = id
  }
  return watched
}

function stageNotes(status: PipelineStatus): Record<JobKind, StageNotes> {
  const deps = status.atlasDeps
  return {
    postprocess: { summary: `${status.markdownCount} Markdown sidecars`, done: false },
    atlas: {
      summary: status.atlasReady ? `Built: ${status.chunkCount} chunks` : null,
      done: status.atlasReady,
      setup: deps && !deps.ready_for_build ? deps.install_commands : undefined,
    },
    topics: {
      summary: status.curated ? "Outline curated" : status.topics ? "Outline ready, not curated yet" : null,
      done: status.topics,
      next: status.topics && !status.curated ? "curate the topic tree" : undefined,
    },
    compose: {
      summary: status.finalized ? "Terms finalized" : status.composed ? "Terms generated, not finalized yet" : status.curated ? null : "Needs a curated topic tree first",
      done: status.composed,
      next: status.composed && !status.finalized ? "pick and finalize the terms" : undefined,
    },
    compile: {
      summary: status.paperPath ? `Paper: \`${status.paperPath}\`` : status.finalized ? null : "Needs finalized Compose terms first",
      done: !!status.paperPath,
      next: status.paperPath ? "preview and export the report" : undefined,
    },
  }
}
