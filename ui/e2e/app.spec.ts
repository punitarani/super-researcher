import { expect, test, type APIRequestContext } from "@playwright/test"
import { mkdirSync, writeFileSync } from "node:fs"
import path from "node:path"
import { API_URL } from "../playwright.config"

async function createRun(request: APIRequestContext, topic: string, size: "low" | "high" = "low") {
  const response = await request.post(`${API_URL}/api/runs`, {
    data: { topic, depth: size, breadth: size, final_source_count: 3 },
  })
  expect(response.ok()).toBeTruthy()
  return (await response.json()).run_id as string
}

test("start a run, watch it live, and stop it", async ({ page }) => {
  await page.goto("/new")
  await expect(page.getByText("Gemini isn't ready, so planning will use built-in defaults")).toBeVisible()
  await expect(page.getByText("No search provider key")).toBeVisible()

  await page.getByLabel("Research topic").fill("Grid-scale battery storage economics")
  await page.getByRole("button", { name: "Start research run" }).click()

  await expect(page).toHaveURL(/\?run=.+_Corpus/)
  await expect(page.getByText(/Discovery running: \d+\/\d+ searches checked/).first()).toBeVisible()
  await page.getByRole("button", { name: "Stop" }).click()
  await expect(page.getByRole("button", { name: "Stopping after current step…" })).toBeVisible()
  await expect(page.getByRole("article").getByText("Stopped", { exact: true })).toBeVisible()
  await expect(page.getByText("Run stopped.")).toBeVisible()

  // History comes from disk, so it survives a reload.
  await page.reload()
  await expect(page.getByRole("article").getByText("Stopped", { exact: true })).toBeVisible()
})

test("new-run input is validated on the server", async ({ page }) => {
  await page.goto("/new")
  await page.getByLabel("Research topic").fill("   ")
  await page.getByLabel("Context").fill("Keep me")
  await page.getByRole("button", { name: "Start research run" }).click()
  await expect(page.getByText("Enter a research topic.")).toBeVisible()
  await expect(page.getByLabel("Context")).toHaveValue("Keep me")
  await expect(page).toHaveURL(/\/new$/)
})

test("filters and the selected run live in the URL", async ({ page, request }) => {
  const runId = await createRun(request, "Heat pumps in cold climates")
  await page.goto(`/?q=heat+pumps&run=${runId}`)
  const list = page.getByRole("complementary", { name: "Runs" })
  await expect(list.getByRole("link", { name: /Heat pumps in cold climates/ })).toHaveAttribute("aria-current", "page")
  await expect(list.getByRole("listitem")).toHaveCount(1)

  await list.getByRole("radio", { name: "Failed" }).click()
  await expect(page).toHaveURL(/state=failed/)
  await expect(page.getByText("No runs match these filters.")).toBeVisible()
  await page.reload()
  await expect(page.getByText("No runs match these filters.")).toBeVisible()
})

test("on a phone, a run's back link returns to the list", async ({ page, request }) => {
  const runId = await createRun(request, "Tidal energy in estuaries")
  await page.setViewportSize({ width: 375, height: 812 })
  await page.goto(`/?run=${runId}`)
  await page.getByRole("link", { name: "All runs" }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("complementary", { name: "Runs" })).toBeVisible()
})

test("the brief and the run's documents can be read, and nothing outside the run", async ({ page, request }) => {
  const response = await request.post(`${API_URL}/api/runs`, {
    data: { topic: "Heat networks", context: "For a city energy memo", must_include: "district heating", depth: "low", breadth: "low", final_source_count: 3 },
  })
  const runId = (await response.json()).run_id as string
  await expect.poll(async () => (await (await request.get(`${API_URL}/api/runs/${runId}`)).json()).state).toBe("completed")

  await page.goto(`/?run=${runId}&tab=artifacts`)
  await page.locator("summary", { hasText: /^Brief/ }).click()
  await expect(page.getByText("For a city energy memo")).toBeVisible()
  await expect(page.getByText("district heating")).toBeVisible()
  await expect(page.getByRole("definition").filter({ hasText: "Gemini · API key" })).toBeVisible()

  await page.getByRole("button", { name: "View run summary" }).click()
  await expect(page).toHaveURL(/doc=run-summary\.md/)
  await expect(page.getByRole("heading", { name: "Research Run Summary" })).toBeVisible()

  await page.getByRole("button", { name: "View research protocol" }).click()
  await expect(page.getByText("research_topic:")).toBeVisible()

  await page.goto(`/?run=${runId}&tab=artifacts&doc=../app-settings.json`)
  await expect(page.getByText("That file is outside this run's folder.")).toBeVisible()
})

