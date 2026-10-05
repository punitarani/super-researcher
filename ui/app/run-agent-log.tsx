import Link from "next/link"
import { CopyButton } from "@/components/copy-button"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent } from "@/components/ui/card"
import { AGENT_LOG_INDEX, callFiles, parseAgentLog, summarizeCalls, TOPIC_PROMPT, type AgentCall } from "@/lib/agent-log"
import { api, attempt } from "@/lib/backend"
import { formatTime } from "@/lib/labels"
import { MAX_BYTES, readRunFile, runFileExists } from "@/lib/run-files"
import { isActive, type Run } from "@/lib/schemas"
import { CloseButton, OpenButton, RefreshButton } from "./doc-controls"
import { FileBody, locate, readStep, settle } from "./run-documents"

const OUTCOMES: Record<AgentCall["outcome"], { label: string; variant: "secondary" | "outline" | "destructive" }> = {
  answered: { label: "Answered", variant: "secondary" },
  fell_back: { label: "Used built-in defaults", variant: "outline" },
  failed: { label: "Failed", variant: "destructive" },
}

/** The run's agent calls as a timeline. Prompts and replies are read only for the call that's opened. */
export async function RunAgentLog({ run, call, more }: { run: Run; call: string | null; more: number }) {
  const [folder, settings] = await Promise.all([settle(locate(run)), attempt(api.settings())])
  if (folder.data === null) return <p className="text-sm text-destructive">{folder.error}</p>
  const [index, hasTopicPrompt] = await Promise.all([settle(readRunFile(folder.data, AGENT_LOG_INDEX, MAX_BYTES)), runFileExists(folder.data, TOPIC_PROMPT)])
  const calls = index.data ? parseAgentLog(index.data.text, index.data.truncated) : []
  const counts = summarizeCalls(calls)
  const location = `${run.dossier_path}/${AGENT_LOG_INDEX}`

  return (
    <div className="space-y-4 text-sm">
      <div className="space-y-1 rounded-lg border px-3 py-2">
        <div className="flex items-center gap-2">
          <p className="min-w-0 flex-1">
            Kept on this computer at <code className="font-mono text-xs break-all">{location}</code>
          </p>
          <CopyButton value={location} label="Copy log path" />
        </div>
        <p className="text-muted-foreground">
          Logging is {settings.data ? (settings.data.agent_log ? "on" : "off") : "unknown"} for new agent calls.{" "}
          <Link href="/agents#agent-log" className="underline underline-offset-2">
            Change it on the Agents page
          </Link>
          . Credentials are removed before anything is saved.
        </p>
      </div>

      {isActive(run.state) && (
        <div className="flex flex-wrap items-center gap-3 text-muted-foreground">
          <span>This run is still going, so new calls appear when it finishes or when you refresh.</span>
          <RefreshButton />
        </div>
      )}

      {call !== null && <CallView folder={folder.data} call={call} calls={calls} complete={!index.data?.truncated} more={more} />}

      {calls.length > 0 && (
        <p className="text-muted-foreground">
          {counts.total} agent calls: {counts.answered} answered, {counts.fell_back} used built-in defaults, {counts.failed} failed
          {counts.not_sent > 0 && ` (${counts.not_sent} never sent to the agent)`}.
        </p>
      )}
      {index.data?.truncated && <p className="text-muted-foreground">The log is larger than 2 MB, so only its first {calls.length} calls are listed.</p>}

      {calls.length === 0 && !hasTopicPrompt ? (
        <p className="text-muted-foreground">No agent calls logged for this run yet.</p>
      ) : (
        <ol className="divide-y rounded-lg border">
          {calls.map((entry) => (
            <li key={entry.call} className="flex flex-wrap items-start gap-3 px-3 py-2">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {entry.call}. {entry.step}
                  </span>
                  <Badge variant={OUTCOMES[entry.outcome].variant}>{OUTCOMES[entry.outcome].label}</Badge>
                  {!entry.sent && <Badge variant="outline">Not sent</Badge>}
                </div>
                <div className="text-xs text-muted-foreground">
                  {[formatTime(entry.time), entry.agent && `${entry.agent} (${entry.model})`, entry.sent ? `${(entry.duration_ms / 1000).toFixed(1)} s` : null, `${entry.prompt_chars.toLocaleString()} → ${entry.reply_chars.toLocaleString()} characters`].filter(Boolean).join(" · ")}
                </div>
                {entry.reason && <p className="text-xs break-words text-muted-foreground">Why: {entry.reason}</p>}
              </div>
              <OpenButton viewer="call" value={String(entry.call)} label={`call ${entry.call}`} />
            </li>
          ))}
          {hasTopicPrompt && (
            <li className="flex flex-wrap items-start gap-3 px-3 py-2">
              <div className="min-w-0 flex-1 space-y-1">
                <span className="font-medium">Topic discovery: saved prompt</span>
                <div className="text-xs text-muted-foreground">Saved by topic discovery in {TOPIC_PROMPT}, with the outline it produced</div>
              </div>
              <OpenButton viewer="call" value="topics" label="topic discovery prompt" />
            </li>
          )}
        </ol>
      )}
    </div>
  )
}

async function CallView({ folder, call, calls, complete, more }: { folder: string; call: string; calls: AgentCall[]; complete: boolean; more: number }) {
  const entry = calls.find((item) => String(item.call) === call)
  // A numbered call must be in the log, unless the log was too large to read in full.
  const files = call === "topics" || entry || !complete ? callFiles(call) : null
  const title = call === "topics" ? "Topic discovery: saved prompt" : entry ? `${entry.call}. ${entry.step}` : `Call ${call}`
  const [prompt, reply] = files ? await Promise.all([readStep(folder, files.prompt, more), readStep(folder, files.reply, more)]) : [null, null]
  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-2">
          <h3 className="min-w-0 flex-1 truncate font-medium">{title}</h3>
          <CloseButton viewer="call" label="Close call" />
        </div>
        {!files || !prompt || !reply ? (
          <p className="text-muted-foreground">There&apos;s no such call in this run.</p>
        ) : (
          <>
            <section className="space-y-2">
              <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Prompt</h4>
              {prompt.data ? <FileBody file={prompt.data} plainText /> : <p className="text-muted-foreground">{prompt.error}</p>}
            </section>
            <section className="space-y-2">
              <h4 className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Reply</h4>
              {reply.data ? (
                reply.data.text ? <FileBody file={reply.data} plainText /> : <p className="text-muted-foreground">No reply.</p>
              ) : (
                <p className="text-muted-foreground">{call === "topics" ? "No outline was saved." : reply.error}</p>
              )}
            </section>
          </>
        )}
      </CardContent>
    </Card>
  )
}
