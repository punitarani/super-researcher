import type { Metadata } from "next"
import Link from "next/link"
import { BackendProblem } from "@/components/backend-problem"
import { InlineCode } from "@/components/inline-code"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { api, attempt } from "@/lib/backend"
import { SEARCH_PROVIDERS } from "@/lib/labels"
import { NewRunForm } from "./new-run-form"

export const metadata: Metadata = { title: "New run · SuperResearcher" }

export default async function NewRunPage() {
  const [config, agents] = await Promise.all([attempt(api.config()), attempt(api.agents())])
  if (!config.data) return <BackendProblem title="Can't start runs right now" message={config.error} />
  const agent = agents.data?.agents.find((item) => item.id === agents.data.selected)
  const hasSearch = SEARCH_PROVIDERS.some(({ key }) => config.data.configuredKeys.includes(key))

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">New research run</h1>
        <p className="text-muted-foreground">Plan the research, find and download sources, and check the corpus&apos;s quality.</p>
      </div>
      {agent && !agent.ready && (
        <Alert>
          <AlertTitle>{agent.label} isn&apos;t ready, so planning will use built-in defaults</AlertTitle>
          <AlertDescription>
            <p>
              <InlineCode text={agent.message} />{" "}
              <Link href="/agents" className="font-medium text-foreground underline underline-offset-4">
                Set up agents
              </Link>
            </p>
          </AlertDescription>
        </Alert>
      )}
      {!hasSearch && (
        <Alert>
          <AlertTitle>No search provider key, so discovery won&apos;t find sources</AlertTitle>
          <AlertDescription>
            <p>
              Add <code className="font-mono">EXA_API_KEY</code>, <code className="font-mono">SERPER_API_KEY</code> or{" "}
              <code className="font-mono">SERP_API_KEY</code> to <code className="font-mono">api_keys.txt</code>. The run still plans and writes its reports.
            </p>
          </AlertDescription>
        </Alert>
      )}
      <NewRunForm config={config.data} />
    </div>
  )
}
