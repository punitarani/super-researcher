"use client"

import { LoaderCircle, Play, RotateCw } from "lucide-react"
import Link from "next/link"
import { useQueryState } from "nuqs"
import { useState, useTransition } from "react"
import { startJob } from "@/app/actions"
import { InlineCode } from "@/components/inline-code"
import { StatusBadge, StatusDot } from "@/components/status"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { Progress } from "@/components/ui/progress"
import { isActive, jobSchema, type Job } from "@/lib/schemas"
import { runsParams } from "@/lib/search-params"
import type { Stage } from "@/lib/stages"
import { useLiveSnapshot } from "@/lib/use-live-snapshot"

export type StageNotes = { summary: string | null; done: boolean; next?: string; setup?: string[] }

type Props = {
  stage: Stage
  step: number
  corpusId: string
  notes: StageNotes
  agentProblem: string | null
  classicUrl: string
  initialJob: Job | null
  lostJob: boolean
}

const maybeJob = jobSchema.nullable()

export function JobCard(props: Props) {
  const [job, setJob] = useState(props.initialJob)
  // A new job gets a fresh card body, so its live view starts from that job.
  return <JobCardBody key={job?.job_id ?? "idle"} {...props} started={job} onStarted={setJob} />
}

function JobCardBody({ stage, step, corpusId, notes, agentProblem, classicUrl, lostJob, started, onStarted }: Props & { started: Job | null; onStarted: (job: Job) => void }) {
  const url = started && `/api/stream/${stage.kind}/${encodeURIComponent(started.job_id)}`
  const { data: job, error: streamError } = useLiveSnapshot(url, maybeJob, started)
  const [, setWatched] = useQueryState("jobs", runsParams.jobs)
  const [force, setForce] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, startTransition] = useTransition()
  const running = !!job && isActive(job.state)
  const counts = Object.entries(job?.counts ?? {}).filter(([, value]) => typeof value === "number" || typeof value === "string")

  const start = () =>
    startTransition(async () => {
      const result = await startJob(stage.kind, corpusId, force)
      setError(result.error)
      if (!result.job) return
      const entry = `${stage.kind}:${result.job.job_id}`
      await setWatched((current) => [...current.filter((item) => !item.startsWith(`${stage.kind}:`)), entry])
      onStarted(result.job)
    })

  return (
    <Card className="gap-4">
      <CardHeader>
        <CardTitle>
          <h3 className="flex items-center gap-2">
            <span className="grid size-6 place-items-center rounded-full bg-muted text-xs tabular-nums" aria-hidden>
              {step}
            </span>
            {stage.title}
          </h3>
        </CardTitle>
        <CardDescription>{stage.description}</CardDescription>
        <CardAction>
          {job ? (
            <StatusBadge state={job.state} />
          ) : notes.done ? (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <StatusDot tone="ok" /> Done
            </span>
          ) : null}
        </CardAction>
      </CardHeader>

      <CardContent className="space-y-3 text-sm empty:hidden">
        {notes.summary && (
          <p className="text-muted-foreground break-words">
            <InlineCode text={notes.summary} />
          </p>
        )}
        {agentProblem && !running && (
          <p className="text-muted-foreground">
            Uses the agent, which isn&apos;t ready ({agentProblem}).{" "}
            <Link href="/agents" className="font-medium text-foreground underline underline-offset-4">
              Set up agents
            </Link>
          </p>
        )}
        {notes.setup && (
          <Alert>
            <AlertTitle>Needs extra packages</AlertTitle>
            <AlertDescription>
              <p>From the repo root:</p>
              <ul className="list-none space-y-1">
                {notes.setup.map((line) => (
                  <li key={line}>
                    <InlineCode text={line.endsWith(".") ? line : `\`${line}\``} />
                  </li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        )}
        {job && job.state !== "failed" && (running || job.state === "completed") && (
          <div className="space-y-2">
            <div className="flex justify-between gap-3">
              <span aria-live="polite">{job.stage || "Queued"}</span>
              <span className="text-muted-foreground tabular-nums">{Math.round(job.progress)}%</span>
            </div>
            <Progress value={job.progress} aria-label={`${stage.title} progress`} />
            {!!counts.length && (
              <p className="text-xs text-muted-foreground">
                {counts
                  .slice(0, 6)
                  .map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`)
                  .join(" · ")}
              </p>
            )}
          </div>
        )}
        {(job?.state === "failed" || error) && (
          <Alert variant="destructive" role="alert">
            <AlertTitle>{error ? `Couldn't start ${stage.title.toLowerCase()}` : `${stage.title} failed`}</AlertTitle>
            <AlertDescription>
              <p>
                <InlineCode text={error ?? job?.error ?? "No details from the backend."} />
              </p>
              {error && agentProblem ? (
                <Link href="/agents" className="font-medium text-foreground underline underline-offset-4">
                  Set up agents
                </Link>
              ) : (
                job?.state === "failed" && stage.classicTab && <ClassicLink url={classicUrl} />
              )}
            </AlertDescription>
          </Alert>
        )}
        {streamError && <p className="text-muted-foreground">{streamError}</p>}
        {lostJob && !job && <p className="text-muted-foreground">The job this link followed is gone, probably because the backend restarted.</p>}
        {notes.next && stage.classicTab && !running && (
          <p>
            Next: {notes.next} in the classic UI&apos;s {stage.classicTab} tab. <ClassicLink url={classicUrl} />
          </p>
        )}
      </CardContent>

      <CardFooter className="flex-wrap gap-x-4 gap-y-2">
        <Button size="sm" variant={notes.done ? "outline" : "default"} disabled={running || pending} onClick={start}>
          {running || pending ? <LoaderCircle className="animate-spin" /> : notes.done ? <RotateCw /> : <Play />}
          {running ? "Running…" : pending ? "Starting…" : notes.done ? "Run again" : "Run"}
        </Button>
        {stage.force && (
          <div className="flex items-center gap-2">
            <Checkbox id={`${stage.kind}-force`} checked={force} onCheckedChange={(value) => setForce(value === true)} disabled={running} />
            <Label htmlFor={`${stage.kind}-force`} className="font-normal">
              {stage.force}
            </Label>
          </div>
        )}
      </CardFooter>
    </Card>
  )
}

function ClassicLink({ url }: { url: string }) {
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className="font-medium text-foreground underline underline-offset-4">
      Open the classic UI
    </a>
  )
}
