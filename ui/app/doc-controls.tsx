"use client"

import { Eye, LoaderCircle, RefreshCw, X } from "lucide-react"
import { useRouter } from "next/navigation"
import { useQueryStates } from "nuqs"
import { useTransition } from "react"
import { Button } from "@/components/ui/button"
import { runsParams } from "@/lib/search-params"

// Viewers render on the server, so these update the URL and re-render the page.
// Each tab has its own open item: the Artifacts document, the Sources text, or the Agent log call.
const VIEWERS = { doc: runsParams.doc, src: runsParams.src, call: runsParams.call } as const
type Viewer = keyof typeof VIEWERS

function useViewer() {
  const [pending, startTransition] = useTransition()
  const [{ more }, set] = useQueryStates({ ...VIEWERS, more: runsParams.more }, { shallow: false, startTransition })
  return { pending, more, set }
}

/** The accessible name is "View <label>", or `label` itself when the button has its own `text`. */
export function OpenButton({ viewer, value, label, text = "View" }: { viewer: Viewer; value: string | number; label: string; text?: string }) {
  const { pending, set } = useViewer()
  return (
    <Button variant="outline" size="sm" aria-label={text === "View" ? `View ${label}` : label} onClick={() => set({ [viewer]: value, more: null })}>
      {pending ? <LoaderCircle className="animate-spin" /> : <Eye />} {text}
    </Button>
  )
}

export function CloseButton({ viewer, label = "Close document" }: { viewer: Viewer; label?: string }) {
  const { set } = useViewer()
  return (
    <Button variant="ghost" size="sm" aria-label={label} onClick={() => set({ [viewer]: null, more: null })}>
      <X /> Close
    </Button>
  )
}

export function ShowMoreButton() {
  const { pending, more, set } = useViewer()
  return (
    <Button variant="outline" size="sm" disabled={pending} onClick={() => set({ more: more + 1 })}>
      {pending && <LoaderCircle className="animate-spin" />} Show more
    </Button>
  )
}

/** These tabs are read once per page render; while a run is going, this re-reads them. */
export function RefreshButton() {
  const router = useRouter()
  const [pending, startTransition] = useTransition()
  return (
    <Button variant="outline" size="sm" disabled={pending} onClick={() => startTransition(() => router.refresh())}>
      {pending ? <LoaderCircle className="animate-spin" /> : <RefreshCw />} Refresh
    </Button>
  )
}

export const ViewDocButton = ({ path, label }: { path: string; label: string }) => <OpenButton viewer="doc" value={path} label={label} />
export const CloseDocButton = () => <CloseButton viewer="doc" />
