import { api } from "@/lib/backend"
import { streamTarget } from "@/lib/schemas"
import { snapshotStream } from "@/lib/stream"

export async function GET(request: Request, { params }: RouteContext<"/api/stream/[kind]/[id]">) {
  const target = streamTarget.safeParse(await params)
  if (!target.success) return Response.json({ error: "Unknown stream." }, { status: 404 })
  const { kind, id } = target.data
  const load: () => Promise<{ state: string }> = kind === "run" ? () => api.run(id) : () => api.job(kind, id)
  return new Response(snapshotStream(load, request.signal), {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  })
}
