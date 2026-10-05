/**
 * A path saved by the backend, relative to the run folder. Saved paths are absolute, so if the runs
 * folder was moved (or is reached through a symlink, like /var and /private/var on macOS), fall back
 * to the part after the run's own folder name. The file reader still checks it stays inside.
 */
export function relativeToRun(file: string, runFolder: string) {
  if (file.startsWith(`${runFolder}/`)) return file.slice(runFolder.length + 1)
  const marker = `/${runFolder.slice(runFolder.lastIndexOf("/") + 1)}/`
  // The first match is the run folder itself; a later one would be a folder inside it.
  const at = file.indexOf(marker)
  return at >= 0 ? file.slice(at + marker.length) : null
}
