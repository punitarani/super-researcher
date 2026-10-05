import { Badge } from "@/components/ui/badge"
import { AGENT_STATE_LABELS, STATE_LABELS, type Tone } from "@/lib/labels"
import type { Agent } from "@/lib/schemas"
import { cn } from "@/lib/utils"

const DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  busy: "bg-sky-500 animate-pulse",
  warn: "bg-amber-500",
  bad: "bg-destructive",
  idle: "bg-muted-foreground",
}

export function StatusDot({ tone, className }: { tone: Tone; className?: string }) {
  return <span aria-hidden className={cn("inline-block size-2 shrink-0 rounded-full", DOT[tone], className)} />
}

export function StatusBadge({ state }: { state: string }) {
  const { label, tone } = STATE_LABELS[state] ?? { label: state, tone: "idle" }
  return (
    <Badge variant="outline" className="gap-1.5">
      <StatusDot tone={tone} />
      {label}
    </Badge>
  )
}

export function AgentBadge({ agent }: { agent: Agent }) {
  return (
    <Badge variant="outline" className="gap-1.5">
      <StatusDot tone={agent.ready ? "ok" : "warn"} />
      {AGENT_STATE_LABELS[agent.state]}
    </Badge>
  )
}
