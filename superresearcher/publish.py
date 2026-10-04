from __future__ import annotations

import json
import hashlib
import re
import threading
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import atlas, query_bundles
from .config import atomic_write_json, atomic_write_text, load_api_keys, slugify
from .llm import LLMClient, require_ready_agent


PUBLISH_VERSION = "publish-markdown-v1"
MAX_SECTION_CHUNKS = 30
MAX_SECTION_CONTEXT_CHARS = 45_000
MAX_CHUNKS_PER_SOURCE = 5
MAX_SMALL_VISUAL_BYTES = 35 * 1024
MAX_SQUARE_VISUAL_BYTES = 200 * 1024
MIN_VISUAL_SIDE = 150
PUBLISH_JOBS: dict[str, "PublishCompileJob"] = {}


def publish_dir(corpus: Path) -> Path:
    return corpus / "atlas" / "publish"


def publish_sections_dir(corpus: Path) -> Path:
    return publish_dir(corpus) / "sections"


def publish_plan_path(corpus: Path) -> Path:
    return publish_dir(corpus) / "publish_plan.json"


def publish_state_path(corpus: Path) -> Path:
    return publish_dir(corpus) / "publish_state.json"


def publish_toc_path(corpus: Path) -> Path:
    return publish_dir(corpus) / "toc.md"


def publish_paper_path(corpus: Path) -> Path:
    return publish_dir(corpus) / "paper.md"


def publish_source_index_path(corpus: Path) -> Path:
    return publish_dir(corpus) / "source_index.json"


def visual_candidates_path(corpus: Path) -> Path:
    return publish_dir(corpus) / "visual_candidates.jsonl"


def visual_selections_path(corpus: Path) -> Path:
    return publish_dir(corpus) / "visual_selections.json"


def visual_plan_path(corpus: Path) -> Path:
    return publish_dir(corpus) / "visual_plan.json"


def get_publish_payload(corpus: str = "latest") -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    plan = read_json_object(publish_plan_path(corpus_path))
    state = read_json_object(publish_state_path(corpus_path))
    finalized_payload = read_json_object(query_bundles.compose_finalized_json_path(corpus_path))
    finalized = {
        "exists": bool(finalized_payload),
        "path": str(query_bundles.compose_finalized_json_path(corpus_path)),
        "markdown_path": str(query_bundles.compose_finalized_md_path(corpus_path)),
        "payload": finalized_payload or None,
    }
    sections = list_section_files(corpus_path)
    return {
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "ready": bool(finalized.get("exists")),
        "finalized": finalized,
        "plan": {
            "exists": bool(plan),
            "path": str(publish_plan_path(corpus_path)),
            "payload": plan or None,
            "toc_markdown": publish_toc_path(corpus_path).read_text(encoding="utf-8", errors="replace")
            if publish_toc_path(corpus_path).exists()
            else "",
        },
        "state": state or default_publish_state(corpus_path),
        "sections": sections,
        "paper": {
            "exists": publish_paper_path(corpus_path).exists(),
            "path": str(publish_paper_path(corpus_path)),
        },
        "source_index": {
            "exists": publish_source_index_path(corpus_path).exists(),
            "path": str(publish_source_index_path(corpus_path)),
        },
    }


def get_visual_payload(corpus: str = "latest") -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    selections = read_json_object(visual_selections_path(corpus_path))
    plan = read_json_object(visual_plan_path(corpus_path))
    candidates = read_visual_candidates(corpus_path)
    return {
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "candidates": {
            "exists": visual_candidates_path(corpus_path).exists(),
            "path": str(visual_candidates_path(corpus_path)),
            "items": attach_visual_state(corpus_path, candidates, selections),
        },
        "selections": {
            "exists": visual_selections_path(corpus_path).exists(),
            "path": str(visual_selections_path(corpus_path)),
            "payload": selections or default_visual_selections(corpus_path),
        },
        "plan": {
            "exists": visual_plan_path(corpus_path).exists(),
            "path": str(visual_plan_path(corpus_path)),
            "payload": plan or default_visual_plan(corpus_path),
        },
        "summary": visual_summary(candidates, selections, plan),
    }


def build_visual_candidates(corpus: str = "latest") -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    publish_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    candidates = rank_visual_candidates(corpus_path, extract_visual_candidates(corpus_path))
    atlas.write_jsonl(visual_candidates_path(corpus_path), candidates)
    ensure_visual_plan(corpus_path)
    return get_visual_payload(str(corpus_path))


def save_visual_selections(corpus: str, payload: dict[str, Any]) -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    publish_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    current = read_json_object(visual_selections_path(corpus_path)) or default_visual_selections(corpus_path)
    selections = dict(current.get("selections") or {})
    incoming = payload.get("selections")
    if isinstance(incoming, dict):
        selections = normalize_visual_selections(incoming)
    else:
        candidate_id = str(payload.get("candidate_id", "")).strip()
        status = str(payload.get("status", "")).strip()
        if candidate_id:
            if status in {"", "none", "clear"}:
                selections.pop(candidate_id, None)
            else:
                normalized = normalize_visual_selection({**(selections.get(candidate_id) or {}), **payload, "status": status})
                if normalized:
                    selections[candidate_id] = normalized
    out = {
        "version": PUBLISH_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "selections": selections,
    }
    atomic_write_json(visual_selections_path(corpus_path), out)
    ensure_visual_plan(corpus_path)
    return get_visual_payload(str(corpus_path))


