import { CircleAlert } from "lucide-react"
import { InlineCode } from "@/components/inline-code"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

export function BackendProblem({ message, title = "Something's not right" }: { message: string; title?: string }) {
  return (
    <Alert variant="destructive">
      <CircleAlert />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>
          <InlineCode text={message} />
        </p>
      </AlertDescription>
    </Alert>
  )
}
