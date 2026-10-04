import "server-only"
import { open, realpath, stat } from "node:fs/promises"
import path from "node:path"

// Reads a run's own text files from disk for the viewer. The backend runs on this computer
// (SUPERRESEARCHER_API_URL must be loopback), so its run folders are local files.

export const VIEWABLE_EXTENSIONS = [".md", ".markdown", ".json", ".jsonl", ".txt"]
export const FIRST_CHUNK = 128 * 1024 // bytes shown before "Show more"
export const MAX_BYTES = 2 * 1024 * 1024 // never read more than this from one file

export class RunFileError extends Error {}

export type RunFile = { relativePath: string; text: string; size: number; truncated: boolean; limit: number }

export function isViewable(relativePath: string) {
  return VIEWABLE_EXTENSIONS.includes(path.extname(relativePath).toLowerCase())
}

/** The run's folder: a real directory named after the run, directly inside the storage root. */
export async function runFolder(storageRoot: string, dossierPath: string, runId: string) {
  try {
    const root = await realpath(storageRoot)
    const folder = await realpath(dossierPath)
    if (path.dirname(folder) === root && path.basename(folder) === runId && (await stat(folder)).isDirectory()) return folder
  } catch {
    // missing or unreadable: refused below
  }
  throw new RunFileError("This run's folder isn't in the storage folder, so its files can't be shown here.")
}

/** Up to `limit` bytes (capped at MAX_BYTES) of a viewable text file inside `runDir` (from runFolder). */
export async function readRunFile(runDir: string, relativePath: string, limit = FIRST_CHUNK): Promise<RunFile> {
  const { folder, target, real, info } = await resolveRunFile(runDir, relativePath)
  const cap = Math.min(Math.max(limit, FIRST_CHUNK), MAX_BYTES)
  const length = Math.min(info.size, cap)
  const buffer = Buffer.alloc(length)
  const handle = await open(real, "r")
  try {
    await handle.read(buffer, 0, length, 0)
  } finally {
    await handle.close()
  }
  // stream: true holds back a character cut off at the end instead of turning it into "�".
  const text = new TextDecoder().decode(buffer, { stream: info.size > cap })
  return { relativePath: path.relative(folder, target), text, size: info.size, truncated: info.size > cap, limit: cap }
}

/** Whether `relativePath` is a viewable file inside the run folder. */
export async function runFileExists(runDir: string, relativePath: string) {
  return resolveRunFile(runDir, relativePath).then(
    () => true,
    () => false,
  )
}

async function resolveRunFile(runDir: string, relativePath: string) {
  if (!relativePath || relativePath.includes("\0") || path.isAbsolute(relativePath)) throw new RunFileError("That isn't a file in this run.")
  if (!isViewable(relativePath)) throw new RunFileError("Only Markdown, JSON and text files can be shown here.")
  const folder = await realpath(runDir)
  // Check the path as written, then again after following symlinks.
  const target = path.resolve(folder, relativePath)
  if (!isInside(folder, target)) throw new RunFileError("That file is outside this run's folder.")
  let real: string
  try {
    real = await realpath(target)
  } catch {
    throw new RunFileError("That file doesn't exist (yet).")
  }
  if (!isInside(folder, real)) throw new RunFileError("That file is outside this run's folder.")
  const info = await stat(real)
  if (!info.isFile()) throw new RunFileError("That isn't a file.")
  return { folder, target, real, info }
}

function isInside(folder: string, candidate: string) {
  return candidate.startsWith(folder + path.sep)
}
