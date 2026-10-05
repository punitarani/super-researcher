/** Render backend messages, showing `backticked` commands as code. */
export function InlineCode({ text }: { text: string }) {
  return text.split(/(`[^`]+`)/).map((part, index) =>
    part.startsWith("`") && part.endsWith("`") && part.length > 2 ? (
      <code key={index} className="rounded bg-muted px-1 py-0.5 font-mono text-[0.9em] break-words">
        {part.slice(1, -1)}
      </code>
    ) : (
      part
    ),
  )
}