test("pipeline jobs stream progress and show the backend's curation message", async ({ page, request }) => {
  const runId = await createRun(request, "Solid-state batteries")
  await expect.poll(async () => (await (await request.get(`${API_URL}/api/runs/${runId}`)).json()).state).toBe("completed")

  await page.goto(`/?run=${runId}&tab=pipeline`)
  const stage = (name: string) => page.getByRole("listitem").filter({ has: page.getByRole("heading", { name }) })

  await stage("Post-process").getByRole("button", { name: "Run" }).click()
  await expect(stage("Post-process").getByText("Completed")).toBeVisible()
  await expect(page).toHaveURL(/jobs=postprocess/)

  await stage("Compose terms").getByRole("button", { name: "Run" }).click()
  await expect(stage("Compose terms").getByRole("alert")).toContainText("Save curated topics first")
  await expect(stage("Compose terms").getByRole("link", { name: "Open the classic UI" })).toHaveAttribute("href", /127\.0\.0\.1:8799/)
})

async function finishedRun(request: APIRequestContext, topic: string) {
  const runId = await createRun(request, topic)
  await expect.poll(async () => (await (await request.get(`${API_URL}/api/runs/${runId}`)).json()).state).toBe("completed")
  return runId
}

test("sources can be searched and filtered, the filters survive a reload, and a source's text opens", async ({ page, request }) => {
  const runId = await finishedRun(request, "Tidal turbines")
  // The keyless e2e backend finds no sources, so give the run two, as ingest would write them.
  const folder = path.join(process.env.E2E_STORAGE!, runId)
  mkdirSync(path.join(folder, "markdown"), { recursive: true })
  writeFileSync(path.join(folder, "markdown", "0001-blade-wear.md"), "# Blade wear\n\n## Findings\n\nErosion dominates.\n")
  const sources = [
    { url: "https://example.org/blade-wear", title: "Blade wear in tidal arrays", publisher: "example.org", source_type: "academic", fetch_status: "downloaded", markdown_status: "created", markdown_path: path.join(folder, "markdown", "0001-blade-wear.md"), conversion_notes: [] },
    { url: "https://example.gov/permits", title: "Permitting guidance", publisher: "example.gov", source_type: "government", fetch_status: "failed", fetch_error: "HTTP Error 403: Forbidden", conversion_notes: [] },
  ]
  writeFileSync(path.join(folder, "ingested_sources.jsonl"), sources.map((row) => JSON.stringify(row)).join("\n") + "\n")

  await page.goto(`/?run=${runId}&tab=sources`)
  await expect(page.getByText("Showing 2 of 2 sources")).toBeVisible()
  await expect(page.getByText("Not downloaded: HTTP Error 403: Forbidden")).toBeVisible()
  await page.getByLabel("Flagged only").click()
  await expect(page.getByText("Showing 1 of 2 sources")).toBeVisible()
  await expect(page).toHaveURL(/flagged=true/)
  await page.reload()
  await expect(page.getByText("Showing 1 of 2 sources")).toBeVisible()

  await page.getByLabel("Flagged only").click()
  await page.getByRole("button", { name: "Read the text of source 1" }).click()
  await expect(page.getByRole("heading", { name: "Findings" })).toBeVisible()
})

test("the agent log shows each call, why it used defaults, and its prompt", async ({ page, request }) => {
  const runId = await finishedRun(request, "Grid frequency response")
  await page.goto(`/?run=${runId}&tab=log`)
  await expect(page.getByText(/agent calls: 0 answered, \d+ used built-in defaults/)).toBeVisible()
  const first = page.getByRole("listitem").filter({ hasText: "1. Protocol: classify the topic" })
  await expect(first.getByText("Not sent")).toBeVisible()
  await expect(first.getByText(/Why: Add GEMINI_API_KEY to \.env/)).toBeVisible()
  await first.getByRole("button", { name: "View call 1" }).click()
  await expect(page.getByText("You are the Archetype Classifier")).toBeVisible()
})

test("agents page explains how to set each agent up", async ({ page, request }) => {
  await page.goto("/agents")
  await expect(page.getByRole("heading", { name: "Gemini" })).toBeVisible()
  await expect(page.getByText("Add GEMINI_API_KEY to .env")).toBeVisible()
  await expect(page.getByText("No search key yet")).toBeVisible()
  await expect(page.getByRole("link", { name: /Agent: Gemini, No API key/ })).toBeVisible()

  const log = page.getByLabel("Keep a log of each agent prompt and reply")
  await expect(log).toBeChecked()
  try {
    await log.click()
    await expect(log).not.toBeChecked()
    await page.reload()
    await expect(log).not.toBeChecked()
    await log.click()
    await expect(log).toBeChecked()
  } finally {
    // The setting is shared by the whole e2e backend, so never leave logging off for other tests.
    await request.put(`${API_URL}/api/settings`, { data: { agent_log: true } })
  }
})
