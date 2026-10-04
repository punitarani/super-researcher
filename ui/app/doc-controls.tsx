"use client"

import { Eye, LoaderCircle, X } from "lucide-react"
import { useQueryStates } from "nuqs"
import { useTransition } from "react"
import { Button } from "@/components/ui/button"
import { runsParams } from "@/lib/search-params"

// The document viewer renders on the server, so these update the URL and re-render the page.
function useDocState() {
  const [pending, startTransition] = useTransition()
  const [{ more }, setDoc] = useQueryStates({ doc: runsParams.doc, more: runsParams.more }, { shallow: false, startTransition })
  return { pending, more, setDoc }
}

export function ViewDocButton({ path, label }: { path: string; label: string }) {
  const { pending, setDoc } = useDocState()
  return (
    <Button variant="outline" size="sm" aria-label={`View ${label}`} onClick={() => setDoc({ doc: path, more: null })}>
      {pending ? <LoaderCircle className="animate-spin" /> : <Eye />} View
    </Button>
  )
}

export function CloseDocButton() {
  const { setDoc } = useDocState()
  return (
    <Button variant="ghost" size="sm" aria-label="Close document" onClick={() => setDoc({ doc: null, more: null })}>
      <X /> Close
    </Button>
  )
}

export function ShowMoreButton() {
  const { pending, more, setDoc } = useDocState()
  return (
    <Button variant="outline" size="sm" disabled={pending} onClick={() => setDoc({ more: more + 1 })}>
      {pending && <LoaderCircle className="animate-spin" />} Show more
    </Button>
  )
}