def create_publish_plan(corpus: str = "latest", custom_prompts: dict[str, str] | None = None, llm: LLMClient | None = None) -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    finalized = finalized_compose_payload(corpus_path)
    sections = finalized.get("subtopics", [])
    if not sections:
        raise RuntimeError("No finalized Compose sections found. Finalize Compose first.")
    llm = llm or LLMClient(load_api_keys())
    fallback = fallback_toc_plan(corpus_path, sections)
    generated = llm.json_call(toc_prompt(corpus_path, sections), fallback)
    plan_sections = validate_toc_sections(generated, sections)
    out = {
        "version": PUBLISH_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "compose_cache_signature": finalized.get("cache_signature"),
        "sections": plan_sections,
        "custom_prompts": normalize_custom_prompts(custom_prompts or {}),
        "summary": {
            "section_count": len(plan_sections),
            "selected_tag_count": finalized.get("summary", {}).get("selected_tag_count", 0),
        },
    }
    publish_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    publish_sections_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    atomic_write_json(publish_plan_path(corpus_path), out)
    atomic_write_text(publish_toc_path(corpus_path), render_toc_markdown(out))
    state = read_json_object(publish_state_path(corpus_path)) or default_publish_state(corpus_path)
    state.update(
        {
            "version": PUBLISH_VERSION,
            "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
            "plan_path": str(publish_plan_path(corpus_path)),
            "section_count": len(plan_sections),
            "custom_prompts": out["custom_prompts"],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    atomic_write_json(publish_state_path(corpus_path), state)
    return get_publish_payload(str(corpus_path))


class PublishCompileJob:
    def __init__(self, corpus: Path, custom_prompts: dict[str, str] | None = None, force_plan: bool = False) -> None:
        self.corpus = corpus.resolve()
        self.custom_prompts = normalize_custom_prompts(custom_prompts or {})
        self.force_plan = force_plan
        self.job_id = f"publish-{self.corpus.name}-{int(time.time())}"
        self.status: dict[str, Any] = {
            "job_id": self.job_id,
            "corpus_id": self.corpus.name,
            "corpus_path": str(self.corpus),
            "state": "queued",
            "stage": "Queued",
            "progress": 0,
            "counts": {},
            "error": None,
            "started_at": None,
            "completed_at": None,
        }
        self._lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        PUBLISH_JOBS[self.job_id] = self
        self.thread.start()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self.status))

    def event(self, stage: str, progress: int, **counts: Any) -> None:
        with self._lock:
            self.status["stage"] = stage
            self.status["progress"] = progress
            self.status["counts"].update(counts)

    def _run(self) -> None:
        try:
            with self._lock:
                self.status["state"] = "running"
                self.status["started_at"] = datetime.now().isoformat(timespec="seconds")
            llm = LLMClient(load_api_keys())
            result = compile_publish_paper(
                str(self.corpus),
                custom_prompts=self.custom_prompts,
                force_plan=self.force_plan,
                llm=llm,
                progress=self.event,
            )
            with self._lock:
                self.status["state"] = "completed"
                self.status["stage"] = "Ready"
                self.status["progress"] = 100
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")
                self.status["counts"].update(result.get("summary", {}))
        except Exception as exc:
            log_path = publish_dir(self.corpus) / "publish-error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
            with self._lock:
                self.status["state"] = "failed"
                self.status["error"] = str(exc)
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")


def start_publish_compile_job(corpus_id_or_path: str, custom_prompts: dict[str, str] | None = None, force_plan: bool = False) -> PublishCompileJob:
    corpus = query_bundles.resolve_topic_corpus(corpus_id_or_path)
    for job in PUBLISH_JOBS.values():
        snap = job.snapshot()
        if snap["corpus_path"] == str(corpus) and snap["state"] in {"queued", "running"}:
            return job
    require_ready_agent()
    job = PublishCompileJob(corpus, custom_prompts=custom_prompts, force_plan=force_plan)
    job.start()
    return job


def get_publish_job(job_id: str) -> PublishCompileJob | None:
    return PUBLISH_JOBS.get(job_id)


def compile_publish_paper(
    corpus: str = "latest",
    custom_prompts: dict[str, str] | None = None,
    force_plan: bool = False,
    llm: LLMClient | None = None,
    progress=None,
) -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    llm = llm or LLMClient(load_api_keys())
    custom_prompts = normalize_custom_prompts(custom_prompts or {})
    if progress:
        progress("Preparing publish plan", 5)
    if force_plan or not publish_plan_path(corpus_path).exists():
        create_publish_plan(str(corpus_path), custom_prompts=custom_prompts, llm=llm)
    else:
        merge_custom_prompts(corpus_path, custom_prompts)
    plan = read_json_object(publish_plan_path(corpus_path))
    if not plan.get("sections"):
        raise RuntimeError("Publish plan has no sections.")
    finalized = finalized_compose_payload(corpus_path)
    sections_by_topic = {section.get("topic_id"): section for section in finalized.get("subtopics", [])}
    chunks, selections = load_publish_units(corpus_path)
    state = read_json_object(publish_state_path(corpus_path)) or default_publish_state(corpus_path)
    state.setdefault("completed_sections", [])
    source_index: dict[str, Any] = read_json_object(publish_source_index_path(corpus_path))
    continuity_summary = state.get("continuity_summary", "")
    total = len(plan["sections"])
    completed_ids = {item.get("section_id") for item in state.get("completed_sections", [])}
    for index, plan_section in enumerate(plan["sections"], start=1):
        section_id = plan_section["section_id"]
        source_topic_id = plan_section["source_topic_id"]
        if section_id in completed_ids and section_file_path(corpus_path, index, plan_section).exists():
            if progress:
                progress(f"Skipping completed section: {plan_section['title']}", 10 + int(index / max(total, 1) * 82), completed_sections=len(completed_ids), section_count=total)
            continue
        source_section = sections_by_topic.get(source_topic_id)
        if not source_section:
            raise RuntimeError(f"Finalized section not found for {source_topic_id}.")
        if progress:
            progress(f"Compiling section {index}/{total}: {plan_section['title']}", 10 + int((index - 1) / max(total, 1) * 82), completed_sections=len(completed_ids), section_count=total, current_section=plan_section["title"])
        ranked = rank_chunks_for_section(source_section, chunks, selections)
        selected_chunks = cap_chunks(ranked)
        update_source_index(source_index, selected_chunks)
        prompt = section_prompt(corpus_path, plan, plan_section, source_section, selected_chunks, continuity_summary)
        section_markdown = llm.text_call(prompt)
        if not section_markdown.strip().startswith("#"):
            section_markdown = f"## {plan_section['title']}\n\n{section_markdown.strip()}\n"
        path = section_file_path(corpus_path, index, plan_section)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, section_markdown.strip() + "\n")
        continuity_summary = update_continuity_summary(llm, continuity_summary, plan_section, section_markdown)
        completed = {
            "section_id": section_id,
            "source_topic_id": source_topic_id,
            "title": plan_section["title"],
            "path": str(path),
            "chunk_count": len(selected_chunks),
            "completed_at": datetime.now().isoformat(timespec="seconds"),
        }
        state = {
            **state,
            "state": "running",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "section_count": total,
            "completed_sections": [item for item in state.get("completed_sections", []) if item.get("section_id") != section_id] + [completed],
            "continuity_summary": continuity_summary,
            "custom_prompts": plan.get("custom_prompts", {}),
        }
        atomic_write_json(publish_source_index_path(corpus_path), source_index)
        atomic_write_json(publish_state_path(corpus_path), state)
        write_final_paper(corpus_path, plan, state, source_index)
        completed_ids.add(section_id)
    state["state"] = "completed"
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    atomic_write_json(publish_state_path(corpus_path), state)
    atomic_write_json(publish_source_index_path(corpus_path), source_index)
    write_final_paper(corpus_path, plan, state, source_index)
    if progress:
        progress("Paper compiled", 96, completed_sections=len(state.get("completed_sections", [])), section_count=total)
    return get_publish_payload(str(corpus_path))


