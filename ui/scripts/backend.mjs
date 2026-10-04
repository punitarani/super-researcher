// Starts the Python backend (run_app.py) with any arguments passed to this script.
// Uses the active virtualenv, else the repo's .venv if there is one (where the README
// installs the Atlas and PDF extras), else python3 on PATH.
import { spawn } from "node:child_process"
import { existsSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..")
const windows = process.platform === "win32"
const venvPython = windows ? path.join(root, ".venv", "Scripts", "python.exe") : path.join(root, ".venv", "bin", "python")
const python = !process.env.VIRTUAL_ENV && existsSync(venvPython) ? venvPython : windows ? "python" : "python3"

const backend = spawn(python, [path.join(root, "run_app.py"), ...process.argv.slice(2)], { stdio: "inherit" })
backend.on("error", (error) => {
  console.error(`Couldn't start the backend with ${python}: ${error.message}`)
  process.exit(1)
})
backend.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)))
for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => backend.kill(signal))
