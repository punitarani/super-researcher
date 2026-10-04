"use client"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return (
    <Alert variant="destructive">
      <AlertTitle>Something went wrong</AlertTitle>
      <AlertDescription>
        <p>The page couldn&apos;t load. Check the terminal running <code className="font-mono">npm run ui</code> for details.</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={reset}>
          Try again
        </Button>
      </AlertDescription>
    </Alert>
  )
}
