import { expect, test, type APIRequestContext } from "@playwright/test"
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

test("agents page explains how to set each agent up", async ({ page }) => {
  await page.goto("/agents")
  await expect(page.getByRole("heading", { name: "Gemini" })).toBeVisible()
  await expect(page.getByText("Add GEMINI_API_KEY to api_keys.txt")).toBeVisible()
  await expect(page.getByText("No search key yet")).toBeVisible()
  await expect(page.getByRole("link", { name: /Agent: Gemini, No API key/ })).toBeVisible()
})
