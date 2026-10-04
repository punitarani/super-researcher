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
  const [error, setError] = useState<string | null>(null)
  if (initial !== rendered) {
    setRendered(initial)
    setData(initial)
  }
  const liveUrl = url && data && isActive(data.state) ? url : null

  useEffect(() => {
    if (!liveUrl) return
    const source = new EventSource(liveUrl)
    source.addEventListener("snapshot", (event) => {
      const parsed = schema.safeParse(JSON.parse(event.data))
      if (parsed.success) setData(parsed.data)
    })
    source.addEventListener("end", () => {
      source.close()
      router.refresh()
    })
    source.addEventListener("fail", (event) => {
      source.close()
      setError(JSON.parse(event.data).message)
    })
    // The browser reconnects on its own after a dropped connection; this is the case it gives up.
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) setError("Lost the connection to the app. Refresh the page to reconnect.")
    }
    return () => source.close()
  }, [liveUrl, schema, router])

  return { data, error }
}
