"use client"

import { LoaderCircle } from "lucide-react"
import { useActionState, useState } from "react"
import { startRun, type StartRunState } from "@/app/actions"
import { InlineCode } from "@/components/inline-code"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { BREADTHS, DEPTHS, type Config, type NewRun } from "@/lib/schemas"

const DEPTH_LABELS: Record<(typeof DEPTHS)[number], string> = { low: "Low", medium: "Medium", high: "High", extra_high: "Extra high", ludicrous: "Ludicrous" }
const BREADTH_LABELS: Record<(typeof BREADTHS)[number], string> = { low: "Low", medium: "Medium", high: "High" }
// The character limits match newRunSchema, so the browser stops input the server would reject.
const SCOPE_FIELDS: [keyof NewRun, string, string, number][] = [
  ["audience", "Audience", "Executives, founders, engineers", 300],
  ["geographic_scope", "Geographic scope", "Global, US, EU, India", 300],
  ["time_horizon", "Time horizon", "Current state plus 3–5 year outlook", 300],
  ["objective", "Objective", "Decision-grade corpus for a strategy memo", 500],
]
const LIST_FIELDS: [keyof NewRun, string][] = [
  ["must_include", "Must include"],
  ["must_exclude", "Must exclude"],
  ["preferred_sources", "Preferred sources"],
  ["disallowed_sources", "Disallowed sources"],
]
const INITIAL: StartRunState = { error: null, fieldErrors: {}, values: {} }

export function NewRunForm({ config }: { config: Config }) {
  const [state, formAction, pending] = useActionState(startRun, INITIAL)
  const [depth, setDepth] = useState(state.values.depth ?? "high")
  const [breadth, setBreadth] = useState(state.values.breadth ?? "high")
  const value = (name: keyof NewRun) => state.values[name]
  const errorFor = (name: keyof NewRun) => state.fieldErrors[name]?.[0]
  const describedBy = (name: keyof NewRun, hint?: string) => [hint, errorFor(name) && `${name}-error`].filter(Boolean).join(" ") || undefined

  return (
    <form action={formAction} className="space-y-6">
      {state.error && (
        <Alert variant="destructive" role="alert">
          <AlertTitle>Couldn&apos;t start the run</AlertTitle>
          <AlertDescription>
            <p>
              <InlineCode text={state.error} />
            </p>
          </AlertDescription>
        </Alert>
      )}

      <Field name="topic" label="Research topic" error={errorFor("topic")}>
        <Textarea
          id="topic"
          name="topic"
          rows={3}
          required
          maxLength={500}
          defaultValue={value("topic")}
          placeholder="AI agents for enterprise finance operations"
          aria-invalid={!!errorFor("topic")}
          aria-describedby={describedBy("topic")}
        />
      </Field>
      <Field name="context" label="Context" hint="Why this matters, who it's for, what decision it supports." error={errorFor("context")}>
        <Textarea id="context" name="context" rows={2} maxLength={2000} defaultValue={value("context")} aria-describedby={describedBy("context", "context-hint")} />
      </Field>

      <div className="grid gap-6 sm:grid-cols-2">
        <Choice
          name="depth"
          label="Depth"
          hint={`${config.depth_options[depth] ?? "?"} results per search query`}
          value={depth}
          onChange={setDepth}
          options={DEPTHS.map((key) => [key, DEPTH_LABELS[key]])}
          error={errorFor("depth")}
        />
        <Choice
          name="breadth"
          label="Breadth"
          hint="How many angles the search plan covers"
          value={breadth}
          onChange={setBreadth}
          options={BREADTHS.map((key) => [key, BREADTH_LABELS[key]])}
          error={errorFor("breadth")}
        />
      </div>

      <div className="grid gap-6 sm:grid-cols-[12rem_minmax(0,1fr)]">
        <Field name="final_source_count" label="Final sources" hint={`1–${config.max_final_source_count}`} error={errorFor("final_source_count")}>
          <Input
            id="final_source_count"
            name="final_source_count"
            type="number"
            inputMode="numeric"
            required
            min={1}
            max={config.max_final_source_count}
            defaultValue={value("final_source_count") ?? config.default_final_source_count}
            aria-invalid={!!errorFor("final_source_count")}
            aria-describedby={describedBy("final_source_count", "final_source_count-hint")}
          />
        </Field>
        <div className="space-y-2">
          <Label htmlFor="storage_root">Saved to</Label>
          <Input
            id="storage_root"
            readOnly
            value={config.default_storage_root}
            title={config.default_storage_root}
            className="font-mono text-xs"
            aria-describedby="storage_root-hint"
          />
          <p id="storage_root-hint" className="text-xs text-muted-foreground">
            Set <code className="font-mono">SUPERRESEARCHER_STORAGE_ROOT</code> before starting the app to change this.
          </p>
        </div>
      </div>

      <details className="group rounded-lg border p-4 [&_summary::-webkit-details-marker]:hidden">
        <summary className="cursor-pointer text-sm font-medium">
          Scope controls <span className="font-normal text-muted-foreground">(optional)</span>
        </summary>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          {SCOPE_FIELDS.map(([name, label, placeholder, maxLength]) => (
            <Field key={name} name={name} label={label} error={errorFor(name)}>
              <Input id={name} name={name} maxLength={maxLength} defaultValue={value(name)} placeholder={placeholder} aria-describedby={describedBy(name)} />
            </Field>
          ))}
          {LIST_FIELDS.map(([name, label]) => (
            <Field key={name} name={name} label={label} hint="One per line or comma-separated" error={errorFor(name)}>
              <Textarea id={name} name={name} rows={2} maxLength={2000} defaultValue={value(name)} aria-describedby={describedBy(name, `${name}-hint`)} />
            </Field>
          ))}
        </div>
      </details>

      <Button type="submit" disabled={pending}>
        {pending && <LoaderCircle className="animate-spin" />}
        {pending ? "Starting…" : "Start research run"}
      </Button>
    </form>
  )
}

function Field({ name, label, hint, error, children }: { name: string; label: string; hint?: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-2">
      <Label htmlFor={name}>{label}</Label>
      {children}
      {hint && (
        <p id={`${name}-hint`} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${name}-error`} className="text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

function Choice({
  name,
  label,
  hint,
  value,
  onChange,
  options,
  error,
}: {
  name: string
  label: string
  hint: string
  value: string
  onChange: (value: string) => void
  options: [string, string][]
  error?: string
}) {
  return (
    <div className="space-y-2">
      <Label id={`${name}-label`}>{label}</Label>
      <input type="hidden" name={name} value={value} />
      <ToggleGroup
        type="single"
        variant="outline"
        aria-labelledby={`${name}-label`}
        aria-describedby={`${name}-hint`}
        className="w-full flex-wrap"
        value={value}
        onValueChange={(next) => next && onChange(next)}
      >
        {options.map(([key, text]) => (
          <ToggleGroupItem key={key} value={key} className="flex-1 text-xs whitespace-nowrap data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">
            {text}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      <p id={`${name}-hint`} className="text-xs text-muted-foreground">
        {hint}
      </p>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  )
}
