import { describe, expect, it } from "vitest"
import { filterSources, parseSources } from "@/lib/sources"

const folder = "/runs/x_Corpus"
const line = (row: object) => JSON.stringify(row)
const rows = [
  { url: "https://arxiv.org/pdf/1", title: "METAL: Multilingual Meta-Evaluation", publisher: "arxiv.org", source_type: "academic", fetch_status: "downloaded", markdown_status: "created", markdown_path: `${folder}/markdown/0001-metal.md`, score: 100, conversion_notes: ["postprocess_pymupdf4llm_reconverted"] },
  { url: "https://example.gov/report", title: "Agency report", publisher: "example.gov", source_type: "government", fetch_status: "failed", fetch_error: "HTTP Error 403: Forbidden", score: 40, conversion_notes: [] },
  { url: "https://arxiv.org/pdf/3", title: "Rough PDF", publisher: "arxiv.org", source_type: "academic", fetch_status: "downloaded", markdown_status: "created", markdown_path: "/elsewhere/0003.md", conversion_notes: ["pdf_fallback_text_only"] },
]

describe("parseSources", () => {
  const parsed = parseSources(rows.map(line).join("\n") + "\n", false, folder)

  it("numbers sources and keeps only what the list shows", () => {
    expect(parsed.map((row) => row.index)).toEqual([1, 2, 3])
    expect(parsed[0]).toMatchObject({ title: "METAL: Multilingual Meta-Evaluation", textPath: "markdown/0001-metal.md", notes: ["Pymupdf4llm reconverted"], flags: [] })
    expect(parsed[0]).not.toHaveProperty("conversion_notes")
  })

  it("flags failed downloads and rough text, and only links text inside the run folder", () => {
    expect(parsed[1].flags).toEqual(["Not downloaded: HTTP Error 403: Forbidden"])
    expect(parsed[1].textPath).toBeNull()
    expect(parsed[2].flags).toEqual(["PDF text extracted roughly"])
    expect(parsed[2].textPath).toBeNull()
  })

  it("finds the text when the runs folder was moved or reached through a symlink", () => {
    const moved = { ...rows[0], markdown_path: "/private/old-place/x_Corpus/markdown/0001-metal.md" }
    expect(parseSources(line(moved), false, folder)[0].textPath).toBe("markdown/0001-metal.md")
  })

  it("skips a line cut off by truncation and lines that aren't sources", () => {
    const text = [line(rows[0]), "not json", line(rows[1]), '{"url": "https://cut'].join("\n")
    expect(parseSources(text, true, folder).map((row) => row.title)).toEqual(["METAL: Multilingual Meta-Evaluation", "Agency report"])
  })
})

describe("filterSources", () => {
  const parsed = parseSources(rows.map(line).join("\n"), false, folder)
  const titles = (filters: Parameters<typeof filterSources>[1]) => filterSources(parsed, filters).map((row) => row.index)

  it("searches title, URL and publisher, case-insensitively", () => {
    expect(titles({ q: "metal", type: "", status: "", flagged: false })).toEqual([1])
    expect(titles({ q: "EXAMPLE.GOV", type: "", status: "", flagged: false })).toEqual([2])
  })

  it("filters by type, status and flags together", () => {
    expect(titles({ q: "", type: "academic", status: "", flagged: false })).toEqual([1, 3])
    expect(titles({ q: "", type: "", status: "failed", flagged: false })).toEqual([2])
    expect(titles({ q: "", type: "academic", status: "", flagged: true })).toEqual([3])
  })
})