def finalized_compose_payload(corpus: Path) -> dict[str, Any]:
    finalized = read_json_object(query_bundles.compose_finalized_json_path(corpus))
    if not finalized:
        raise RuntimeError("Finalized Compose terms not found. Finalize Compose first.")
    return finalized


def toc_prompt(corpus: Path, sections: list[dict[str, Any]]) -> str:
    compact = [
        {
            "topic_id": section.get("topic_id"),
            "label": section.get("label"),
            "path": section.get("path", []),
            "parent_label": section.get("parent_label", ""),
        }
        for section in sections
    ]
    return f"""You are arranging a research paper table of contents.

Use only the provided finalized sections. Do not invent sections. Arrange them into a cohesive paper flow.

Corpus: {corpus.name}
Finalized sections:
{json.dumps(compact, indent=2, ensure_ascii=False)}

Return strict JSON only:
{{
  "sections": [
    {{
      "section_id": "section-001",
      "title": "paper section title",
      "source_topic_id": "must exactly match one provided topic_id",
      "rationale": "short reason for this ordering"
    }}
  ]
}}
"""


def fallback_toc_plan(corpus: Path, sections: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sections": [
            {
                "section_id": f"section-{index:03d}",
                "title": section.get("label", f"Section {index}"),
                "source_topic_id": section.get("topic_id", ""),
                "rationale": "Fallback order from finalized Compose tree.",
            }
            for index, section in enumerate(sections, start=1)
        ]
    }


