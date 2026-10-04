import { describe, expect, it } from "vitest"
import { snapshotStream } from "@/lib/stream"

async function read(stream: ReadableStream<Uint8Array>) {
  return new Response(stream).text()
}

describe("snapshotStream", () => {
  it("sends each change once and ends when the run finishes", async () => {
    const states = ["running", "running", "completed"]
    const stream = snapshotStream(async () => ({ state: states.shift()! }), new AbortController().signal, 0)
    expect(await read(stream)).toBe(
      'event: snapshot\ndata: {"state":"running"}\n\n' + 'event: snapshot\ndata: {"state":"completed"}\n\n' + "event: end\ndata: {}\n\n",
    )
  })

  it("reports a failed load and closes", async () => {
    const stream = snapshotStream(() => Promise.reject(new Error("Run not found")), new AbortController().signal, 0)
    expect(await read(stream)).toBe('event: fail\ndata: {"message":"Run not found"}\n\n')
  })

  it("stops polling when the browser disconnects", async () => {
    const controller = new AbortController()
    let loads = 0
    const stream = snapshotStream(async () => (++loads === 2 && controller.abort(), { state: "running" }), controller.signal, 0)
    await read(stream)
    expect(loads).toBe(2)
  })
})
