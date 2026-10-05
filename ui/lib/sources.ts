import { z } from "zod"
import { relativeToRun } from "./run-paths"

// Sources from a run's ingested_sources.jsonl, reduced to what the Sources tab shows.

const sourceRowSchema = z.object({
  url: z.string().catch(""),
  title: z.string().catch(""),
  publisher: z.string().catch(""),
  source_type: z.string().catch("unknown"),
  fetch_status: z.string().catch("unknown"),
  fetch_error: z.string().nullish().catch(null),
  markdown_status: z.string().nullish().catch(null),
  markdown_path: z.string().nullish().catch(null),
  score: z.number().nullish().catch(null),
  conversion_notes: z.array(z.string()).catch([]),
})

export type Source = {
  index: number
  url: string
  /** The URL when it's a web address, the only kind the list links to. */
  link: string | null
  title: string
  publisher: string
  type: string
  status: string
  markdownStatus: string | null
  score: number | null
  /** Problems worth a look. */
  flags: string[]
  /** Other conversion steps, for context. */
  notes: string[]
  /** The extracted text, relative to the run folder, if it's inside it. */
  textPath: string | null
}

export type SourceFilters = { q: string; type: string; status: string; flagged: boolean }

const PROBLEM_NOTES: Record<string, string> = {
  pdf_fallback_text_only: "PDF text extracted roughly",
  postprocess_newline_repair_failed: "Text repair failed",
  still_failed: "Re-fetch failed",
}

export function parseSources(text: string, truncated: boolean, runFolder: string): Source[] {
  // A file cut off by the read limit ends in a partial line; leave it out.
  const lines = text.split("\n").slice(0, truncated ? -1 : undefined)
  const sources: Source[] = []
  for (const line of lines) {
    if (!line.trim()) continue
    let raw: unknown
    try {
      raw = JSON.parse(line)
    } catch {
      continue
    }
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue
    const row = sourceRowSchema.parse(raw)
    const flags = [
      ...(row.fetch_status === "downloaded" ? [] : [`Not downloaded${row.fetch_error ? `: ${row.fetch_error}` : ""}`]),
      ...(row.markdown_status && row.markdown_status !== "created" ? [`Markdown ${row.markdown_status}`] : []),
      ...row.conversion_notes.filter((note) => note in PROBLEM_NOTES).map((note) => PROBLEM_NOTES[note]),
    ]
    sources.push({
      index: sources.length + 1,
      url: row.url,
      link: /^https?:\/\//i.test(row.url) ? row.url : null,
      title: row.title || row.url,
      publisher: row.publisher,
      type: row.source_type,
      status: row.fetch_status,
      markdownStatus: row.markdown_status ?? null,
      score: row.score ?? null,
      flags,
      notes: row.conversion_notes.filter((note) => !(note in PROBLEM_NOTES)).map(humanizeNote),
      textPath: row.markdown_path ? relativeToRun(row.markdown_path, runFolder) : null,
    })
  }
  return sources
}

export function filterSources(sources: Source[], { q, type, status, flagged }: SourceFilters) {
  const query = q.trim().toLowerCase()
  return sources.filter(
    (source) =>
      (!query || [source.title, source.url, source.publisher].some((field) => field.toLowerCase().includes(query))) &&
      (!type || source.type === type) &&
      (!status || source.status === status) &&
      (!flagged || source.flags.length > 0),
  )
}

function humanizeNote(note: string) {
  const text = note.replace(/^postprocess_/, "").replaceAll("_", " ")
  return text.charAt(0).toUpperCase() + text.slice(1)
}
