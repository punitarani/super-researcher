from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import atomic_write_json, atomic_write_text, ensure_storage_root, load_api_keys, redact_keys, slugify
from .ingest import ingest_sources
from .llm import LLMClient, agent_status
from .phase1 import build_protocol
from .phase2 import (
    create_search_batches,
    generate_heuristics,
    select_sources,
    write_corpus_index,
    write_dedupe_report,
    write_quality_report,
)
from .search import dedupe_candidates, discover_candidates


RUNS: dict[str, "ResearchRun"] = {}


class ResearchRun:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        now = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.slug = f"{now}-{slugify(payload['topic'])}_Corpus"
        self.run_id = self.slug
        storage_root = ensure_storage_root(payload.get("storage_root") or Path.cwd() / "research_runs")
        self.dossier = storage_root / self.slug
        self.dossier.mkdir(parents=True, exist_ok=False)
        for sub in ("logs", "originals", "markdown", "assets", "data"):
            (self.dossier / sub).mkdir(exist_ok=True)
        self.status: dict[str, Any] = {
            "run_id": self.run_id,
            "state": "queued",
            "topic": payload["topic"],
            "dossier_path": str(self.dossier),
            "started_at": None,
            "completed_at": None,
            "milestone": "Queued",
            "progress": 0,
            "counts": {},
            "events": [],
            "error": None,
            "quality": None,
            "files": {},
        }
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        RUNS[self.run_id] = self
        self.thread.start()
        threading.Thread(target=self._heartbeat, daemon=True).start()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.status))

    def event(self, message: str, milestone: str | None = None, progress: int | None = None, **counts: Any) -> None:
        with self._lock:
            if milestone:
                self.status["milestone"] = milestone
            if progress is not None:
                self.status["progress"] = progress
            self.status["counts"].update({k: v for k, v in counts.items() if v is not None})
            self.status["events"].append({"time": datetime.now().isoformat(timespec="seconds"), "message": message})
            self.status["events"] = self.status["events"][-200:]
            atomic_write_json(self.dossier / "run.json", self.status)

    def _heartbeat(self) -> None:
        while True:
            time.sleep(300)
            snapshot = self.snapshot()
            if snapshot["state"] in {"completed", "failed"}:
                return
            counts = snapshot.get("counts", {})
            message = "Still working."
            if counts:
                compact = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in sorted(counts.items())[:4])
                message = f"Still working. {compact}."
            self.event(message)

    def _run(self) -> None:
        keys = load_api_keys()
        try:
            self.status["state"] = "running"
            self.status["started_at"] = datetime.now().isoformat(timespec="seconds")
            self.event("Run started. Creating dossier and validating storage.", "Run started", 3)
            llm = LLMClient(keys)
            atomic_write_json(
                self.dossier / "settings.json",
                {**self.payload, "agent": llm.agent, "configured_api_keys": redact_keys(keys)},
            )
            agent = agent_status(llm.agent, keys)
            if not agent["ready"]:
                llm.unavailable = agent["message"]
                self.event(f"{agent['label']} isn't ready, so planning uses built-in defaults. {agent['message']}")

            self.event("Dossier created. Building the research protocol.", "Dossier created", 8)
            protocol = build_protocol(self.payload, llm)
            atomic_write_json(self.dossier / "research_protocol.json", protocol)
            self._file("research_protocol", self.dossier / "research_protocol.json")

            self.event("Research protocol created. Generating search heuristics.", "Research plan created", 18)
            heuristics = generate_heuristics(protocol, llm, self.dossier / "search_heuristics.json")
            self._file("search_heuristics", self.dossier / "search_heuristics.json")

            plan = create_search_batches(protocol, heuristics, self.dossier / "parallel-search-batches.json")
            self._file("parallel_search_batches", self.dossier / "parallel-search-batches.json")
            self.event(
                "Search plan created. Starting candidate discovery.",
                "Candidate source discovery started",
                28,
                planned_searches=plan["planned_search_count"],
                estimated_candidate_checks=plan["estimated_candidate_checks"],
            )

            max_batches = self.payload.get("max_batches")
            candidates = discover_candidates(plan, keys, progress=lambda msg: self.event(msg), max_batches=max_batches)
            write_jsonl(self.dossier / "candidate_sources.jsonl", candidates)
            self._file("candidate_sources", self.dossier / "candidate_sources.jsonl")
            self.event(
                f"Discovery complete: {len(candidates)} candidate discoveries found. Deduping and ranking.",
                "Candidate source discovery completed",
                48,
                candidate_sources=len(candidates),
            )

            deduped, duplicate_count = dedupe_candidates(candidates)
            write_jsonl(self.dossier / "deduped_sources.jsonl", deduped)
            write_dedupe_report(self.dossier / "dedupe-report.md", len(candidates), deduped, duplicate_count)
            self._file("dedupe_report", self.dossier / "dedupe-report.md")
            target = protocol["run_settings"]["final_source_count"]
            selected = select_sources(deduped, target)
            write_jsonl(self.dossier / "selected_sources.jsonl", selected)
            self._file("selected_sources", self.dossier / "selected_sources.jsonl")
            self.event(
                f"Source ranking complete: {len(deduped)} deduped candidates, {len(selected)} selected for ingestion.",
                "Source ranking and selection completed",
                58,
                deduped_candidates=len(deduped),
                selected_sources=len(selected),
            )

            ingested = ingest_sources(selected, self.dossier, keys, progress=lambda msg: self.event(msg))
            write_jsonl(self.dossier / "ingested_sources.jsonl", ingested)
            self._file("ingested_sources", self.dossier / "ingested_sources.jsonl")
            self.event(
                "Source ingestion finished. Building corpus index and quality report.",
                "Source ingestion completed",
                82,
                ingested_sources=sum(1 for s in ingested if s.get("fetch_status") == "downloaded"),
            )

            write_corpus_index(self.dossier / "research_corpus_index.md", ingested)
            self._file("corpus_index", self.dossier / "research_corpus_index.md")
            quality = write_quality_report(self.dossier / "quality-report.md", protocol, ingested, deduped)
            self._file("quality_report", self.dossier / "quality-report.md")
            self.status["quality"] = quality
            self.event(
                f"Quality check complete: {quality['verdict']}.",
                "Corpus quality check completed",
                94,
                quality_verdict=quality["verdict"],
            )

            summary = final_summary(self.payload, protocol, candidates, deduped, ingested, quality, self.dossier)
            atomic_write_text(self.dossier / "run-summary.md", summary)
            self._file("run_summary", self.dossier / "run-summary.md")
            with self._lock:
                self.status["state"] = "completed"
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")
            self.event("Run completed.", "Run completed", 100)
        except Exception as exc:
            trace = traceback.format_exc()
            atomic_write_text(self.dossier / "logs" / "error.log", trace)
            with self._lock:
                self.status["state"] = "failed"
                self.status["error"] = str(exc)
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")
            self.event(f"Run failed: {exc}", "Run failed", self.status.get("progress", 0))

    def _file(self, key: str, path: Path) -> None:
        with self._lock:
            self.status["files"][key] = str(path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    tmp.replace(path)


def final_summary(
    payload: dict[str, Any],
    protocol: dict[str, Any],
    candidates: list[dict[str, Any]],
    deduped: list[dict[str, Any]],
    ingested: list[dict[str, Any]],
    quality: dict[str, Any],
    dossier: Path,
) -> str:
    downloaded = sum(1 for s in ingested if s.get("fetch_status") == "downloaded")
    warning = quality["warnings"][0] if quality.get("warnings") else "None"
    return f"""# Research Run Summary

Research run complete.

Topic: {payload["topic"]}
Depth: {protocol["run_settings"]["depth"]}
Breadth: {protocol["run_settings"]["breadth"]}
Final source target: {protocol["run_settings"]["final_source_count"]}

Candidate sources found: {len(candidates)}
Deduped candidates: {len(deduped)}
Selected sources: {len(ingested)}
Ingested sources: {downloaded}
Quality verdict: {quality["verdict"]}

Main warning: {warning}
Dossier path: {dossier}
Quality report: {dossier / "quality-report.md"}
"""
