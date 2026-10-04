"use client"

import { useRouter } from "next/navigation"
import { useEffect, useState } from "react"
import type { z } from "zod"

/**
 * Follow a run or job over server-sent events from /api/stream. Starts from the
 * server-rendered snapshot; refreshes the page's server data when it finishes.
 */
export function useLiveSnapshot<T>(url: string | null, schema: z.ZodType<T>, initial: T) {
  const router = useRouter()
  const [data, setData] = useState(initial)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!url) return
    const source = new EventSource(url)
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
  }, [url, schema, router])

  return { data, error }
}
