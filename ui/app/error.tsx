"use client"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"

// retry() re-fetches the page's data and re-renders; reset() would only re-render what already failed.
export default function ErrorPage({ retry }: { error: Error; retry: () => void }) {
  return (
    <Alert variant="destructive">
      <AlertTitle>Something went wrong</AlertTitle>
      <AlertDescription>
        <p>The page couldn&apos;t load. Check the terminal running <code className="font-mono">npm run ui</code> for details.</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={() => retry()}>
          Try again
        </Button>
      </AlertDescription>
    </Alert>
  )
}
