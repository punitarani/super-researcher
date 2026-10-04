// A collapsible JSON view built from <details>, so it needs no client JavaScript.

export function JsonTree({ value }: { value: unknown }) {
  return (
    <div className="font-mono text-xs leading-relaxed">
      <JsonNode value={value} depth={0} />
    </div>
  )
}

function JsonNode({ name, value, depth }: { name?: string; value: unknown; depth: number }) {
  const label = name === undefined ? null : <span className="text-muted-foreground">{name}: </span>
  if (value === null || typeof value !== "object") {
    return (
      <div className="break-words">
        {label}
        <Scalar value={value} />
      </div>
    )
  }
  const entries: [string, unknown][] = Array.isArray(value) ? value.map((item, index) => [String(index), item]) : Object.entries(value)
  const summary = Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`
  return (
    <details open={depth < 2}>
      <summary className="cursor-pointer select-none">
        {label}
        <span className="text-muted-foreground">{summary}</span>
      </summary>
      <div className="border-l pl-4">
        {entries.map(([key, item]) => (
          <JsonNode key={key} name={key} value={item} depth={depth + 1} />
        ))}
      </div>
    </details>
  )
}

function Scalar({ value }: { value: unknown }) {
  if (typeof value === "string") return <span className="whitespace-pre-wrap">{JSON.stringify(value)}</span>
  return <span className="text-sky-700 dark:text-sky-300">{String(value)}</span>
}
