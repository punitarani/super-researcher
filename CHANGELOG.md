# Changelog

## Unreleased

- Agent picker: choose **Codex (ChatGPT plan)** or **Gemini (API key)** from the top bar. The choice is saved, and the selected agent never silently falls back to another provider.
- Codex runs on the user's ChatGPT subscription through their own Codex CLI. The app checks that Codex is installed (0.122+) and signed in with ChatGPT, explains how to fix it, and can start `codex login` for you. It never reads or stores credentials, strips `CODEX_API_KEY`/`OPENAI_API_KEY` from Codex's environment, and runs each prompt in an isolated, read-only session without tools or web search.
- Codex uses the default model for the user's plan instead of hardcoded `gpt-5`/`gpt-5.5` (optional `CODEX_MODEL` override).
- Clear errors for expired sign-ins, plan usage limits, and network problems. After a sign-in or usage-limit failure, a job stops calling Codex instead of retrying every prompt.
- Topic discovery and compile check the agent before starting; research runs log when they fall back to built-in planning.
- Web UI in `ui/` (Next.js), started with `npm run ui`: choose and set up the agent, start, watch live, stop and review runs, and run the five corpus pipeline jobs with live progress. Both servers bind to 127.0.0.1, and credentials never reach the browser.
- Research runs can be stopped (`POST /api/runs/<id>/stop`); they stop at the next step.
- Run history survives restarts: `GET /api/runs` lists the runs saved under the storage root, and runs that were cut off by a restart show as interrupted.

## 0.2.0 — 2026-09-26

First public release.

- Local-first research pipeline: protocol → discovery → corpus → Atlas → publish, served by a dependency-free Python HTTP server with a vanilla-JS UI.
- Configurable Codex binary (`CODEX_BIN` env, PATH lookup, legacy .app fallback), storage root (`SUPERRESEARCHER_STORAGE_ROOT`), and keys file (`SUPERRESEARCHER_API_KEYS`).
- `superresearcher` console entry point (`superresearcher --host/--port/--version`) and `pyproject.toml` packaging; core remains 100% stdlib.
- Secrets hygiene: `api_keys.txt` git-ignored, `api_keys.example.txt` template.
- Version alignment across package, server banner, and HTTP user agents.
- 80 unit tests passing.
