import type { Metadata } from "next"
import { BackendProblem } from "@/components/backend-problem"
import { StatusDot } from "@/components/status"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { api, attempt } from "@/lib/backend"
import { SEARCH_PROVIDERS } from "@/lib/labels"
import { AgentPicker } from "./agent-picker"

export const metadata: Metadata = { title: "Agents · SuperResearcher" }

export default async function AgentsPage() {
  const [agents, config] = await Promise.all([attempt(api.agents()), attempt(api.config())])
  const keys = new Set(config.data?.configuredKeys)
  const hasSearch = SEARCH_PROVIDERS.some(({ key }) => keys.has(key))
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
        <p className="text-muted-foreground">Choose which AI plans research, maps topics, and writes report sections.</p>
      </div>

      {agents.data ? <AgentPicker agents={agents.data} /> : <BackendProblem title="Can't check agents" message={agents.error} />}

      <Card>
        <CardHeader>
          <CardTitle>
            <h2>Search providers</h2>
          </CardTitle>
          <CardDescription>
            {hasSearch
              ? "Discovery searches with every provider that has a key."
              : "No search key yet, so discovery won't find new sources. Add one of these to api_keys.txt (see api_keys.example.txt). It's read on every run, so there's no restart."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ul className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            {SEARCH_PROVIDERS.map(({ key, label }) => (
              <li key={key} className="flex items-center gap-2">
                <StatusDot tone={keys.has(key) ? "ok" : "idle"} />
                {label}
                <span className="text-muted-foreground">
                  <code className="font-mono text-xs">{key}</code> {keys.has(key) ? "set" : "not set"}
                </span>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  )
}
