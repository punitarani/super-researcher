"use client"

import { ExternalLink } from "lucide-react"
import { useQueryStates } from "nuqs"
import { useMemo } from "react"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { runsParams } from "@/lib/search-params"
import { filterSources, type Source } from "@/lib/sources"
import { OpenButton } from "./doc-controls"

const SELECT = "h-9 rounded-md border bg-transparent px-2 text-sm"

/** Filters run in the browser and live in the URL, so a filtered view survives a reload. */
export function SourceList({ sources }: { sources: Source[] }) {
  const [filters, setFilters] = useQueryStates(
    { q: runsParams.sq, type: runsParams.stype, status: runsParams.sstatus, flagged: runsParams.flagged },
    { urlKeys: { q: "sq", type: "stype", status: "sstatus" }, throttleMs: 300 },
  )
  const types = useMemo(() => [...new Set(sources.map((source) => source.type))].sort(), [sources])
  const statuses = useMemo(() => [...new Set(sources.map((source) => source.status))].sort(), [sources])
  const shown = filterSources(sources, filters)

  if (!sources.length) return <p className="text-sm text-muted-foreground">No sources in this run.</p>
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-48 flex-1 space-y-1">
          <Label htmlFor="source-search">Search sources</Label>
          <Input id="source-search" type="search" placeholder="Title, URL or publisher" value={filters.q} onChange={(event) => setFilters({ q: event.target.value || null })} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="source-type">Type</Label>
          <select id="source-type" className={SELECT} value={filters.type} onChange={(event) => setFilters({ type: event.target.value || null })}>
            <option value="">All types</option>
            {types.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="source-status">Download</Label>
          <select id="source-status" className={SELECT} value={filters.status} onChange={(event) => setFilters({ status: event.target.value || null })}>
            <option value="">Any status</option>
            {statuses.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </div>
        <div className="flex h-9 items-center gap-2">
          <Checkbox id="source-flagged" checked={filters.flagged} onCheckedChange={(checked) => setFilters({ flagged: checked === true || null })} />
          <Label htmlFor="source-flagged">Flagged only</Label>
        </div>
      </div>

      <p className="text-sm text-muted-foreground" aria-live="polite">
        Showing {shown.length} of {sources.length} sources
      </p>
      {shown.length ? (
        <ol className="divide-y rounded-lg border">
          {shown.map((source) => (
            <li key={source.index} className="flex flex-wrap items-start gap-3 px-3 py-2 text-sm">
              <div className="min-w-0 flex-1 space-y-1">
                <a href={source.url} target="_blank" rel="noreferrer noopener" className="inline-flex max-w-full items-center gap-1 font-medium hover:underline">
                  <span className="truncate">
                    {source.index}. {source.title}
                  </span>
                  <ExternalLink className="size-3 shrink-0" aria-hidden />
                </a>
                <div className="text-xs text-muted-foreground">
                  {[source.publisher, source.type, source.score === null ? null : `score ${source.score}`, source.status].filter(Boolean).join(" · ")}
                </div>
                {(source.flags.length > 0 || source.notes.length > 0) && (
                  <div className="flex flex-wrap gap-1">
                    {source.flags.map((flag) => (
                      <Badge key={flag} variant="destructive" className="max-w-full whitespace-normal">
                        {flag}
                      </Badge>
                    ))}
                    {source.notes.map((note) => (
                      <Badge key={note} variant="secondary">
                        {note}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
              {source.textPath && <OpenButton viewer="src" value={source.index} label={`Read the text of source ${source.index}`} text="Read text" />}
            </li>
          ))}
        </ol>
      ) : (
        <p className="text-sm text-muted-foreground">No sources match these filters.</p>
      )}
    </div>
  )
}
