import { Suspense } from "react"
import { BackendProblem } from "@/components/backend-problem"
import { Skeleton } from "@/components/ui/skeleton"
import { api, attempt } from "@/lib/backend"
import { loadRunsParams } from "@/lib/search-params"
import { Pipeline } from "./pipeline"
import { RunDetail } from "./run-detail"
import { RunList } from "./run-list"

export default async function RunsPage({ searchParams }: PageProps<"/">) {
  const { run: runId, tab, jobs } = await loadRunsParams(searchParams)
  const [runs, run] = await Promise.all([attempt(api.runs()), runId ? attempt(api.run(runId)) : null])
  if (!runs.data) return <BackendProblem title="Can't load runs" message={runs.error} />

  return (
    <div className="grid gap-6 md:grid-cols-[17rem_minmax(0,1fr)] lg:grid-cols-[20rem_minmax(0,1fr)]">
      <aside aria-label="Runs" className={runId ? "hidden md:block" : undefined}>
        <RunList runs={runs.data} />
      </aside>
      <section aria-label="Run details" className={runId ? undefined : "hidden md:block"}>
        {!run ? (
          <div className="grid h-64 place-items-center rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            {runs.data.length ? "Select a run to see its progress and files." : "Your runs will show up here."}
          </div>
        ) : run.data ? (
          <RunDetail
            key={run.data.run_id}
            initial={run.data}
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
