"use client"

import { useState, useTransition } from "react"
import { setAgentLog } from "@/app/actions"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import type { Settings } from "@/lib/schemas"

export function AgentLogSetting({ settings }: { settings: Settings }) {
  const [pending, startTransition] = useTransition()
  const [error, setError] = useState<string | null>(null)
  const change = (enabled: boolean) =>
    startTransition(async () => {
      setError((await setAgentLog(enabled)).error)
    })
  return (
    <div className="space-y-2 text-sm">
      <div className="flex items-center gap-2">
        <Checkbox id="agent-log-enabled" checked={settings.agent_log} disabled={pending} onCheckedChange={(checked) => change(checked === true)} />
        <Label htmlFor="agent-log-enabled">Keep a log of each agent prompt and reply</Label>
      </div>
      <p className="text-muted-foreground">
        Stored on this computer in each run&apos;s folder: <code className="font-mono text-xs break-all">{settings.agent_log_location}</code>. Credentials are
        removed before anything is saved. Turning this off stops new logging and keeps existing logs.
      </p>
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
