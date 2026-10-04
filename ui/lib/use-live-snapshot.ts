"use client"

import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import type { z } from "zod"
import { isActive } from "./schemas"

/**
 * Follow a run or job over server-sent events from `url` while it's active.
 * Starts from the server-rendered snapshot and adopts newer server data when
 * the page re-renders; refreshes the page's server data when it finishes.
 */
export function useLiveSnapshot<T extends { state: string } | null>(url: string | null, schema: z.ZodType<T>, initial: T) {
  const router = useRouter()
  const [data, setData] = useState(initial)
  const [rendered, setRendered] = useState(initial)
  // Bumped when server data is adopted. That data can be older than what the stream already
  // showed, and the stream only sends changes, so reconnecting makes it resend the current snapshot.
  const [generation, setGeneration] = useState(0)
  const [error, setError] = useState<string | null>(null)
  if (initial !== rendered) {
    setRendered(initial)
    setData(initial)
    setGeneration((value) => value + 1)
  }
  const liveUrl = url && data && isActive(data.state) ? url : null

  useEffect(() => {
    if (!liveUrl) return
    const source = new EventSource(liveUrl)
    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      source.close()
      router.refresh()
    }
    source.addEventListener("snapshot", (event) => {
      const parsed = schema.safeParse(JSON.parse(event.data))
      if (!parsed.success) return
      setData(parsed.data)
      // A finished snapshot stops this effect on the next render, which can come before
      // the `end` event, so finish here rather than relying on `end` to refresh the page.
      if (parsed.data && !isActive(parsed.data.state)) finish()
    })
    source.addEventListener("end", finish)
    source.addEventListener("fail", (event) => {
      source.close()
      setError(JSON.parse(event.data).message)
    })
    // The browser reconnects on its own after a dropped connection; this is the case it gives up.
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) setError("Lost the connection to the app. Refresh the page to reconnect.")
    }
    return () => source.close()
  }, [liveUrl, generation, schema, router])

  return { data, error }
}