def validate_toc_sections(generated: Any, finalized_sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_topic = {section.get("topic_id"): section for section in finalized_sections if section.get("topic_id")}
    used: set[str] = set()
    rows: list[dict[str, Any]] = []
    raw_sections = generated.get("sections", []) if isinstance(generated, dict) else []
    for item in raw_sections:
        if not isinstance(item, dict):
            continue
        topic_id = str(item.get("source_topic_id", ""))
        if topic_id not in by_topic or topic_id in used:
            continue
        source = by_topic[topic_id]
        rows.append(
            {
                "section_id": f"section-{len(rows) + 1:03d}",
                "title": clean_title(item.get("title") or source.get("label") or f"Section {len(rows) + 1}"),
                "source_topic_id": topic_id,
                "source_label": source.get("label", ""),
                "source_path": source.get("path", []),
                "rationale": str(item.get("rationale", "")).strip(),
            }
        )
        used.add(topic_id)
    for source in finalized_sections:
        topic_id = source.get("topic_id")
        if topic_id and topic_id not in used:
            rows.append(
                {
                    "section_id": f"section-{len(rows) + 1:03d}",
                    "title": clean_title(source.get("label") or f"Section {len(rows) + 1}"),
                    "source_topic_id": topic_id,
                    "source_label": source.get("label", ""),
                    "source_path": source.get("path", []),
                    "rationale": "Appended because this finalized section was not included in the LLM TOC.",
                }
            )
            used.add(topic_id)
    return rows


def load_publish_units(corpus: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    chunks = atlas.read_jsonl(corpus / "atlas" / "chunks.jsonl")
    selections = {
        row["chunk_id"]: row
        for row in atlas.read_jsonl(corpus / "atlas" / "selections.jsonl")
        if row.get("chunk_id")
    }
    units = []
    for chunk in chunks:
        status = selections.get(chunk.get("id"), {}).get("status", "unmarked")
        if status == "reject":
            continue
        row = dict(chunk)
        row["selection_status"] = status
        row["selection_notes"] = selections.get(chunk.get("id"), {}).get("notes", "")
        units.append(row)
    return units, selections


def rank_chunks_for_section(section: dict[str, Any], chunks: list[dict[str, Any]], selections: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    tags = [tag.get("label", "") for tag in section.get("tags", []) if tag.get("selected", True)]
    query_terms = [section.get("label", ""), " ".join(section.get("path", []))] + tags
    compiled = compile_terms(query_terms)
    rows = []
    for chunk in chunks:
        text = f"{metadata(chunk).get('title', '')} {metadata(chunk).get('section_path', '')} {chunk.get('text', '')}"
        text_norm = normalize(text)
        tag_hits = sum(1 for term in compiled["tags"] if term and term in text_norm)
        label_score = overlap_score(compiled["section_tokens"], text_norm)
        tag_score = overlap_score(compiled["tag_tokens"], text_norm)
        status = chunk.get("selection_status") or selections.get(chunk.get("id"), {}).get("status", "unmarked")
        curation_bonus = {"key_evidence": 8.0, "keep": 5.0, "maybe": 1.5, "unmarked": 0.0}.get(status, 0.0)
        quality = chunk_quality_score(chunk.get("text", ""))
        score = label_score * 14.0 + tag_score * 10.0 + tag_hits * 3.0 + curation_bonus + quality
        if score <= 0:
            continue
        row = dict(chunk)
        row["publish_score"] = round(score, 4)
        row["publish_reasons"] = {
            "tag_hits": tag_hits,
            "label_score": round(label_score, 4),
            "tag_score": round(tag_score, 4),
            "curation_status": status,
            "quality": round(quality, 4),
        }
        rows.append(row)
    return sorted(rows, key=lambda item: item["publish_score"], reverse=True)


def cap_chunks(chunks: list[dict[str, Any]], max_chunks: int = MAX_SECTION_CHUNKS, max_chars: int = MAX_SECTION_CONTEXT_CHARS) -> list[dict[str, Any]]:
    selected = []
    source_counts: dict[str, int] = defaultdict(int)
    chars = 0
    for chunk in chunks:
        source_id = metadata(chunk).get("source_id", "unknown")
        if source_counts[source_id] >= MAX_CHUNKS_PER_SOURCE:
            continue
        text = clean_chunk_text(chunk.get("text", ""))
        if not text:
            continue
        next_chars = chars + len(text)
        if selected and next_chars > max_chars:
            continue
        row = dict(chunk)
        row["text"] = text[:8000]
        selected.append(row)
        source_counts[source_id] += 1
        chars += len(row["text"])
        if len(selected) >= max_chunks or chars >= max_chars:
            break
    return selected


def section_prompt(
    corpus: Path,
    plan: dict[str, Any],
    plan_section: dict[str, Any],
    source_section: dict[str, Any],
    chunks: list[dict[str, Any]],
    continuity_summary: str,
) -> str:
    custom = plan.get("custom_prompts", {}).get(plan_section["source_topic_id"], "")
    tags = [tag.get("label", "") for tag in source_section.get("tags", []) if tag.get("selected", True)]
    toc = "\n".join(f"{idx}. {section['title']}" for idx, section in enumerate(plan.get("sections", []), start=1))
    chunk_text = "\n\n".join(render_prompt_chunk(chunk) for chunk in chunks)
    return f"""You are writing one section of a source-backed research paper.

Rules:
- Use only the provided source chunks as factual evidence.
- Cite factual claims with local inline citations like [S0001:C0003].
- Do not repeat prior sections; use the continuity summary only for flow.
- Write polished Markdown for this section only.
- Follow the user's section customization request if present.

Paper/corpus: {corpus.name}
Full table of contents:
{toc}

Current section title: {plan_section['title']}
Original curated section: {' > '.join(source_section.get('path', []))}
Selected tags: {', '.join(tags[:80])}
User section customization request:
{custom or '(none)'}

Continuity summary from prior sections:
{continuity_summary or '(first section; no prior summary)'}

Source chunks:
{chunk_text}
"""


def render_prompt_chunk(chunk: dict[str, Any]) -> str:
    meta = metadata(chunk)
    source_id = str(meta.get("source_id", "unknown")).zfill(4) if str(meta.get("source_id", "")).isdigit() else str(meta.get("source_id", "unknown"))
    chunk_id = chunk.get("id", "")
    citation = f"[S{source_id}:C{chunk_id}]"
    return "\n".join(
        [
            f"### {citation}",
            f"Title: {meta.get('title', 'Untitled source')}",
            f"Publisher: {meta.get('publisher', 'unknown')}",
            f"Section path: {meta.get('section_path', '')}",
            f"Curation: {chunk.get('selection_status', 'unmarked')}",
            "",
            chunk.get("text", ""),
        ]
    )


def update_continuity_summary(llm: LLMClient, previous_summary: str, plan_section: dict[str, Any], section_markdown: str) -> str:
    prompt = f"""Compact the running paper summary for continuity.

Keep the summary under 900 words. Preserve flow, key findings, unresolved tensions, and facts that later sections should not repeat.

Previous summary:
{previous_summary or '(none)'}

New section title: {plan_section['title']}
New section:
{section_markdown[:20000]}
"""
    try:
        return llm.text_call(prompt).strip()[:7000]
    except Exception:
        fallback = f"{previous_summary}\n\n{plan_section['title']}: {strip_markdown(section_markdown)[:1200]}".strip()
        return fallback[-7000:]


def write_final_paper(corpus: Path, plan: dict[str, Any], state: dict[str, Any], source_index: dict[str, Any]) -> None:
    title = paper_title(corpus)
    completed_by_id = {item.get("section_id"): item for item in state.get("completed_sections", [])}
    parts = [f"# {title}", "", "## Table of Contents", ""]
    for index, section in enumerate(plan.get("sections", []), start=1):
        parts.append(f"{index}. {section['title']}")
    parts.append("")
    for index, section in enumerate(plan.get("sections", []), start=1):
        completed = completed_by_id.get(section["section_id"])
        if not completed:
            continue
        path = Path(completed.get("path", ""))
        if path.exists():
            parts.extend([path.read_text(encoding="utf-8", errors="replace").strip(), ""])
    parts.extend(["## Source Index", ""])
    for source_id, source in sorted(source_index.get("sources", {}).items()):
        title = source.get("title", "Untitled source")
        publisher = source.get("publisher", "unknown")
        url = source.get("url", "")
        parts.append(f"- S{source_id}: {title} ({publisher}) {url}".strip())
    atomic_write_text(publish_paper_path(corpus), "\n".join(parts).strip() + "\n")


def update_source_index(index: dict[str, Any], chunks: list[dict[str, Any]]) -> None:
    index.setdefault("version", PUBLISH_VERSION)
    index.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    index.setdefault("sources", {})
    index.setdefault("chunks", {})
    for chunk in chunks:
        meta = metadata(chunk)
        raw_source_id = str(meta.get("source_id", "unknown"))
        source_id = raw_source_id.zfill(4) if raw_source_id.isdigit() else raw_source_id
        index["sources"].setdefault(
            source_id,
            {
                "source_id": source_id,
                "title": meta.get("title", "Untitled source"),
                "publisher": meta.get("publisher", "unknown"),
                "url": meta.get("url", ""),
                "source_type": meta.get("source_type", "unknown"),
                "local_path": meta.get("local_path", ""),
                "markdown_path": meta.get("markdown_path", ""),
            },
        )
        index["chunks"][chunk.get("id", "")] = {
            "chunk_id": chunk.get("id", ""),
            "source_id": source_id,
            "citation": f"[S{source_id}:C{chunk.get('id', '')}]",
            "section_path": meta.get("section_path", ""),
            "publish_score": chunk.get("publish_score", 0),
        }


def render_toc_markdown(plan: dict[str, Any]) -> str:
    lines = ["# Publish Table of Contents", ""]
    for index, section in enumerate(plan.get("sections", []), start=1):
        lines.append(f"{index}. {section.get('title', '')}")
    return "\n".join(lines).strip() + "\n"


def list_section_files(corpus: Path) -> list[dict[str, Any]]:
    sections_dir = publish_sections_dir(corpus)
    if not sections_dir.exists():
        return []
    rows = []
    for path in sorted(sections_dir.glob("*.md")):
        rows.append({"name": path.name, "path": str(path), "bytes": path.stat().st_size})
    return rows


def default_publish_state(corpus: Path) -> dict[str, Any]:
    return {
        "version": PUBLISH_VERSION,
        "corpus": {"id": corpus.name, "path": str(corpus)},
        "state": "idle",
        "section_count": 0,
        "completed_sections": [],
        "continuity_summary": "",
        "custom_prompts": {},
    }


def section_file_path(corpus: Path, index: int, section: dict[str, Any]) -> Path:
    return publish_sections_dir(corpus) / f"{index:03d}-{slugify(section.get('title', 'section'))}.md"


def read_visual_candidates(corpus: Path) -> list[dict[str, Any]]:
    line_cache: dict[Path, list[str]] = {}
    rows = []
    for row in atlas.read_jsonl(visual_candidates_path(corpus)):
        enriched = attach_visual_extraction_state(row, line_cache)
        if visual_candidate_allowed(enriched):
            rows.append(enriched)
    return rows


def default_visual_selections(corpus: Path) -> dict[str, Any]:
    return {
        "version": PUBLISH_VERSION,
        "corpus": {"id": corpus.name, "path": str(corpus)},
        "selections": {},
    }


def default_visual_plan(corpus: Path) -> dict[str, Any]:
    return {
        "version": PUBLISH_VERSION,
        "corpus": {"id": corpus.name, "path": str(corpus)},
        "placeholders": [],
    }


def visual_summary(candidates: list[dict[str, Any]], selections: dict[str, Any], plan: dict[str, Any]) -> dict[str, int]:
    selection_rows = selections.get("selections") or {}
    return {
        "candidate_count": len(candidates),
        "add_count": sum(1 for item in selection_rows.values() if item.get("status") == "add"),
        "maybe_count": sum(1 for item in selection_rows.values() if item.get("status") == "maybe"),
        "reject_count": sum(1 for item in selection_rows.values() if item.get("status") == "reject"),
        "placeholder_count": len(plan.get("placeholders") or []),
    }


def attach_visual_state(corpus: Path, candidates: list[dict[str, Any]], selections: dict[str, Any]) -> list[dict[str, Any]]:
    selection_rows = selections.get("selections") or {}
    rows = []
    for candidate in candidates:
        row = dict(candidate)
        selection = selection_rows.get(candidate.get("candidate_id"), {})
        row["selection"] = selection
        row["selection_status"] = selection.get("status", "none")
        row["image_url"] = visual_image_url(corpus, row)
        rows.append(row)
    return rows


def visual_image_url(corpus: Path, candidate: dict[str, Any]) -> str:
    if candidate.get("is_remote"):
        return str(candidate.get("asset_path") or "")
    if candidate.get("missing"):
        return ""
    candidate_id = str(candidate.get("candidate_id") or "")
    if not candidate_id:
        return ""
    return f"/api/atlas/{quote(corpus.name)}/publish/visuals/assets/{quote(candidate_id)}"


def extract_visual_candidates(corpus: Path) -> list[dict[str, Any]]:
    source_metadata = atlas.source_metadata_by_markdown(corpus)
    chunks = atlas.read_jsonl(corpus / "atlas" / "chunks.jsonl")
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for md_path in sorted((corpus / "markdown").glob("*.md")):
        source = source_metadata.get(str(md_path.resolve())) or source_metadata.get(md_path.name) or atlas.fallback_source_metadata(md_path)
        lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
        section_paths = markdown_line_section_paths(lines)
        for line_index, line in enumerate(lines):
            for match in re.finditer(r"!\[([^\]]*)\]\(([^)\n]+)\)", line):
                alt_text = clean_inline_text(match.group(1))
                original_link = normalize_markdown_image_link(match.group(2))
                if not original_link:
                    continue
                asset = resolve_visual_asset(corpus, md_path, original_link)
                if asset["is_remote"]:
                    continue
                dedupe_key = visual_dedupe_key(asset)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                nearby = nearby_visual_text(lines, line_index)
                extraction = visual_extraction_metadata(lines, line_index)
                dimensions = image_dimensions(asset.get("local_path_obj")) if asset.get("local_path_obj") else {}
                flags = visual_flags(original_link, alt_text, nearby, dimensions, asset.get("file_size", 0), asset["is_remote"])
                if extraction["status"] == "table_text_extracted":
                    flags.append("table_text_extracted")
                if should_skip_visual(flags):
                    continue
                candidate_id = "vis_" + hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()[:12]
                local_path = asset.get("local_path_obj")
                row = {
                    "candidate_id": candidate_id,
                    "asset_path": asset["asset_path"],
                    "original_link": original_link,
                    "local_path": str(local_path) if local_path else "",
                    "is_remote": asset["is_remote"],
                    "missing": asset["missing"],
                    "content_hash": asset.get("content_hash", ""),
                    "file_size": asset.get("file_size", 0),
                    "width": dimensions.get("width", 0),
                    "height": dimensions.get("height", 0),
                    "alt_text": alt_text,
                    "caption": visual_caption(alt_text, nearby),
                    "nearby_text": nearby[:900],
                    "extraction_status": extraction["status"],
                    "extraction_label": extraction["label"],
                    "has_picture_text": extraction["has_picture_text"],
                    "has_table_text": extraction["has_table_text"],
                    "markdown_path": str(md_path),
                    "markdown_line": line_index + 1,
                    "source_id": str(source.get("source_id", "")),
                    "title": source.get("title", md_path.stem),
                    "publisher": source.get("publisher", "unknown"),
                    "url": source.get("url", ""),
                    "source_type": source.get("source_type", "unknown"),
                    "section_path": " > ".join(section_paths.get(line_index, [])),
                    "chunk_id": visual_chunk_id(chunks, source, original_link, md_path),
                    "flags": flags,
                    "section_matches": [],
                    "primary_section_id": "",
                    "primary_section_title": "",
                    "visual_score": 0.0,
                }
                rows.append(row)
    return rows


def rank_visual_candidates(corpus: Path, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plan = read_json_object(publish_plan_path(corpus))
    plan_sections = plan.get("sections") or []
    try:
        finalized = finalized_compose_payload(corpus)
    except Exception:
        finalized = {}
    sections_by_topic = {section.get("topic_id"): section for section in finalized.get("subtopics", [])}
    try:
        chunks, selections = load_publish_units(corpus)
    except Exception:
        chunks, selections = [], {}
    section_context = []
    for plan_section in plan_sections:
        source_section = sections_by_topic.get(plan_section.get("source_topic_id"), {})
        ranked_chunks = cap_chunks(rank_chunks_for_section(source_section, chunks, selections), max_chunks=20, max_chars=120_000) if source_section else []
        section_context.append(
            {
                "section": plan_section,
                "terms": visual_section_terms(plan_section, source_section),
                "top_chunk_ids": {chunk.get("id") for chunk in ranked_chunks},
                "top_source_ids": {str(metadata(chunk).get("source_id", "")) for chunk in ranked_chunks},
            }
        )
    for candidate in candidates:
        matches = []
        candidate_text = visual_candidate_text(candidate)
        candidate_norm = normalize(candidate_text)
        for context in section_context:
            terms = context["terms"]
            tag_hits = sum(1 for term in terms["tags"] if term and term in candidate_norm)
            label_score = overlap_score(terms["section_tokens"], candidate_norm)
            tag_score = overlap_score(terms["tag_tokens"], candidate_norm)
            score = label_score * 14.0 + tag_score * 10.0 + tag_hits * 3.0
            if candidate.get("source_id") in context["top_source_ids"]:
                score += 2.0
            if candidate.get("chunk_id") in context["top_chunk_ids"]:
                score += 4.0
            if re.search(r"\b(figure|chart|map|diagram|table|framework|workflow|architecture)\b", candidate_norm):
                score += 1.5
            if candidate.get("flags"):
                score -= min(4.0, len(candidate["flags"]) * 1.5)
            if score <= 0:
                continue
            section = context["section"]
            matches.append(
                {
                    "section_id": section.get("section_id", ""),
                    "title": section.get("title", ""),
                    "score": round(score, 4),
                    "reasons": {
                        "tag_hits": tag_hits,
                        "label_score": round(label_score, 4),
                        "tag_score": round(tag_score, 4),
                        "source_match": candidate.get("source_id") in context["top_source_ids"],
                        "chunk_match": candidate.get("chunk_id") in context["top_chunk_ids"],
                    },
                }
            )
        matches.sort(key=lambda item: item["score"], reverse=True)
        candidate["section_matches"] = matches[:5]
        if matches:
            candidate["primary_section_id"] = matches[0]["section_id"]
            candidate["primary_section_title"] = matches[0]["title"]
            candidate["visual_score"] = matches[0]["score"]
        else:
            candidate["visual_score"] = visual_generic_score(candidate)
    return sorted(
        candidates,
        key=lambda item: (
            -float(item.get("visual_score") or 0),
            item.get("source_id", ""),
            item.get("markdown_line", 0),
            item.get("candidate_id", ""),
        ),
    )


def visual_section_terms(plan_section: dict[str, Any], source_section: dict[str, Any]) -> dict[str, Any]:
    tags = [tag.get("label", "") for tag in source_section.get("tags", []) if tag.get("selected", True)]
    values = [
        plan_section.get("title", ""),
        " ".join(plan_section.get("source_path", []) or source_section.get("path", [])),
    ] + tags
    return compile_terms(values)


def visual_candidate_text(candidate: dict[str, Any]) -> str:
    return " ".join(
        str(candidate.get(key, ""))
        for key in ("title", "section_path", "alt_text", "caption", "nearby_text", "publisher")
    )


def visual_generic_score(candidate: dict[str, Any]) -> float:
    text = normalize(visual_candidate_text(candidate))
    score = 0.0
    if re.search(r"\b(figure|chart|map|diagram|table|framework|workflow|architecture)\b", text):
        score += 2.0
    if candidate.get("caption"):
        score += 1.0
    if candidate.get("width") and candidate.get("height"):
        score += min(2.0, (candidate["width"] * candidate["height"]) / 250_000)
    return round(score, 4)


def ensure_visual_plan(corpus: Path) -> dict[str, Any]:
    candidates = {row.get("candidate_id"): row for row in read_visual_candidates(corpus)}
    selections = read_json_object(visual_selections_path(corpus)) or default_visual_selections(corpus)
    plan = read_json_object(publish_plan_path(corpus))
    plan_sections = plan.get("sections") or []
    section_order = {section.get("section_id"): index for index, section in enumerate(plan_sections, start=1)}
    selected = []
    for candidate_id, selection in (selections.get("selections") or {}).items():
        if selection.get("status") != "add" or candidate_id not in candidates:
            continue
        candidate = candidates[candidate_id]
        section_id = selection.get("section_id") or candidate.get("primary_section_id") or (plan_sections[0].get("section_id") if plan_sections else "")
        selected.append((section_order.get(section_id, 999), section_id, candidate_id, selection, candidate))
    selected.sort(key=lambda item: (item[0], item[2]))
    ordinals: Counter[str] = Counter()
    placeholders = []
    for section_number, section_id, candidate_id, selection, candidate in selected:
        ordinals[section_id] += 1
        placeholders.append(
            {
                "placeholder_id": f"fig_{section_number if section_id else 0:03d}_{ordinals[section_id]:02d}",
                "section_id": section_id,
                "visual_id": candidate_id,
                "asset_path": candidate.get("asset_path", ""),
                "source_id": candidate.get("source_id", ""),
                "chunk_id": candidate.get("chunk_id", ""),
                "caption": selection.get("caption") or candidate.get("caption") or candidate.get("alt_text") or "Visual evidence",
                "placement": selection.get("placement") or "after_first_matching_citation",
            }
        )
    out = {
        "version": PUBLISH_VERSION,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"id": corpus.name, "path": str(corpus)},
        "placeholders": placeholders,
    }
    atomic_write_json(visual_plan_path(corpus), out)
    return out


def normalize_visual_selections(value: dict[str, Any]) -> dict[str, Any]:
    rows = {}
    for key, raw in value.items():
        item = normalize_visual_selection({**(raw if isinstance(raw, dict) else {}), "candidate_id": key})
        if item:
            rows[item["candidate_id"]] = item
    return rows


def normalize_visual_selection(value: dict[str, Any]) -> dict[str, Any]:
    candidate_id = str(value.get("candidate_id", "")).strip()
    status = str(value.get("status", "")).strip()
    if not candidate_id or status not in {"add", "maybe", "reject"}:
        return {}
    out = {
        "candidate_id": candidate_id,
        "status": status,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    for key in ("section_id", "role", "caption", "placement"):
        text = str(value.get(key, "")).strip()
        if text:
            out[key] = text[:500]
    return out


def markdown_line_section_paths(lines: list[str]) -> dict[int, list[str]]:
    current: list[str] = []
    result: dict[int, list[str]] = {}
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            level = len(match.group(1))
            current = current[: level - 1] + [clean_inline_text(match.group(2))]
        result[index] = list(current)
    return result


def normalize_markdown_image_link(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("<") and ">" in text:
        return text[1 : text.index(">")].strip()
    text = text.strip("<>")
    quoted = re.match(r"^([^\"']+?)\s+[\"'].+[\"']$", text)
    if quoted:
        text = quoted.group(1)
    return text.strip()


def resolve_visual_asset(corpus: Path, md_path: Path, link: str) -> dict[str, Any]:
    if re.match(r"^https?://", link, flags=re.I):
        return {"asset_path": link, "is_remote": True, "missing": False}
    if re.match(r"^(?:data|file):", link, flags=re.I):
        return {"asset_path": link, "is_remote": True, "missing": False}
    decoded = re.sub(r"%20", " ", link)
    raw_path = Path(decoded)
    local_path = raw_path if raw_path.is_absolute() else (md_path.parent / raw_path)
    try:
        local_path = local_path.resolve()
    except Exception:
        local_path = md_path.parent / raw_path
    missing = not local_path.exists()
    return {
        "asset_path": relative_asset_path(corpus, local_path) if not missing else link,
        "local_path_obj": local_path,
        "is_remote": False,
        "missing": missing,
        "content_hash": file_hash(local_path) if not missing else "",
        "file_size": local_path.stat().st_size if not missing else 0,
    }


def relative_asset_path(corpus: Path, path: Path) -> str:
    try:
        return str(path.relative_to(corpus)).replace(" ", "%20")
    except ValueError:
        return str(path)


def visual_dedupe_key(asset: dict[str, Any]) -> str:
    if asset.get("content_hash"):
        return f"hash:{asset['content_hash']}"
    return f"link:{normalize(asset.get('asset_path', ''))}"


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_dimensions(path: Path | None) -> dict[str, int]:
    if not path or not path.exists():
        return {}
    try:
        data = path.read_bytes()[:65536]
    except Exception:
        return {}
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return {"width": int.from_bytes(data[16:20], "big"), "height": int.from_bytes(data[20:24], "big")}
    if data.startswith(b"\xff\xd8"):
        return jpeg_dimensions(data)
    return {}


def jpeg_dimensions(data: bytes) -> dict[str, int]:
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9, 0x01}:
            continue
        if index + 2 > len(data):
            break
        length = int.from_bytes(data[index : index + 2], "big")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF} and index + 7 < len(data):
            return {"height": int.from_bytes(data[index + 3 : index + 5], "big"), "width": int.from_bytes(data[index + 5 : index + 7], "big")}
        index += max(length, 2)
    return {}


def nearby_visual_text(lines: list[str], line_index: int) -> str:
    start = max(0, line_index - 4)
    end = min(len(lines), line_index + 5)
    text = "\n".join(lines[start:end])
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    return clean_inline_text(text)


def clean_inline_text(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"[*_`#>|]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def visual_caption(alt_text: str, nearby: str) -> str:
    if alt_text and normalize(alt_text) not in {"image", "picture", "figure"}:
        return alt_text[:240]
    match = re.search(r"\b(?:figure|fig\.?|table|chart|map|diagram)\s*[\dA-Za-z.-]*[:.\-\s]+([^.\n]{12,220})", nearby, flags=re.I)
    if match:
        return clean_inline_text(match.group(0))[:260]
    return clean_inline_text(nearby)[:220]


def visual_flags(link: str, alt_text: str, nearby: str, dimensions: dict[str, int], file_size: int = 0, is_remote: bool = False) -> list[str]:
    flags = []
    text = normalize(f"{link} {alt_text} {nearby}")
    if not is_remote and file_size and file_size <= MAX_SMALL_VISUAL_BYTES:
        flags.append("small_file")
    if dimensions.get("width") and dimensions.get("height") and (dimensions["width"] < MIN_VISUAL_SIDE or dimensions["height"] < MIN_VISUAL_SIDE):
        flags.append("tiny_image")
    if not is_remote and file_size and file_size < MAX_SQUARE_VISUAL_BYTES and near_square_visual(dimensions):
        flags.append("small_square")
    if re.search(r"\b(logo|icon|avatar|favicon|spinner|credit logo|social|person1|person2)\b", text):
        flags.append("decorative_name")
    return flags


def should_skip_visual(flags: list[str]) -> bool:
    return bool({"small_file", "tiny_image", "small_square", "decorative_name", "table_text_extracted"} & set(flags))


def visual_candidate_allowed(candidate: dict[str, Any]) -> bool:
    if candidate.get("is_remote"):
        return False
    flags = set(candidate.get("flags") or [])
    if {"small_file", "tiny_image", "small_square", "decorative_name", "table_text_extracted"} & flags:
        return False
    if candidate.get("extraction_status") == "table_text_extracted":
        return False
    file_size = int(candidate.get("file_size") or 0)
    if candidate.get("width") and candidate.get("height"):
        if int(candidate.get("width") or 0) < MIN_VISUAL_SIDE or int(candidate.get("height") or 0) < MIN_VISUAL_SIDE:
            return False
        if not candidate.get("is_remote") and 0 < file_size < MAX_SQUARE_VISUAL_BYTES and near_square_visual(candidate):
            return False
    if not candidate.get("is_remote") and file_size and file_size <= MAX_SMALL_VISUAL_BYTES:
        return False
    return True


def near_square_visual(dimensions: dict[str, int]) -> bool:
    width = int(dimensions.get("width") or 0)
    height = int(dimensions.get("height") or 0)
    if not width or not height:
        return False
    difference = abs(width - height)
    # The 5px floor includes examples like 235x240 while still blocking near-square thumbnails.
    return difference <= max(5, round(max(width, height) * 0.02))


def attach_visual_extraction_state(candidate: dict[str, Any], line_cache: dict[Path, list[str]] | None = None) -> dict[str, Any]:
    if candidate.get("extraction_status"):
        return candidate
    row = dict(candidate)
    md_path = Path(str(row.get("markdown_path", "")))
    line_index = max(0, int(row.get("markdown_line") or 1) - 1)
    lines: list[str] = []
    if md_path.exists():
        if line_cache is not None:
            if md_path not in line_cache:
                line_cache[md_path] = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
            lines = line_cache[md_path]
        else:
            lines = md_path.read_text(encoding="utf-8", errors="replace").splitlines()
    extraction = visual_extraction_metadata(lines, line_index)
    row.update(
        {
            "extraction_status": extraction["status"],
            "extraction_label": extraction["label"],
            "has_picture_text": extraction["has_picture_text"],
            "has_table_text": extraction["has_table_text"],
        }
    )
    if extraction["status"] == "table_text_extracted":
        flags = list(row.get("flags") or [])
        if "table_text_extracted" not in flags:
            flags.append("table_text_extracted")
        row["flags"] = flags
    return row


def visual_extraction_metadata(lines: list[str], line_index: int) -> dict[str, Any]:
    window = visual_local_extraction_block(lines, line_index)
    has_picture_text = bool(re.search(r"Start of picture text|End of picture text", window, re.I))
    has_markdown_table = markdown_table_in_window(window.splitlines())
    has_table_heading = bool(re.search(r"\b(?:Table|Tab\.)\s+[A-Za-z0-9.:-]+", window, re.I))
    has_table_text = has_markdown_table or (has_picture_text and has_table_heading)
    if has_table_text:
        status = "table_text_extracted"
        label = "Likely table text extracted"
    elif has_picture_text:
        status = "text_extracted"
        label = "Text extracted"
    else:
        status = "no_extracted_text"
        label = "No extracted text found"
    return {
        "status": status,
        "label": label,
        "has_picture_text": has_picture_text,
        "has_table_text": has_table_text,
    }


def visual_local_extraction_block(lines: list[str], line_index: int) -> str:
    if not lines:
        return ""
    before = lines[max(0, line_index - 2) : line_index]
    after = []
    for index in range(line_index + 1, min(len(lines), line_index + 14)):
        line = lines[index]
        if index > line_index + 1 and re.search(r"!\[[^\]]*\]\([^)]+\)", line):
            break
        if index > line_index + 1 and re.match(r"^#{1,6}\s+", line):
            break
        after.append(line)
    return "\n".join(before + [lines[line_index]] + after)


def markdown_table_in_window(lines: list[str]) -> bool:
    for index, line in enumerate(lines):
        if not re.match(r"^\s*\|.+\|\s*$", line):
            continue
        neighbors = lines[max(0, index - 1) : min(len(lines), index + 2)]
        if any(re.match(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", item) for item in neighbors):
            return True
        if line.count("|") >= 3:
            return True
    return False


def visual_chunk_id(chunks: list[dict[str, Any]], source: dict[str, Any], link: str, md_path: Path) -> str:
    source_id = str(source.get("source_id", ""))
    link_name = Path(link).name.replace("%20", " ")
    for chunk in chunks:
        meta = metadata(chunk)
        if str(meta.get("source_id", "")) != source_id:
            continue
        text = chunk.get("text", "")
        if link in text or link_name in text:
            return str(chunk.get("id", ""))
    for chunk in chunks:
        meta = metadata(chunk)
        if str(meta.get("source_id", "")) == source_id and Path(str(meta.get("markdown_path", ""))).name == md_path.name:
            return str(chunk.get("id", ""))
    return ""


def visual_asset_path(corpus: str, candidate_id: str) -> Path | None:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    for candidate in read_visual_candidates(corpus_path):
        if candidate.get("candidate_id") != candidate_id or candidate.get("is_remote") or candidate.get("missing"):
            continue
        path = Path(candidate.get("local_path", ""))
        try:
            resolved = path.resolve()
            resolved.relative_to(corpus_path.resolve())
            if resolved.exists() and resolved.is_file():
                return resolved
        except Exception:
            return None
    return None


def merge_custom_prompts(corpus: Path, custom_prompts: dict[str, str]) -> None:
    if not custom_prompts:
        return
    plan = read_json_object(publish_plan_path(corpus))
    merged = {**plan.get("custom_prompts", {}), **normalize_custom_prompts(custom_prompts)}
    plan["custom_prompts"] = merged
    atomic_write_json(publish_plan_path(corpus), plan)
    state = read_json_object(publish_state_path(corpus)) or default_publish_state(corpus)
    state["custom_prompts"] = merged
    atomic_write_json(publish_state_path(corpus), state)


def normalize_custom_prompts(value: dict[str, str]) -> dict[str, str]:
    return {str(key): str(text).strip() for key, text in value.items() if str(key).strip() and str(text).strip()}


def compile_terms(values: list[str]) -> dict[str, Any]:
    section_values = values[:2]
    tag_values = values[2:]
    return {
        "section_tokens": set(normalize(" ".join(section_values)).split()),
        "tag_tokens": set(normalize(" ".join(tag_values)).split()),
        "tags": [normalize(value) for value in tag_values if normalize(value)],
    }


def overlap_score(tokens: set[str], text_norm: str) -> float:
    if not tokens or not text_norm:
        return 0.0
    hay = set(text_norm.split())
    useful = {token for token in tokens if len(token) > 2}
    return len(useful & hay) / max(1, len(useful))


def chunk_quality_score(text: str) -> float:
    words = len(re.findall(r"\b[\w'-]+\b", text))
    if words < 40:
        return -2.0
    score = min(4.0, words / 250.0)
    if "before you continue" in text.lower() or "verify you are human" in text.lower():
        score -= 8.0
    return score


def clean_chunk_text(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text or "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_title(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:120] or "Untitled Section"


def normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def metadata(chunk: dict[str, Any]) -> dict[str, Any]:
    return chunk.get("metadata") or {}


def strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", "", text)
    text = re.sub(r"#+\s*", "", text)
    return re.sub(r"\s+", " ", text).strip()


def paper_title(corpus: Path) -> str:
    run = atlas.read_json(corpus / "run.json")
    topic = run.get("topic") or corpus.name.removesuffix("_Corpus")
    return clean_title(topic)


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}
