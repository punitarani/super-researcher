# SuperResearcher 🦸

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-93%20passing-brightgreen)]()

**SuperResearcher is a local-first research harness.** Give it a topic and it runs the research end to end: planning the protocol, discovering and downloading real sources, building a durable corpus with readable sidecars, mapping it in an embedding Atlas, and compiling a publishable, consulting-grade report. It reports progress every 5 minutes and gives honest quality verdicts — Pass, Pass with warnings, or Fail.

## 📄 Example reports

Real reports produced by the harness — see [`outputs/`](outputs/). They replicate consulting-grade reports: structured chapters, evidence-backed claims, and charts pulled from the actual sources.

- **[Global Migration 1826–2226: A 200-Year Forecast](outputs/global-migration-1826-2226/)** — 51 sections, 395 inline source citations, 32 figures extracted from the source PDFs (McKinsey and Meta Research editions)
- **[eVTOL UAM Flying Car: Feasibility, Opportunity, Technology, Scaling and Risks](outputs/evtol-uam-flying-car/)** — 54 pages, 55 sections, 367 inline citations, 11 source-extracted figures

What you get: a consulting-style PDF where every claim carries a source citation, and every chart comes from the actual sources.

## 🧭 Guided tour

Opening the app for the first time? Start with the [guided tour](docs/guided-tour/) — nine pages, one per stage, with every setting explained and the reasoning behind it. Available as [PDF](docs/guided-tour/guided-tour.pdf) and [HTML](docs/guided-tour/guided-tour.html).


## How it works

```
Topic → Phase 1: Research Protocol → Phase 2: Discover → Download → Dedupe → Rank
        → Corpus (originals + Markdown sidecars + metadata)
        → Post-process (repair) → Atlas (embed, map, curate) → Publish (paper/report)
```

- **Phase 1** — research protocol: topic, context, controls, archetype classification, rubric assessment, default assumptions.
- **Phase 2** — search heuristics, parallel search plan, candidate discovery, source downloads, dedupe, Markdown sidecars, corpus index, quality report.
- **Post-process** — repairs corpus outputs after acquisition: filename/type mismatches, Markdown sidecar regeneration, targeted re-fetch of wrong payloads, index updates.
- **Atlas** — chunk Markdown sidecars, embed locally, 2D projection, point inspection, Keep/Reject/Key-Evidence curation.
- **Topic discovery** — mines tables of contents and Atlas heading paths into a topic/subtopic outline.
- **Publish** — compiles sections into papers and reports (Markdown, HTML, PDF via pandoc/WeasyPrint/xelatex when installed), with native figure extraction from source PDFs, scored section-matching, and source-attributed figure captions.

Runs are written under `research_runs/<timestamp-topic>_Corpus/`. Writes are atomic, storage is checked up front (1 GB free required), and LLM planning failure never kills a run.

## Quickstart

Requires Python 3.10+ and Node 18+ (only for building the Atlas frontend bundle).

```bash
git clone https://github.com/kumar-vis/super-researcher.git
cd super-researcher

# Recommended: AI agent on your ChatGPT plan (no API key needed)
npm install -g @openai/codex            # or: brew install --cask codex
codex login                             # sign in with your ChatGPT account

# Optional: search providers and Gemini (app works without keys, with fallbacks)
cp api_keys.example.txt api_keys.txt   # then fill in your keys

# Optional: Atlas embeddings + better PDF extraction
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-atlas.txt   # embeddings/projection
pip install pymupdf beautifulsoup4 lxml # PDF extraction
npm install && npm run build:atlas      # Atlas frontend bundle

python3 run_app.py
# → http://127.0.0.1:8765
```

Or install the CLI: `pip install .` then run `superresearcher` (UI assets ship inside the package).

### Agent: Codex on your ChatGPT plan

The agent writes research plans, topic trees, and report sections. Pick it from the chip in the top bar:

- **Codex · ChatGPT plan** (default): runs the [Codex CLI](https://github.com/openai/codex) you installed, signed in with your ChatGPT account (Plus, Pro, Business, Edu, or Enterprise). Usage counts against your plan's Codex limits. The app checks that Codex is installed (0.122 or newer) and signed in with ChatGPT, and shows how to fix it if not. **Sign in with ChatGPT** in the agent panel runs `codex login` for you; on a machine without a browser, run `codex login --device-auth`.
- **Gemini · API key**: uses `GEMINI_API_KEY` from `api_keys.txt`.

The app never sees or stores your ChatGPT credentials: Codex handles sign-in and keeps its own tokens. Each prompt runs in a throwaway, read-only Codex session with tools, web search, and your personal Codex config turned off, and without `CODEX_API_KEY`/`OPENAI_API_KEY` in its environment, so it always uses your ChatGPT plan rather than an API account. The selected agent never silently falls back to another provider. Codex picks the best model for your plan; set `CODEX_MODEL` in `api_keys.txt` to override it.

### Configuration

| Setting | Where | Default |
|---|---|---|
| API keys | `api_keys.txt` (git-ignored) or `SUPERRESEARCHER_API_KEYS` | — |
| Storage root | `SUPERRESEARCHER_STORAGE_ROOT` | `<repo>/research_runs` |
| Agent | Top-bar agent picker (saved to `<storage root>/app-settings.json`) or `SUPERRESEARCHER_AGENT=codex\|gemini` (overrides the picker) | Codex, or Gemini if only a Gemini key is set up |
| Codex binary | `CODEX_BIN` env, else `codex` on PATH, else Codex.app bundle | auto-detected |
| Host / port | `--host`, `--port` flags. Starting runs and jobs or changing settings only works from this computer, whatever the host. | `127.0.0.1:8765` |

**Providers.** The selected agent (Codex at high reasoning effort, or Gemini via `GEMINI_API_KEY`/`GOOGLE_API_KEY`) handles LLM work; if it isn't set up, research runs use built-in planning defaults, and topic discovery and compiling explain what to fix. Search/fetch adapters use Exa, Serper, SerpAPI, and Firecrawl when keys are present; if a provider rejects its key or runs out of credits, the run log names it, and a run whose searches all fail stops with that error. Everything degrades gracefully without keys.

**Depth presets** (final source targets): low 3 · medium 7 · high 10 · extra-high 15 · ludicrous 50. Default final-source target 120, max 500.

## Repo layout

```
superresearcher/        Python package (stdlib-only core)
  server.py             Plain HTTP server + JSON API (no framework)
  runner.py / phase1.py / phase2.py
  search.py / ingest.py / doc_convert.py
  postprocess.py        Corpus repair pass
  atlas.py              Embeddings, projection, curation jobs
  topic_discovery.py    TOC/heading mining
  publish.py / reporting.py   Report + paper compilation
  llm.py / codex.py      Agent selection; Codex CLI on your ChatGPT plan
  prompts.py / config.py
superresearcher/web/       Vanilla-JS UI (index.html, app.js, styles.css)
superresearcher/web/atlas-src/  Atlas frontend source (vite → web/atlas/)
outputs/                Published example reports (PDF)
tests/                  93 unit tests
docs/                   Product requirements + original build plans
```

## Tests

```bash
python3 -m unittest discover -s tests
```

## Roadmap

- [ ] Background scheduling / watchlists
- [ ] More search providers
- [ ] Export corpora as Zotero/RIS
- [ ] Multi-user / hosted mode

## License

MIT — see [LICENSE](LICENSE).
