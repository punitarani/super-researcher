import { CopyButton } from "@/components/copy-button"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent } from "@/components/ui/card"
import { MAX_BYTES, readRunFile } from "@/lib/run-files"
import type { Run } from "@/lib/schemas"
import { parseSources } from "@/lib/sources"
import { CloseButton } from "./doc-controls"
import { FileBody, locate, readStep, settle } from "./run-documents"
import { SourceList } from "./source-list"

const SOURCES_FILE = "ingested_sources.jsonl"

/** The run's sources, with the open source's extracted text. Read only when the tab is open. */
export async function RunSources({ run, src, more }: { run: Run; src: number | null; more: number }) {
  const folder = await settle(locate(run))
  if (folder.data === null) return <Problem title="Can't show sources" error={folder.error} />
  const file = await settle(readRunFile(folder.data, SOURCES_FILE, MAX_BYTES))
  if (file.data === null) {
    return <p className="text-sm text-muted-foreground">Sources appear here once the run has downloaded them.</p>
  }
  const sources = parseSources(file.data.text, file.data.truncated, run.dossier_path)
  const open = src === null ? null : (sources.find((source) => source.index === src) ?? null)
  const text = open?.textPath ? await readStep(folder.data, open.textPath, more) : null

  return (
    <div className="space-y-4">
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

function Problem({ title, error }: { title: string; error: string }) {
  return (
    <Alert variant="destructive">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{error}</AlertDescription>
    </Alert>
  )
}
