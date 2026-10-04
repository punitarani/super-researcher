"use client"

import { Plus, Search } from "lucide-react"
import Link from "next/link"
import { useQueryStates } from "nuqs"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { formatTime } from "@/lib/labels"
import type { RunSummary } from "@/lib/schemas"
import { matchesFilter, RUN_FILTERS, runsHref, runsParams, type RunFilter } from "@/lib/search-params"
import { cn } from "@/lib/utils"

const FILTER_LABELS: Record<RunFilter, string> = { all: "All", active: "Active", completed: "Done", failed: "Failed", stopped: "Stopped" }

export function RunList({ runs }: { runs: RunSummary[] }) {
  const [{ q, state, run: selected }, setParams] = useQueryStates(runsParams)
  const visible = runs.filter((run) => matchesFilter(run, state, q))

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h1 className="text-xl font-semibold tracking-tight">Runs</h1>
        <Button asChild size="sm">
          <Link href="/new">
            <Plus /> New run
          </Link>
        </Button>
      </div>
      <div className="relative">
        <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          type="search"
          aria-label="Search runs by topic"
          placeholder="Search topics"
          className="pl-8"
          value={q}
          onChange={(event) => setParams({ q: event.target.value || null })}
        />
      </div>
      <ToggleGroup
        type="single"
        variant="outline"
        size="sm"
        aria-label="Filter runs by status"
        className="w-full"
        value={state}
        onValueChange={(value) => setParams({ state: (value || "all") as RunFilter })}
      >
        {RUN_FILTERS.map((filter) => (
          <ToggleGroupItem key={filter} value={filter} className="flex-1 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
            {FILTER_LABELS[filter]}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      {runs.length === 0 ? (
        <div className="rounded-lg border border-dashed p-6 text-center text-sm">
          <p className="font-medium">No runs yet</p>
          <p className="mt-1 text-muted-foreground">Start a run to build a research corpus.</p>
        </div>
      ) : visible.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No runs match these filters.</p>
      ) : (
        <ul className="space-y-1.5">
          {visible.map((run) => (
            <li key={run.run_id}>
              <Link
                href={runsHref({ q, state, run: run.run_id })}
                aria-current={run.run_id === selected ? "page" : undefined}
                className={cn(
                  "block rounded-lg border p-3 transition-colors hover:bg-accent",
                  run.run_id === selected && "border-primary/60 bg-accent",
                )}
              >
                <span className="line-clamp-2 text-sm font-medium break-words">{run.topic}</span>
                <span className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
                  <StatusBadge state={run.state} />
                  {formatTime(run.started_at)}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
