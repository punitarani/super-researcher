import Link from "next/link"
import { MainNav } from "@/components/main-nav"
import { StatusDot } from "@/components/status"
import { api, attempt } from "@/lib/backend"
import { AGENT_STATE_LABELS } from "@/lib/labels"

export async function SiteHeader() {
  const { data } = await attempt(api.agents())
  const agent = data?.agents.find((item) => item.id === data.selected)
  return (
    <header className="border-b">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <Link href="/" className="font-semibold tracking-tight">
          SuperResearcher
        </Link>
        <MainNav />
        <Link
          href="/agents"
          className="ml-auto flex items-center gap-2 rounded-full border px-3 py-1 text-sm hover:bg-accent"
          aria-label={agent ? `Agent: ${agent.label}, ${AGENT_STATE_LABELS[agent.state]}. Manage agents` : "Backend offline. Open agents"}
        >
          <StatusDot tone={agent ? (agent.ready ? "ok" : "warn") : "bad"} />
          {agent ? `${agent.label} · ${AGENT_STATE_LABELS[agent.state]}` : "Backend offline"}
        </Link>
      </div>
    </header>
  )
}
