import { Suspense } from "react"
import { BackendProblem } from "@/components/backend-problem"
import { Skeleton } from "@/components/ui/skeleton"
import { api, attempt } from "@/lib/backend"
import { loadRunsParams } from "@/lib/search-params"
import { Pipeline } from "./pipeline"
import { RunDetail } from "./run-detail"
import { RunAgentLog } from "./run-agent-log"
import { DocumentView, publishedFiles, RunBrief } from "./run-documents"
import { RunSources } from "./run-sources"
import { RunList } from "./run-list"

export default async function RunsPage({ searchParams }: PageProps<"/">) {
  const { run: runId, tab, jobs, doc, more, src, call } = await loadRunsParams(searchParams)
  const [runs, run] = await Promise.all([attempt(api.runs()), runId ? attempt(api.run(runId)) : null])
  if (!runs.data) return <BackendProblem title="Can't load runs" message={runs.error} />
  // Files are read only for the tab that shows them, and a document only once it's opened.
  const reportFiles = tab === "artifacts" && run?.data ? await publishedFiles(run.data) : []

  return (
    <div className="grid gap-6 md:grid-cols-[17rem_minmax(0,1fr)] lg:grid-cols-[20rem_minmax(0,1fr)]">
      <aside aria-label="Runs" className={runId ? "hidden md:block" : undefined}>
        <RunList runs={runs.data} />
      </aside>
      {/* min-w-0: long file paths in the run view must not widen the grid column. */}
      <section aria-label="Run details" className={runId ? "min-w-0" : "hidden min-w-0 md:block"}>
        {!run ? (
          <div className="grid h-64 place-items-center rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            {runs.data.length ? "Select a run to see its progress and files." : "Your runs will show up here."}
          </div>
        ) : run.data ? (
          <RunDetail
            key={run.data.run_id}
            initial={run.data}
            brief={
              // Keyed: these sit among RunDetail's own children, which React checks for keys.
              <Suspense key="brief" fallback={<Skeleton className="h-11" />}>
                <RunBrief run={run.data} />
              </Suspense>
            }
            document={
              tab === "artifacts" && doc ? (
                <Suspense key={`${doc}:${more}`} fallback={<Skeleton className="h-64" />}>
                  <DocumentView run={run.data} doc={doc} more={more} />
                </Suspense>
              ) : null
            }
            reportFiles={reportFiles}
            sources={
              tab === "sources" ? (
                <Suspense fallback={<Skeleton className="h-64" />}>
                  <RunSources run={run.data} src={src} more={more} />
                </Suspense>
              ) : null
            }
            agentLog={
              tab === "log" ? (
                <Suspense fallback={<Skeleton className="h-64" />}>
                  <RunAgentLog run={run.data} call={call} more={more} />
                </Suspense>
              ) : null
            }
            pipeline={
              tab === "pipeline" ? (
                <Suspense fallback={<Skeleton className="h-64" />}>
                  <Pipeline run={run.data} jobs={jobs} />
                </Suspense>
              ) : null
            }
          />
        ) : (
          <BackendProblem title="Can't open this run" message={run.error} />
        )}
      </section>
    </div>
  )
}
