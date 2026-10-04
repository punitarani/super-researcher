"use client"

import { useRouter } from "next/navigation"
import { useEffect, useState, useTransition } from "react"
import { recheckAgents, selectAgent, startCodexLogin, type ActionResult } from "@/app/actions"
import { InlineCode } from "@/components/inline-code"
import { AgentBadge } from "@/components/status"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardAction, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card"
import { SIGN_IN_STATES } from "@/lib/labels"
import type { Agents } from "@/lib/schemas"

const BILLING = { codex: "Uses your ChatGPT plan. No API key.", gemini: "Uses your Gemini API key." } as const
const SIGN_IN_WAIT_MS = 5 * 60 * 1000

export function AgentPicker({ agents }: { agents: Agents }) {
  const router = useRouter()
  const [pending, startTransition] = useTransition()
  const [error, setError] = useState<string | null>(null)
  const [signIn, setSignIn] = useState<{ url: string; until: number } | null>(null)
  const codexReady = agents.agents.some((agent) => agent.id === "codex" && agent.ready)
  const waiting = signIn && !codexReady

  // Codex finishes sign-in on its own; check every couple of seconds until it does.
  useEffect(() => {
    if (!waiting) return
    const timer = setInterval(() => (Date.now() > signIn.until ? setSignIn(null) : router.refresh()), 2000)
    return () => clearInterval(timer)
  }, [waiting, signIn, router])

  const act = (work: () => Promise<ActionResult>) =>
    startTransition(async () => {
      setError((await work()).error)
    })

  const beginSignIn = () =>
    act(async () => {
      const result = await startCodexLogin()
      if (result.url) setSignIn({ url: result.url, until: Date.now() + SIGN_IN_WAIT_MS })
      return result
    })

  return (
    <div className="space-y-4">
      {error && (
        <Alert variant="destructive" role="alert">
          <AlertTitle>That didn&apos;t work</AlertTitle>
          <AlertDescription>
            <p>
              <InlineCode text={error} />
            </p>
          </AlertDescription>
        </Alert>
      )}
      {waiting && (
        <Alert role="status">
          <AlertTitle>Finish signing in to ChatGPT in your browser</AlertTitle>
          <AlertDescription>
            <p>
              Codex opened the sign-in page. Didn&apos;t see it?{" "}
              <a href={signIn.url} target="_blank" rel="noopener noreferrer" className="font-medium underline underline-offset-4">
                Open the sign-in page
              </a>
              . This page updates once you&apos;re signed in.
            </p>
          </AlertDescription>
        </Alert>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        {agents.agents.map((agent) => {
          const selected = agent.id === agents.selected
          return (
            <Card key={agent.id} className={selected ? "ring-2 ring-primary/70" : undefined}>
              <CardHeader>
                <CardTitle>{agent.label}</CardTitle>
                <CardDescription>{BILLING[agent.id]}</CardDescription>
                <CardAction>
                  <AgentBadge agent={agent} />
                </CardAction>
              </CardHeader>
              <CardContent className="text-sm">
                <p>
                  <InlineCode text={agent.message} />
                </p>
              </CardContent>
              <CardFooter className="mt-auto flex-wrap gap-2">
                {selected ? (
                  <Button disabled variant="secondary">
                    In use
                  </Button>
                ) : (
                  <Button disabled={pending} onClick={() => act(() => selectAgent(agent.id))}>
                    Use {agent.label}
                  </Button>
                )}
                {agent.id === "codex" && SIGN_IN_STATES.has(agent.state) && (
                  <Button variant="outline" disabled={pending} onClick={beginSignIn}>
                    Sign in with ChatGPT
                  </Button>
                )}
                <Button variant="ghost" disabled={pending} onClick={() => act(recheckAgents)}>
                  {pending ? "Checking…" : "Re-check"}
                </Button>
              </CardFooter>
            </Card>
          )
        })}
      </div>
    </div>
  )
}
