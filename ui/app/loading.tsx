import { Skeleton } from "@/components/ui/skeleton"

export default function Loading() {
  return (
    <div className="grid gap-6 md:grid-cols-[17rem_minmax(0,1fr)] lg:grid-cols-[20rem_minmax(0,1fr)]" aria-busy="true" aria-label="Loading runs">
      <div className="space-y-3">
        <Skeleton className="h-8 w-32" />
        <Skeleton className="h-9" />
        <Skeleton className="h-8" />
        {[0, 1, 2].map((key) => (
          <Skeleton key={key} className="h-20" />
        ))}
      </div>
      <Skeleton className="hidden h-96 md:block" />
    </div>
  )
}
