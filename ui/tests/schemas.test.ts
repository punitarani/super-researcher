import { describe, expect, it } from "vitest"
import { z } from "zod"
import { configSchema, newRunSchema, runListSchema, runSchema, streamTarget } from "@/lib/schemas"

const validRun = { topic: "  Grid storage  ", depth: "high", breadth: "medium", final_source_count: "12" }

describe("newRunSchema", () => {
  it("trims text and coerces numbers from form data", () => {
    const run = newRunSchema.parse(validRun)
    expect(run.topic).toBe("Grid storage")
    expect(run.final_source_count).toBe(12)
    expect(run.context).toBe("")
  })

  it("explains what's wrong", () => {
    const result = newRunSchema.safeParse({ ...validRun, topic: "   ", depth: "deep", final_source_count: "501" })
    expect(result.success).toBe(false)
    const errors = z.flattenError(result.error!).fieldErrors
    expect(errors.topic).toEqual(["Enter a research topic."])
    expect(errors.depth).toEqual(["Choose a depth."])
    expect(errors.final_source_count).toEqual(["The maximum is 500 sources."])
  })
})

describe("backend responses", () => {
  it("keeps only the names of configured API keys", () => {
    const config = configSchema.parse({
      depth_options: { low: 3 },
      default_storage_root: "/runs",
      default_final_source_count: 120,
      max_final_source_count: 500,
      configured_api_keys: { EXA_API_KEY: "<configured>" },
    })
    expect(config.configuredKeys).toEqual(["EXA_API_KEY"])
    expect(JSON.stringify(config)).not.toContain("<configured>")
  })

  it("accepts saved runs and drops fields it doesn't know", () => {
    const run = runSchema.parse({
      run_id: "20260101-000000-x_Corpus",
      topic: "x",
      state: "interrupted",
      progress: 40,
      dossier_path: "/runs/x",
      counts: {},
      events: [{ time: "2026-01-01T00:00:00", message: "Run started." }],
      files: {},
      settings: { api_key: "secret" },
    })
    expect(run.stop_requested).toBe(false)
    expect(run).not.toHaveProperty("settings")
  })
})

describe("runListSchema", () => {
  it("skips a saved run it can't read instead of failing the whole history", () => {
    const good = { run_id: "a_Corpus", topic: "a", state: "completed", progress: 100, dossier_path: "/runs/a", counts: {} }
    const runs = runListSchema.parse([good, { run_id: "b_Corpus", state: "exploded" }, "not a run"])
    expect(runs.map((run) => run.run_id)).toEqual(["a_Corpus"])
  })
})

describe("streamTarget", () => {
  it("only follows known kinds with plain ids", () => {
    expect(streamTarget.safeParse({ kind: "atlas", id: "a_Corpus-1700000000" }).success).toBe(true)
    expect(streamTarget.safeParse({ kind: "run", id: "../etc/passwd" }).success).toBe(false)
    expect(streamTarget.safeParse({ kind: "shell", id: "x" }).success).toBe(false)
    for (const id of [".", ".."]) expect(streamTarget.safeParse({ kind: "run", id }).success).toBe(false)
  })
})
