import { isActive } from "./schemas"

const encoder = new TextEncoder()
const PING_EVERY = 15 // ticks without changes before a keep-alive comment

/**
 * Server-sent events for a run or job: polls `load`, sends a `snapshot` event
 * whenever it changes, and an `end` event once it finishes. A failed load sends
 * `fail` with a message and closes the stream.
 */
export function snapshotStream<T extends { state: string }>(load: () => Promise<T>, signal: AbortSignal, intervalMs = 1000) {
  let closed = false
  return new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (text: string) => {
        if (!closed) controller.enqueue(encoder.encode(text))
      }
      const event = (name: string, data: unknown) => send(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`)
      let last = ""
      let quiet = 0
      while (!closed && !signal.aborted) {
        try {
          const snapshot = await load()
          const json = JSON.stringify(snapshot)
          if (json !== last) {
            send(`event: snapshot\ndata: ${json}\n\n`)
            last = json
            quiet = 0
          } else if (++quiet % PING_EVERY === 0) {
            send(": ping\n\n")
          }
          if (!isActive(snapshot.state)) {
            event("end", {})
            break
          }
        } catch (error) {
          event("fail", { message: error instanceof Error ? error.message : "Lost track of this job." })
          break
        }
        await sleep(intervalMs, signal)
      }
      if (!closed) controller.close()
    },
    cancel() {
      closed = true
    },
  })
}

function sleep(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    // Remove the abort listener on every wake-up, or a long stream piles up one per tick.
    const wake = () => {
      clearTimeout(timer)
      signal.removeEventListener("abort", wake)
      resolve()
    }
    const timer = setTimeout(wake, ms)
    signal.addEventListener("abort", wake, { once: true })
  })
}
