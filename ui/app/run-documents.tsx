import { CopyButton } from "@/components/copy-button"
import { JsonTree } from "@/components/json-tree"
import { Markdown } from "@/components/markdown"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent } from "@/components/ui/card"
import { api, describeError } from "@/lib/backend"
import { FIRST_CHUNK, MAX_BYTES, readRunFile, RunFileError, runFileExists, runFolder } from "@/lib/run-files"
import { briefSchema, type Run } from "@/lib/schemas"
import { CloseDocButton, ShowMoreButton } from "./doc-controls"

// Server components that read a run's own files from disk (see lib/run-files.ts).

const REPORT_FILES = [
  ["report", "atlas/publish/report.md"],
  ["paper", "atlas/publish/paper.md"],
] as const

/** Like attempt() in lib/backend, but file problems keep their own (user-facing) message. */
async function settle<T>(promise: Promise<T>): Promise<{ data: T; error: null } | { data: null; error: string }> {
  try {
    return { data: await promise, error: null }
  } catch (error) {
    return { data: null, error: error instanceof RunFileError ? error.message : describeError(error) }
  }
}

async function locate(run: Run) {
  const config = await api.config()
  return runFolder(config.default_storage_root, run.dossier_path, run.run_id)
}

/** Report files the pipeline has written, for the Artifacts list (they aren't in run.files). */
export async function publishedFiles(run: Run): Promise<[string, string][]> {
  const folder = await settle(locate(run))
  if (!folder.data) return []
  const found = await Promise.all(REPORT_FILES.map(async ([label, file]) => ((await runFileExists(folder.data, file)) ? [label, `${run.dossier_path}/${file}`] : null)))
  return found.filter((row): row is [string, string] => row !== null)
}

const BRIEF_FIELDS = [
  ["Context", "context"],
  ["Must include", "must_include"],
  ["Must exclude", "must_exclude"],
  ["Preferred sources", "preferred_sources"],
] as const
const OPTIONAL_FIELDS = [
  ["Disallowed sources", "disallowed_sources"],
  ["Audience", "audience"],
  ["Geographic scope", "geographic_scope"],
  ["Time horizon", "time_horizon"],
  ["Objective", "objective"],
] as const
const AGENTS: Record<string, string> = { codex: "Codex · ChatGPT plan", gemini: "Gemini · API key" }

const humanize = (value: string) => (value ? value[0].toUpperCase() + value.slice(1).replaceAll("_", " ") : "—")

/** What the run was asked to do, from its settings.json. */
export async function RunBrief({ run }: { run: Run }) {
  const folder = await settle(locate(run))
  const file = folder.data ? await settle(readRunFile(folder.data, "settings.json")) : null
  let raw: unknown = null
  try {
    raw = file?.data ? JSON.parse(file.data.text) : null
  } catch {
    raw = null
  }
  if (!raw) {
    return <p className="text-sm text-muted-foreground">{folder.error ?? "The brief appears once the run starts."}</p>
  }
  const brief = briefSchema.parse(raw)
  const agent = brief.agent ? (AGENTS[brief.agent] ?? brief.agent) : "—"
  const rows: [string, string][] = [
    ["Topic", brief.topic],
    ["Depth", humanize(brief.depth)],
    ["Breadth", humanize(brief.breadth)],
    ["Final sources", brief.final_source_count === null ? "—" : String(brief.final_source_count)],
    ...BRIEF_FIELDS.map(([label, key]): [string, string] => [label, brief[key] || "—"]),
    ...OPTIONAL_FIELDS.filter(([, key]) => brief[key]).map(([label, key]): [string, string] => [label, brief[key]]),
    ["Agent", agent],
    ["Keys set", brief.keyNames.length ? brief.keyNames.join(", ") : "None"],
  ]
  return (
    <details className="group rounded-lg border px-4 py-3 text-sm">
      <summary className="cursor-pointer select-none font-medium">
        Brief <span className="font-normal text-muted-foreground">· {humanize(brief.depth)} depth · {humanize(brief.breadth)} breadth · {agent}</span>
      </summary>
      <dl className="mt-3 grid gap-x-4 gap-y-2 sm:grid-cols-[10rem_minmax(0,1fr)]">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="break-words whitespace-pre-wrap">{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}

/** One run file, rendered by type. Read only when opened, in steps up to MAX_BYTES. */
export async function DocumentView({ run, doc, more }: { run: Run; doc: string; more: number }) {
  const folder = await settle(locate(run))
  if (folder.data === null) return <DocProblem doc={doc} error={folder.error} />
  const file = await settle(readRunFile(folder.data, doc, FIRST_CHUNK * 2 ** Math.min(Math.max(more, 0), 8)))
  if (file.data === null) return <DocProblem doc={doc} error={file.error} />
  const { relativePath, text, size, truncated, limit } = file.data
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="min-w-0 flex-1 truncate font-mono text-sm font-medium" title={relativePath}>
            {relativePath}
          </h3>
          <span className="text-xs text-muted-foreground">{formatBytes(size)}</span>
          <CopyButton value={`${run.dossier_path}/${relativePath}`} label="Copy path" />
          <CloseDocButton />
        </div>
        <div className="max-h-[70vh] overflow-auto rounded-md border p-3">
          <Rendered relativePath={relativePath} text={text} truncated={truncated} />
        </div>
        {truncated && (
          <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
            <span>
              Showing the first {formatBytes(limit)} of {formatBytes(size)}.
            </span>
            {limit < MAX_BYTES ? <ShowMoreButton /> : <span>Open the file from disk to see the rest.</span>}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function DocProblem({ doc, error }: { doc: string; error: string }) {
  return (
    <Alert variant="destructive">
      <AlertTitle className="flex items-center justify-between gap-2">
        <span className="min-w-0 truncate">Can&apos;t show {doc}</span>
        <CloseDocButton />
      </AlertTitle>
      <AlertDescription>{error}</AlertDescription>
    </Alert>
  )
}

function Rendered({ relativePath, text, truncated }: { relativePath: string; text: string; truncated: boolean }) {
  const extension = relativePath.slice(relativePath.lastIndexOf(".")).toLowerCase()
  if (extension === ".md" || extension === ".markdown") return <Markdown text={text} />
  if (extension === ".jsonl") {
    // A cut-off last line can't be parsed, so drop it.
    const lines = text.split("\n").slice(0, truncated ? -1 : undefined).filter((line) => line.trim())
    const rows = lines.map(parseJson)
    if (rows.every((row) => row.ok)) return <JsonTree value={rows.map((row) => row.value)} />
  }
  if (extension === ".json" && !truncated) {
    const parsed = parseJson(text)
    if (parsed.ok) return <JsonTree value={parsed.value} />
  }
  return <pre className="font-mono text-xs whitespace-pre-wrap">{text}</pre>
}

function parseJson(text: string): { ok: true; value: unknown } | { ok: false } {
  try {
    return { ok: true, value: JSON.parse(text) }
  } catch {
    return { ok: false }
  }
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
