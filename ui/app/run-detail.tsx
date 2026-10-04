"use client"

import { ArrowLeft, LoaderCircle, Square } from "lucide-react"
import Link from "next/link"
import { useQueryState, useQueryStates } from "nuqs"
import { useState, useTransition, type ReactNode } from "react"
import { stopRun } from "@/app/actions"
import { CopyButton } from "@/components/copy-button"
import { InlineCode } from "@/components/inline-code"
import { StatusBadge } from "@/components/status"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { formatTime } from "@/lib/labels"
import { isActive, runSchema, type Run } from "@/lib/schemas"
import { runsHref, runsParams, type RunTab } from "@/lib/search-params"
import { useLiveSnapshot } from "@/lib/use-live-snapshot"

const METRICS = [
  ["Candidates", "candidate_sources"],
  ["Selected", "selected_sources"],
  ["Ingested", "ingested_sources"],
] as const

export function RunDetail({ initial, pipeline }: { initial: Run; pipeline: ReactNode }) {
  const { data: run, error } = useLiveSnapshot(`/api/stream/run/${encodeURIComponent(initial.run_id)}`, runSchema, initial)
  const [{ q, state }] = useQueryStates(runsParams)
  // Switching tabs re-renders on the server so the Pipeline tab loads only when opened.
  const [tab, setTab] = useQueryState("tab", runsParams.tab.withOptions({ shallow: false }))

  return (
    <article className="space-y-4">
      <Link href={runsHref("/", { q, state })} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground md:hidden">
        <ArrowLeft className="size-4" /> All runs
      </Link>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-xl font-semibold tracking-tight break-words">{run.topic}</h2>
          <p className="text-sm text-muted-foreground">
            Started {formatTime(run.started_at)}
            {run.completed_at && ` · Finished ${formatTime(run.completed_at)}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge state={run.state} />
          {isActive(run.state) && <StopButton runId={run.run_id} stopRequested={run.stop_requested} />}
        </div>
      </header>

      <Card>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="font-medium" aria-live="polite">
                {run.milestone ?? "Queued"}
              </span>
              <span className="text-muted-foreground tabular-nums">{Math.round(run.progress)}%</span>
            </div>
            <Progress value={run.progress} aria-label="Run progress" />
          </div>
          <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
            {METRICS.map(([label, key]) => (
              <div key={key}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="font-medium tabular-nums">{String(run.counts[key] ?? 0)}</dd>
              </div>
            ))}
            <div>
              <dt className="text-muted-foreground">Quality</dt>
              <dd className="font-medium">{run.quality?.verdict ?? "—"}</dd>
            </div>
          </dl>
        </CardContent>
      </Card>

      {run.error && (
        <Alert variant="destructive">
          <AlertTitle>The run failed</AlertTitle>
          <AlertDescription>
            <p>
              <InlineCode text={run.error} />
            </p>
          </AlertDescription>
        </Alert>
      )}
      {run.state === "interrupted" && (
        <Alert>
          <AlertTitle>This run was interrupted</AlertTitle>
          <AlertDescription>The app stopped while it was running. Its files so far are kept; start a new run to continue.</AlertDescription>
        </Alert>
      )}
      {error && (
        <Alert>
          <AlertTitle>Live updates stopped</AlertTitle>
          <AlertDescription>
            <p>
              <InlineCode text={error} />
            </p>
          </AlertDescription>
        </Alert>
      )}

      <Tabs value={tab} onValueChange={(value) => setTab(value as RunTab)}>
        <TabsList>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          <TabsTrigger value="artifacts">Artifacts</TabsTrigger>
          <TabsTrigger value="pipeline">Pipeline</TabsTrigger>
        </TabsList>
        <TabsContent value="activity">
          <EventLog events={run.events} />
        </TabsContent>
        <TabsContent value="artifacts">
          <Artifacts run={run} />
        </TabsContent>
        <TabsContent value="pipeline">{pipeline ?? <Skeleton className="h-64" />}</TabsContent>
      </Tabs>
    </article>
  )
}

function StopButton({ runId, stopRequested }: { runId: string; stopRequested: boolean }) {
  const [pending, startTransition] = useTransition()
  const [requested, setRequested] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const stopping = stopRequested || requested || pending
  return (
    <>
      <Button
        variant="outline"
        size="sm"
        disabled={stopping}
        className={stopping ? "text-muted-foreground disabled:opacity-100" : undefined}
        onClick={() =>
          startTransition(async () => {
            const result = await stopRun(runId)
            setError(result.error)
            setRequested(!result.error)
          })
        }
      >
        {stopping ? <LoaderCircle className="animate-spin" /> : <Square />}
        {stopping ? "Stopping after current step…" : "Stop"}
      </Button>
      {error && (
        <p role="alert" className="basis-full text-right text-sm text-destructive">
          {error}
        </p>
      )}
    </>
  )
}

function EventLog({ events }: { events: Run["events"] }) {
  if (!events.length) return <p className="py-6 text-sm text-muted-foreground">No updates yet.</p>
  return (
    <ol className="max-h-[28rem] divide-y overflow-y-auto rounded-lg border text-sm" aria-label="Run updates, newest first">
      {events.toReversed().map((event, index) => (
        <li key={events.length - index} className="flex gap-3 px-3 py-2">
          <time className="shrink-0 text-muted-foreground tabular-nums">{event.time.slice(11, 19)}</time>
          <span className="min-w-0 break-words">
            <InlineCode text={event.message} />
          </span>
        </li>
      ))}
    </ol>
  )
}

function Artifacts({ run }: { run: Run }) {
  const files = Object.entries(run.files)
  return (
    <div className="space-y-4 text-sm">
      <PathRow label="Run folder" path={run.dossier_path} />
      {files.length ? (
        <ul className="divide-y rounded-lg border">
          {files.map(([name, path]) => (
            <li key={name} className="px-3 py-1">
              <PathRow label={name.replaceAll("_", " ")} path={path} shown={path.startsWith(`${run.dossier_path}/`) ? path.slice(run.dossier_path.length + 1) : path} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">Files appear here as the run writes them.</p>
      )}
      {!!run.quality?.warnings.length && (
        <div>
          <h3 className="font-medium">Quality warnings</h3>
          <ul className="mt-1 list-disc pl-5 text-muted-foreground">
            {run.quality.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function PathRow({ label, path, shown = path }: { label: string; path: string; shown?: string }) {
  return (
    <div className="flex items-center gap-2">
      <div className="min-w-0 flex-1">
        <div className="font-medium capitalize">{label}</div>
        <code className="block truncate font-mono text-xs text-muted-foreground" title={path}>
          {shown}
        </code>
      </div>
      <CopyButton value={path} label={`Copy ${label} path`} />
    </div>
  )
}
