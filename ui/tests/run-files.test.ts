import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { FIRST_CHUNK, MAX_BYTES, readRunFile, RunFileError, runFolder } from "@/lib/run-files"

let root: string
let folder: string
let outside: string

beforeEach(() => {
  const base = mkdtempSync(path.join(tmpdir(), "run-files-"))
  root = path.join(base, "research_runs")
  folder = path.join(root, "20261004-000000-demo_Corpus")
  outside = path.join(base, "elsewhere")
  mkdirSync(path.join(folder, "atlas", "publish"), { recursive: true })
  mkdirSync(outside)
  writeFileSync(path.join(folder, "run-summary.md"), "# Summary\n")
  writeFileSync(path.join(folder, "atlas", "publish", "report.md"), "# Report\n")
  writeFileSync(path.join(folder, "settings.json"), "{}")
  writeFileSync(path.join(folder, "original.pdf"), "%PDF-1.7")
  writeFileSync(path.join(outside, "secret.md"), "API_KEY=do-not-show")
  writeFileSync(path.join(root, "app-settings.json"), "{}")
})

afterEach(() => rmSync(path.dirname(root), { recursive: true, force: true }))

describe("runFolder", () => {
  it("accepts a run's own folder directly inside the storage root", async () => {
    await expect(runFolder(root, folder, "20261004-000000-demo_Corpus")).resolves.toMatch(/demo_Corpus$/)
  })

  it("refuses a folder that isn't the run's, or isn't inside the storage root", async () => {
    await expect(runFolder(root, folder, "other_Corpus")).rejects.toBeInstanceOf(RunFileError)
    await expect(runFolder(root, outside, "elsewhere")).rejects.toBeInstanceOf(RunFileError)
    await expect(runFolder(root, path.join(root, "missing_Corpus"), "missing_Corpus")).rejects.toBeInstanceOf(RunFileError)
    const escape = path.join(root, "linked_Corpus")
    symlinkSync(outside, escape)
    await expect(runFolder(root, escape, "linked_Corpus")).rejects.toBeInstanceOf(RunFileError)
  })
})

describe("readRunFile", () => {
  it("reads a text file inside the run folder", async () => {
    const file = await readRunFile(folder, "atlas/publish/report.md")
    expect(file).toMatchObject({ relativePath: "atlas/publish/report.md", text: "# Report\n", truncated: false })
  })

  it.each([
    ["a parent directory", "../app-settings.json"],
    ["a sneaky parent directory", "atlas/../../app-settings.json"],
    ["an absolute path", "/etc/secret.md"],
    ["an empty path", ""],
    ["a NUL byte", "run-summary.md\0.txt"],
    ["a file that isn't text", "original.pdf"],
    ["a folder", "atlas"],
    ["a missing file", "nope.md"],
  ])("refuses %s", async (_, relativePath) => {
    await expect(readRunFile(folder, relativePath)).rejects.toBeInstanceOf(RunFileError)
  })

  it("refuses a symlink that leads outside the run folder", async () => {
    symlinkSync(path.join(outside, "secret.md"), path.join(folder, "notes.md"))
    symlinkSync(outside, path.join(folder, "linked"))
    await expect(readRunFile(folder, "notes.md")).rejects.toBeInstanceOf(RunFileError)
    await expect(readRunFile(folder, "linked/secret.md")).rejects.toBeInstanceOf(RunFileError)
  })

  it("follows a symlink that stays inside the run folder", async () => {
    symlinkSync(path.join(folder, "run-summary.md"), path.join(folder, "latest.md"))
    await expect(readRunFile(folder, "latest.md")).resolves.toMatchObject({ text: "# Summary\n" })
  })

  it("reads large files in steps, never splitting a character", async () => {
    // "é" is two bytes, so the first chunk ends in the middle of one.
    writeFileSync(path.join(folder, "big.md"), "a" + "é".repeat(FIRST_CHUNK))
    const first = await readRunFile(folder, "big.md")
    expect(first.truncated).toBe(true)
    expect(Buffer.byteLength(first.text)).toBeLessThanOrEqual(FIRST_CHUNK)
    expect(first.text.endsWith("é")).toBe(true)

    const more = await readRunFile(folder, "big.md", FIRST_CHUNK * 4)
    expect(more.truncated).toBe(false)
    expect(more.text.length).toBe(FIRST_CHUNK + 1)
  })

  it("never reads past the overall cap", async () => {
    writeFileSync(path.join(folder, "huge.txt"), Buffer.alloc(MAX_BYTES + 10, "x"))
    const file = await readRunFile(folder, "huge.txt", MAX_BYTES * 8)
    expect(file.text.length).toBe(MAX_BYTES)
    expect(file).toMatchObject({ truncated: true, limit: MAX_BYTES })
  })
})
