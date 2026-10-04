import type { JobKind } from "./schemas"

export type Stage = {
  kind: JobKind
  title: string
  description: string
  /** Label for the backend's "start over" option, if the stage has one. */
  force?: string
  /** Stage prompts go to the selected agent. */
  needsAgent?: boolean
  /** Tab in the classic UI where its results are curated or used. */
  classicTab?: string
}

export const STAGES: Stage[] = [
  { kind: "postprocess", title: "Post-process", description: "Repair file types, regenerate Markdown sidecars, and re-fetch broken downloads." },
  { kind: "atlas", title: "Build Atlas", description: "Split the sources into chunks and embed them locally.", force: "Rebuild from scratch" },
  {
    kind: "topics",
    title: "Discover topics",
    description: "Mine headings into a topic outline and refine it with the agent.",
    force: "Discover again",
    needsAgent: true,
    classicTab: "Curate",
  },
  { kind: "compose", title: "Compose terms", description: "Generate search terms for each curated subtopic.", force: "Regenerate", classicTab: "Compose" },
  {
    kind: "compile",
    title: "Compile paper",
    description: "Plan the table of contents and write each section with the agent.",
    force: "Rebuild the table of contents",
    needsAgent: true,
    classicTab: "Publish",
  },
]
