import { afterEach, describe, expect, it, vi } from "vitest"
import { api, backendUrl, BackendError } from "@/lib/backend"

const reply = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }))

afterEach(() => vi.unstubAllGlobals())

describe("backendUrl", () => {
  it("only accepts this computer", () => {
    expect(backendUrl("http://localhost:9000/x")).toBe("http://localhost:9000")
    expect(backendUrl("http://[::1]:8765")).toBe("http://[::1]:8765")
    expect(() => backendUrl("http://192.168.1.5:8765")).toThrow(/must point at this computer/)
  })
})

describe("api", () => {
  it("says how to start the backend when it isn't running", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("fetch failed"))))
    await expect(api.runs()).rejects.toThrow(/npm run ui/)
  })

  it("passes the backend's own error message through", async () => {
    const message = "SUPERRESEARCHER_AGENT=codex is set, so the app always uses Codex."
    vi.stubGlobal("fetch", vi.fn(() => reply({ error: message }, 409)))
    const error = await api.selectAgent("gemini").catch((caught) => caught)
    expect(error).toBeInstanceOf(BackendError)
    expect(error).toMatchObject({ message, status: 409 })
  })

  it("never puts a dot-segment id into a backend URL", async () => {
    const fetch = vi.fn(() => reply({}))
    vi.stubGlobal("fetch", fetch)
    await expect(api.run("..")).rejects.toMatchObject({ status: 404 })
    await expect(api.job("atlas", ".")).rejects.toMatchObject({ status: 404 })
    expect(fetch).not.toHaveBeenCalled()
  })

  it("rejects responses with an unexpected shape", async () => {
    vi.stubGlobal("fetch", vi.fn(() => reply({ selected: "claude" })))
    await expect(api.agents()).rejects.toMatchObject({ status: 502 })
  })

  it("sends job options under the backend's own names, as JSON", async () => {
    const fetch = vi.fn(() => reply({ job_id: "j", corpus_id: "c_Corpus", state: "queued", progress: 0, counts: {} }))
    vi.stubGlobal("fetch", fetch)
    await api.startJob("compile", "c_Corpus", true)
    await api.startJob("postprocess", "c_Corpus", true)
    const calls = fetch.mock.calls as unknown as [string, RequestInit][]
    expect(calls[0][0]).toMatch(/\/api\/publish\/compile$/)
    expect(JSON.parse(calls[0][1].body as string)).toEqual({ corpus_id: "c_Corpus", force_plan: true })
    expect(JSON.parse(calls[1][1].body as string)).toEqual({ corpus_id: "c_Corpus" })
    expect(calls[0][1].headers).toEqual({ "Content-Type": "application/json" })
  })

  it("reads pipeline progress, treating unavailable parts as not done", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) =>
        url.endsWith("/api/corpora")
          ? reply({ corpora: [{ id: "c_Corpus", atlas_ready: true, chunk_count: 42, markdown_count: 3 }] })
          : url.endsWith("/topics")
            ? reply({ raw: { exists: true }, curated: { exists: false } })
            : reply({ error: "Corpus folder not found." }, 400),
      ),
    )
    expect(await api.pipeline("c_Corpus")).toEqual({
      markdownCount: 3,
      atlasReady: true,
      chunkCount: 42,
      atlasDeps: null,
      topics: true,
      curated: false,
      composed: false,
      finalized: false,
      paperPath: null,
    })
  })
})
