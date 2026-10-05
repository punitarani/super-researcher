import { BackendProblem } from "@/components/backend-problem"
import { CopyButton } from "@/components/copy-button"
import { Card, CardContent } from "@/components/ui/card"
import { MAX_BYTES, readRunFile } from "@/lib/run-files"
import { isActive, type Run } from "@/lib/schemas"
import { parseSources } from "@/lib/sources"
import { CloseButton, RefreshButton } from "./doc-controls"
import { FileBody, locate, readStep, settle } from "./run-documents"
import { SourceList } from "./source-list"

const SOURCES_FILE = "ingested_sources.jsonl"

/** The run's sources, with the open source's extracted text. Read only when the tab is open. */
export async function RunSources({ run, src, more }: { run: Run; src: number | null; more: number }) {
  const folder = await settle(locate(run))
  if (folder.data === null) return <BackendProblem title="Can't show sources" message={folder.error} />
  const file = await settle(readRunFile(folder.data, SOURCES_FILE, MAX_BYTES))
  const stillGoing = isActive(run.state) && (
    <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground">
      <span>This run is still going, so sources appear once it has downloaded them, or when you refresh.</span>
      <RefreshButton />
    </div>
  )
  if (file.data === null) {
    return stillGoing || <p className="text-sm text-muted-foreground">This run has no downloaded sources.</p>
  }
  const sources = parseSources(file.data.text, file.data.truncated, run.dossier_path)
  const open = src === null ? null : (sources.find((source) => source.index === src) ?? null)
  const text = open?.textPath ? await readStep(folder.data, open.textPath, more) : null

  return (
    <div className="space-y-4">
      {stillGoing}
      {src !== null && (
        <Card>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="min-w-0 flex-1 truncate text-sm font-medium" title={open?.title}>
                {open ? `${open.index}. ${open.title}` : `Source ${src}`}
              </h3>
              {open?.textPath && <CopyButton value={`${run.dossier_path}/${open.textPath}`} label="Copy path" />}
              <CloseButton viewer="src" label="Close source text" />
            </div>
            {!open ? (
              <p className="text-sm text-muted-foreground">There&apos;s no source {src} in this run.</p>
            ) : text === null ? (
              <p className="text-sm text-muted-foreground">This source has no extracted text in the run folder.</p>
            ) : text.data === null ? (
              <p className="text-sm text-destructive">{text.error}</p>
            ) : (
              <FileBody file={text.data} />
            )}
          </CardContent>
        </Card>
      )}
      {file.data.truncated && (
        <p className="text-sm text-muted-foreground">
          The source list is larger than {MAX_BYTES / 1024 / 1024} MB, so only the first {sources.length} sources are shown.
        </p>
      )}
      <SourceList sources={sources} />
    </div>
  )
}
