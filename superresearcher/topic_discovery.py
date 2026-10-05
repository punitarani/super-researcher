from __future__ import annotations

import argparse
import json
import re
import threading
import time
import traceback
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from . import atlas
from .config import DEFAULT_STORAGE_ROOT, atomic_write_json, atomic_write_text
from .llm import client_for, require_ready_agent


GENERIC_LABELS = {
    "extracted text",  # "Extracted PDF Text": ingest's own label for plain PDF text, not a document heading
    "abstract",
    "acknowledgment",
    "acknowledgments",
    "acknowledgements",
    "appendix",
    "appendices",
    "bibliography",
    "conclusion",
    "conclusions",
    "contents",
    "figure",
    "figures",
    "index",
    "introduction",
    "list of figures",
    "list of tables",
    "references",
    "table",
    "table of contents",
    "tables",
}

STOPWORDS = {
    "about",
    "and",
    "are",
    "com",
    "data",
    "doi",
    "edu",
    "for",
    "from",
    "gov",
    "http",
    "https",
    "into",
    "jpg",
    "may",
    "not",
    "org",
    "pdf",
    "png",
    "research",
    "study",
    "that",
    "the",
    "this",
    "with",
    "www",
}

TOPIC_DISCOVERY_JOBS: dict[str, "TopicDiscoveryJob"] = {}


class TopicDiscoveryJob:
    def __init__(self, corpus: Path, force: bool = False) -> None:
        self.corpus = corpus.resolve()
        self.force = force
        self.job_id = f"topics-{self.corpus.name}-{int(time.time())}"
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
        TOPIC_DISCOVERY_JOBS[self.job_id] = self
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
            if topic_tree_path(self.corpus).exists() and not self.force:
                payload = get_topic_payload(str(self.corpus))
                self.event(
                    "Using existing topic tree",
                    100,
                    topic_count=count_topic_nodes(payload.get("topics", [])),
                    cached=1,
                )
            else:
                result = run_topic_discovery(corpus=str(self.corpus), progress=self.event)
                summary = result.get("summary", {})
                self.event(
                    "Topic discovery complete",
                    100,
                    unit_count=summary.get("unit_count", 0),
                    deduped_subtopic_count=summary.get("deduped_subtopic_count", 0),
                    cached=0,
                )
            with self._lock:
                self.status["state"] = "completed"
                self.status["stage"] = "Ready"
                self.status["progress"] = 100
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")
        except Exception as exc:
            log_path = self.corpus / "logs" / "topic-discovery-error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
            with self._lock:
                self.status["state"] = "failed"
                self.status["error"] = str(exc)
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")


def start_topic_discovery_job(corpus_id_or_path: str, force: bool = False) -> TopicDiscoveryJob:
    corpus = resolve_topic_corpus(corpus_id_or_path)
    for job in TOPIC_DISCOVERY_JOBS.values():
        snap = job.snapshot()
        if snap["corpus_path"] == str(corpus) and snap["state"] in {"queued", "running"}:
            return job
    if force or not topic_tree_path(corpus).exists():  # a cached topic tree needs no agent
        require_ready_agent()
    job = TopicDiscoveryJob(corpus, force=force)
    job.start()
    return job


def get_topic_discovery_job(job_id: str) -> TopicDiscoveryJob | None:
    return TOPIC_DISCOVERY_JOBS.get(job_id)


def resolve_topic_corpus(corpus: str = "latest") -> Path:
    if corpus == "latest":
        latest = atlas.latest_corpus_id(DEFAULT_STORAGE_ROOT)
        if not latest:
            raise ValueError("No corpus folders found under research_runs.")
        return atlas.resolve_corpus(latest)
    candidate = Path(corpus).expanduser()
    if candidate.is_absolute():
        candidate = candidate.resolve()
        if not candidate.exists() or not candidate.is_dir() or not candidate.name.endswith("_Corpus"):
            raise ValueError("Corpus folder not found.")
        return candidate
    return atlas.resolve_corpus(corpus)


def topic_tree_path(corpus: Path) -> Path:
    return corpus / "atlas" / "topics" / "topic_tree.md"


def curated_topic_json_path(corpus: Path) -> Path:
    return corpus / "atlas" / "topics" / "curated_topic_tree.json"


def curated_topic_md_path(corpus: Path) -> Path:
    return corpus / "atlas" / "topics" / "curated_topic_tree.md"


def topic_summary_path(corpus: Path) -> Path:
    return corpus / "atlas" / "topics" / "topic_discovery_summary.md"


def deduped_subtopics_path(corpus: Path) -> Path:
    return corpus / "atlas" / "topics" / "deduped_subtopics.json"


def get_topic_payload(corpus: str = "latest") -> dict[str, Any]:
    corpus_path = resolve_topic_corpus(corpus)
    raw_path = topic_tree_path(corpus_path)
    curated_path = curated_topic_json_path(corpus_path)
    raw_markdown = raw_path.read_text(encoding="utf-8", errors="replace") if raw_path.exists() else ""
    curated = read_curated_topic_tree(corpus_path)
    topics = curated.get("topics") if curated else parse_topic_tree_markdown(raw_markdown)
    return {
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "raw": {
            "exists": raw_path.exists(),
            "path": str(raw_path),
            "markdown": raw_markdown,
        },
        "curated": {
            "exists": bool(curated),
            "path": str(curated_path),
            "markdown_path": str(curated_topic_md_path(corpus_path)),
            "tree": curated,
        },
        "topics": topics,
        "summary": read_topic_summary(corpus_path),
        "deduped_subtopics": read_json_list(deduped_subtopics_path(corpus_path)),
        "source": "curated" if curated else ("raw" if raw_path.exists() else "none"),
    }


def get_curated_topic_payload(corpus: str = "latest") -> dict[str, Any]:
    corpus_path = resolve_topic_corpus(corpus)
    curated = read_curated_topic_tree(corpus_path)
    md_path = curated_topic_md_path(corpus_path)
    return {
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "exists": bool(curated),
        "path": str(curated_topic_json_path(corpus_path)),
        "markdown_path": str(md_path),
        "markdown": md_path.read_text(encoding="utf-8", errors="replace") if md_path.exists() else "",
        "tree": curated,
    }


def save_curated_topic_tree(corpus: str, payload: dict[str, Any]) -> dict[str, Any]:
    corpus_path = resolve_topic_corpus(corpus)
    raw_path = topic_tree_path(corpus_path)
    if not raw_path.exists():
        raise RuntimeError("Topic discovery has not produced topic_tree.md yet.")
    topics = assign_topic_ids(normalize_topic_nodes(payload.get("topics", [])))
    out = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus_id": corpus_path.name,
        "source_topic_tree_md_path": str(raw_path),
        "topics": topics,
    }
    out_dir = corpus_path / "atlas" / "topics"
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(curated_topic_json_path(corpus_path), out)
    atomic_write_text(curated_topic_md_path(corpus_path), render_curated_topic_markdown(topics))
    return get_curated_topic_payload(str(corpus_path))


def read_curated_topic_tree(corpus: Path) -> dict[str, Any] | None:
    path = curated_topic_json_path(corpus)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    topics = payload.get("topics")
    if not isinstance(topics, list):
        return None
    payload["topics"] = assign_topic_ids(normalize_topic_nodes(topics))
    return payload


def read_topic_summary(corpus: Path) -> dict[str, Any]:
    path = topic_summary_path(corpus)
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        rows[key.strip().lower().replace(" ", "_")] = value.strip()
    return rows


def read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload if isinstance(payload, list) else []


def parse_topic_tree_markdown(markdown: str) -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    stack: list[tuple[int, dict[str, Any]]] = []
    for raw in markdown.splitlines():
        match = re.match(r"^(\s*)[-*]\s+(.+?)\s*$", raw)
        if not match:
            continue
        indent = len(match.group(1).replace("\t", "  "))
        level = indent // 2
        label = clean_topic_label(match.group(2))
        if not label:
            continue
        node = {"id": "", "label": label, "selected": True, "children": []}
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return assign_topic_ids(roots)


def normalize_topic_nodes(nodes: Any) -> list[dict[str, Any]]:
    if not isinstance(nodes, list):
        return []
    normalized = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        label = clean_topic_label(node.get("label", ""))
        if not label:
            continue
        normalized.append(
            {
                "id": str(node.get("id") or ""),
                "label": label,
                "selected": bool(node.get("selected", True)),
                "children": normalize_topic_nodes(node.get("children", [])),
            }
        )
    return normalized


def assign_topic_ids(nodes: list[dict[str, Any]], prefix: str = "topic") -> list[dict[str, Any]]:
    for index, node in enumerate(nodes, start=1):
        node["id"] = f"{prefix}-{index}"
        assign_topic_ids(node.get("children", []), node["id"])
    return nodes


def render_curated_topic_markdown(topics: list[dict[str, Any]]) -> str:
    lines: list[str] = []

    def walk(nodes: list[dict[str, Any]], depth: int) -> None:
        for node in nodes:
            if not node.get("selected", True):
                continue
            lines.append(f"{'  ' * depth}- {clean_topic_label(node.get('label', ''))}")
            walk(node.get("children", []), depth + 1)

    walk(normalize_topic_nodes(topics), 0)
    return "\n".join(lines).strip() + ("\n" if lines else "")


def count_topic_nodes(nodes: list[dict[str, Any]]) -> int:
    return sum(1 + count_topic_nodes(node.get("children", [])) for node in nodes)


def clean_topic_label(value: Any) -> str:
    text = clean_display_text(value)
    text = re.sub(r"^\s*[-*]+\s+", "", text)
    return text.strip()


def load_topic_units(
    corpus: Path,
    include_maybe: bool = True,
    include_unmarked: bool = True,
    include_rejected: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    chunks_path = corpus / "atlas" / "chunks.jsonl"
    if not chunks_path.exists():
        raise RuntimeError("Atlas chunks not found. Build Atlas first.")
    chunks = atlas.read_jsonl(chunks_path)
    selections = {
        row["chunk_id"]: row
        for row in atlas.read_jsonl(corpus / "atlas" / "selections.jsonl")
        if row.get("chunk_id")
    }
    counts = Counter()
    units: list[dict[str, Any]] = []
    for chunk in chunks:
        chunk_id = chunk.get("id")
        selection = selections.get(chunk_id, {})
        status = selection.get("status") or "unmarked"
        counts[f"seen_{status}"] += 1
        if status == "reject" and not include_rejected:
            counts["excluded_reject"] += 1
            continue
        if status == "maybe" and not include_maybe:
            counts["excluded_maybe"] += 1
            continue
        if status == "unmarked" and not include_unmarked:
            counts["excluded_unmarked"] += 1
            continue
        units.append(
            {
                "id": chunk_id,
                "text": chunk.get("text", ""),
                "metadata": chunk.get("metadata") or {},
                "selection_status": status,
                "selection_notes": selection.get("notes", ""),
            }
        )
        counts[f"included_{status}"] += 1
    counts["total_chunks"] = len(chunks)
    counts["included_chunks"] = len(units)
    return units, dict(counts)


def extract_toc_entries(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    entries = []
    for unit in units:
        if not is_toc_unit(unit):
            continue
        section_path = metadata(unit).get("section_path", "")
        for label in extract_toc_labels(unit.get("text", "")):
            entries.append(candidate_record(label, "toc", unit, section_path))
    return entries


def extract_heading_candidates(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    for unit in units:
        for path in iter_section_paths(unit):
            for label in path_labels(path):
                candidates.append(candidate_record(label, "heading", unit, path))
    return candidates


def combine_candidates(toc_entries: list[dict[str, Any]], heading_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return toc_entries + heading_entries


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for candidate in candidates:
        if not candidate.get("normalized_label"):
            continue
        match = next((group for group in groups if should_merge(group, candidate)), None)
        if match:
            merge_candidate(match, candidate)
        else:
            groups.append(new_group(candidate))
    for group in groups:
        finalize_group(group)
    return sorted(groups, key=candidate_sort_key, reverse=True)


def run_topic_discovery(
    corpus: str = "latest",
    max_chunks: int | None = None,
    include_maybe: bool = True,
    include_unmarked: bool = True,
    include_rejected: bool = False,
    llm: Any | None = None,
    progress=None,
) -> dict[str, Any]:
    corpus_path = resolve_topic_corpus(corpus)
    if progress:
        progress("Loading Atlas chunks", 8)
    units, filter_counts = load_topic_units(
        corpus_path,
        include_maybe=include_maybe,
        include_unmarked=include_unmarked,
        include_rejected=include_rejected,
    )
    if max_chunks is not None:
        units = units[:max_chunks]
        filter_counts["limited_to_chunks"] = len(units)
    if not units:
        raise RuntimeError("No topic units available after curation filters.")

    if progress:
        progress("Mining topic candidates", 28, unit_count=len(units))
    toc_entries = extract_toc_entries(units)
    heading_entries = extract_heading_candidates(units)
    candidates = combine_candidates(toc_entries, heading_entries)
    if progress:
        progress("Deduping subtopics", 45, candidate_count=len(candidates))
    deduped = dedupe_candidates(candidates)
    if progress:
        progress("Preparing LLM synthesis", 60, deduped_subtopic_count=len(deduped))
    prompt = build_topic_prompt(deduped)
    result = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "settings": {
            "method": "toc_heading_subtopic_mining",
            "max_chunks": max_chunks,
        },
        "filters": {
            "include_maybe": include_maybe,
            "include_unmarked": include_unmarked,
            "include_rejected": include_rejected,
            "counts": filter_counts,
        },
        "summary": {
            "unit_count": len(units),
            "toc_entry_count": len(toc_entries),
            "heading_candidate_count": len(heading_entries),
            "candidate_count": len(candidates),
            "deduped_subtopic_count": len(deduped),
            "llm_status": "not_started",
        },
        "toc_entries": toc_entries,
        "topic_candidates": candidates,
        "deduped_subtopics": deduped,
        "llm_prompt": prompt,
    }
    if progress:
        progress("Writing mined topic artifacts", 68)
    write_topic_outputs(corpus_path, result)
    if not deduped:
        raise RuntimeError(
            "No section headings or table-of-contents entries were found in this corpus, so there's nothing to build "
            "topics from. Its PDFs were saved as plain text: run Post-process to rebuild their Markdown with headings "
            "(it needs the PDF extras from requirements-atlas.txt), then Build Atlas and Discover topics again."
        )

    llm = llm or client_for(corpus_path)
    try:
        if progress:
            progress("Synthesizing topic tree", 76)
        topic_tree = llm.text_call(prompt, step="Topic discovery: outline").strip()
        if not topic_tree:
            raise RuntimeError("LLM returned empty Markdown.")
    except Exception as exc:
        result["summary"]["llm_status"] = "failed"
        result["summary"]["llm_error"] = str(exc)
        write_topic_outputs(corpus_path, result)
        raise RuntimeError(f"Topic synthesis failed: {exc} Mined and deduped artifacts were written to {corpus_path / 'atlas' / 'topics'}.") from exc

    result["summary"]["llm_status"] = "success"
    result["topic_tree_markdown"] = topic_tree
    if progress:
        progress("Writing topic tree", 92)
    write_topic_outputs(corpus_path, result, topic_tree)
    return result


def write_topic_outputs(corpus: Path, result: dict[str, Any], topic_tree_markdown: str | None = None) -> dict[str, str]:
    out_dir = corpus / "atlas" / "topics"
    out_dir.mkdir(parents=True, exist_ok=True)
    remove_obsolete_outputs(out_dir)
    paths = {
        "toc_entries_json": out_dir / "toc_entries.json",
        "toc_entries_md": out_dir / "toc_entries.md",
        "topic_candidates_json": out_dir / "topic_candidates.json",
        "topic_candidates_md": out_dir / "topic_candidates.md",
        "deduped_subtopics_json": out_dir / "deduped_subtopics.json",
        "deduped_subtopics_md": out_dir / "deduped_subtopics.md",
        "topic_tree_md": out_dir / "topic_tree.md",
        "topic_discovery_summary": out_dir / "topic_discovery_summary.md",
        "llm_prompt": out_dir / "llm_prompt.md",
    }
    atomic_write_json(paths["toc_entries_json"], result.get("toc_entries", []))
    atomic_write_text(paths["toc_entries_md"], render_candidates_markdown("Table of Contents Entries", result.get("toc_entries", [])))
    atomic_write_json(paths["topic_candidates_json"], result.get("topic_candidates", []))
    atomic_write_text(paths["topic_candidates_md"], render_candidates_markdown("Topic Candidates", result.get("topic_candidates", [])))
    atomic_write_json(paths["deduped_subtopics_json"], result.get("deduped_subtopics", []))
    atomic_write_text(paths["deduped_subtopics_md"], render_candidates_markdown("Deduped Subtopics", result.get("deduped_subtopics", [])))
    atomic_write_text(paths["topic_discovery_summary"], render_summary_markdown(result))
    atomic_write_text(paths["llm_prompt"], result.get("llm_prompt", ""))
    if topic_tree_markdown is not None:
        atomic_write_text(paths["topic_tree_md"], topic_tree_markdown.strip() + "\n")
    elif paths["topic_tree_md"].exists():
        paths["topic_tree_md"].unlink()
    return {key: str(path) for key, path in paths.items()}


def remove_obsolete_outputs(out_dir: Path) -> None:
    for name in (
        "dbscan_noise.md",
        "dbscan_topics.json",
        "dbscan_topics.md",
        "topic_batch_proposals.json",
        "topic_embeddings.meta.json",
        "topic_embeddings.npy",
        "topic_tree.json",
    ):
        path = out_dir / name
        if path.exists():
            path.unlink()


def build_topic_prompt(deduped: list[dict[str, Any]]) -> str:
    compact = [
        {
            "label": item["label"],
            "aliases": item.get("aliases", [])[:8],
            "origin": item.get("origin"),
            "origins": item.get("origins", []),
            "occurrence_count": item.get("occurrence_count", 0),
            "source_count": item.get("source_count", 0),
            "source_titles": item.get("source_titles", [])[:5],
            "section_paths": item.get("section_paths", [])[:5],
            "curation_mix": item.get("curation_mix", {}),
        }
        for item in deduped
    ]
    return f"""You are organizing mined corpus headings and table-of-contents entries into a report outline.

Use only the deduped candidate list below. Do not use outside knowledge.
Produce Markdown only. No preface, no explanation, no JSON.

Output format:
- Major topic
  - Subtopic
  - Subtopic
- Major topic
  - Subtopic

Rules:
- Merge duplicate or near-duplicate candidates.
- Drop document artifacts, page labels, source-title-only labels, and broken OCR fragments.
- Prefer report-ready wording over raw document wording.
- Keep the outline concise but preserve meaningful niche subtopics.

Deduped candidates:
{json.dumps(compact, ensure_ascii=False)}
"""


def is_toc_unit(unit: dict[str, Any]) -> bool:
    meta = metadata(unit)
    haystack = " ".join([str(meta.get("section_path", "")), unit.get("text", "")[:1200]]).lower()
    return "table of contents" in haystack or re.search(r"\bcontents\b", haystack) is not None


def extract_toc_labels(text: str) -> list[str]:
    cleaned = remove_markdown_images(text)
    labels = []
    lines = re.split(r"[\n\r]+", cleaned)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        fragments = [line]
        if "|" in line:
            cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            fragments.extend(cells)
            for start in range(len(cells)):
                for span in (2, 3):
                    joined = " ".join(cells[start : start + span])
                    if len(joined) <= 160:
                        fragments.append(joined)
        else:
            fragments.extend(part.strip() for part in re.split(r"\s{3,}", line) if part.strip())
        for fragment in fragments:
            label = clean_candidate_label(fragment, strip_trailing_page=True)
            if is_meaningful_label(label):
                labels.append(label)
    return dedupe_preserve(labels)


def iter_section_paths(unit: dict[str, Any]) -> list[str]:
    meta = metadata(unit)
    paths = []
    if meta.get("section_path"):
        paths.append(str(meta["section_path"]))
    for path in meta.get("section_paths") or []:
        if isinstance(path, list):
            paths.append(" > ".join(str(part) for part in path if part))
        elif path:
            paths.append(str(path))
    return dedupe_preserve(paths)


def path_labels(path: str) -> list[str]:
    labels = []
    for segment in path.split(">"):
        label = clean_candidate_label(segment)
        if is_meaningful_label(label):
            labels.append(label)
    return dedupe_preserve(labels)


def candidate_record(label: str, origin: str, unit: dict[str, Any], section_path: str) -> dict[str, Any]:
    label = clean_candidate_label(label)
    meta = metadata(unit)
    source_id = str(meta.get("source_id", ""))
    status = unit.get("selection_status", "unmarked")
    return {
        "label": label,
        "normalized_label": normalize_label(label),
        "origin": origin,
        "origins": [origin],
        "source_title": clean_display_text(meta.get("title", "Untitled source")),
        "publisher": clean_display_text(meta.get("publisher", "unknown")),
        "source_id": source_id,
        "chunk_ids": [unit.get("id", "")] if unit.get("id") else [],
        "section_path": clean_display_text(section_path),
        "section_paths": [clean_display_text(section_path)] if section_path else [],
        "aliases": [label],
        "toc_aliases": [label] if origin == "toc" else [],
        "occurrence_count": 1,
        "source_count": 1 if source_id else 0,
        "curation_mix": normalize_curation(Counter({status: 1})),
        "source_titles": [clean_display_text(meta.get("title", "Untitled source"))],
        "publishers": [clean_display_text(meta.get("publisher", "unknown"))],
        "source_ids": [source_id] if source_id else [],
    }


def new_group(candidate: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(candidate))


def merge_candidate(group: dict[str, Any], candidate: dict[str, Any]) -> None:
    group["aliases"] = dedupe_preserve(group.get("aliases", []) + candidate.get("aliases", []))
    group["toc_aliases"] = dedupe_preserve(group.get("toc_aliases", []) + candidate.get("toc_aliases", []))
    group["origins"] = dedupe_preserve(group.get("origins", []) + candidate.get("origins", []))
    group["chunk_ids"] = dedupe_preserve(group.get("chunk_ids", []) + candidate.get("chunk_ids", []))
    group["section_paths"] = dedupe_preserve(group.get("section_paths", []) + candidate.get("section_paths", []))
    group["source_titles"] = dedupe_preserve(group.get("source_titles", []) + candidate.get("source_titles", []))
    group["publishers"] = dedupe_preserve(group.get("publishers", []) + candidate.get("publishers", []))
    group["source_ids"] = dedupe_preserve(group.get("source_ids", []) + candidate.get("source_ids", []))
    group["occurrence_count"] = int(group.get("occurrence_count", 0)) + int(candidate.get("occurrence_count", 1))
    group["source_count"] = len(group.get("source_ids", []))
    group["curation_mix"] = add_curation(group.get("curation_mix", {}), candidate.get("curation_mix", {}))
    if candidate.get("origin") == "toc" and group.get("origin") != "toc":
        group["origin"] = "toc"
        group["label"] = candidate["label"]
        group["normalized_label"] = candidate["normalized_label"]
        group["source_title"] = candidate.get("source_title", group.get("source_title", ""))
        group["publisher"] = candidate.get("publisher", group.get("publisher", ""))
        group["source_id"] = candidate.get("source_id", group.get("source_id", ""))
        group["section_path"] = candidate.get("section_path", group.get("section_path", ""))


def finalize_group(group: dict[str, Any]) -> None:
    alias_counts = Counter(group.get("aliases", []))
    toc_aliases = set(group.get("toc_aliases", []))
    best = sorted(
        alias_counts,
        key=lambda alias: (
            1 if alias in toc_aliases else 0,
            alias_counts[alias],
            len(tokenize(alias)),
            len(alias),
        ),
        reverse=True,
    )[0]
    group["label"] = best
    group["normalized_label"] = normalize_label(best)
    group["source_count"] = len(group.get("source_ids", []))
    group["source_title"] = first_or_blank(group.get("source_titles", []))
    group["publisher"] = first_or_blank(group.get("publishers", []))
    group["source_id"] = first_or_blank(group.get("source_ids", []))
    group["section_path"] = first_or_blank(group.get("section_paths", []))


def should_merge(group: dict[str, Any], candidate: dict[str, Any]) -> bool:
    left = group.get("normalized_label", "")
    right = candidate.get("normalized_label", "")
    if not left or not right:
        return False
    if left == right:
        return True
    jaccard = token_jaccard(left, right)
    ratio = SequenceMatcher(None, left, right).ratio()
    return ratio >= 0.9 or (jaccard >= 0.82 and ratio >= 0.78)


def candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(candidate.get("source_count", 0)),
        int(candidate.get("occurrence_count", 0)),
        1 if candidate.get("origin") == "toc" else 0,
        len(tokenize(candidate.get("label", ""))),
    )


def render_candidates_markdown(title: str, candidates: list[dict[str, Any]], limit: int | None = None) -> str:
    lines = [f"# {title}", ""]
    rows = candidates if limit is None else candidates[:limit]
    if not rows:
        lines.append("No entries.")
        return "\n".join(lines).strip() + "\n"
    for item in rows:
        origins = ", ".join(item.get("origins") or [item.get("origin", "")])
        source = item.get("source_title") or "Untitled source"
        lines.append(
            f"- {item.get('label', '')} | origin: {origins} | occurrences: {item.get('occurrence_count', 0)} | sources: {item.get('source_count', 0)} | {source}"
        )
    return "\n".join(lines).strip() + "\n"


def render_summary_markdown(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    lines = [
        "# Topic Discovery Summary",
        "",
        f"- Corpus: {result.get('corpus', {}).get('path', '')}",
        f"- Method: {result.get('settings', {}).get('method')}",
        f"- Units processed: {summary.get('unit_count', 0)}",
        f"- ToC entries: {summary.get('toc_entry_count', 0)}",
        f"- Heading candidates: {summary.get('heading_candidate_count', 0)}",
        f"- Combined candidates: {summary.get('candidate_count', 0)}",
        f"- Deduped subtopics: {summary.get('deduped_subtopic_count', 0)}",
        f"- LLM status: {summary.get('llm_status', 'unknown')}",
    ]
    if summary.get("llm_error"):
        lines.append(f"- LLM error: {summary['llm_error']}")
    return "\n".join(lines).strip() + "\n"


def metadata(unit: dict[str, Any]) -> dict[str, Any]:
    return unit.get("metadata") or {}


def is_meaningful_label(label: str) -> bool:
    normalized = normalize_label(label)
    if not normalized or normalized in GENERIC_LABELS:
        return False
    if len(label) < 3 or len(label) > 140:
        return False
    if re.fullmatch(r"[\d.\- ]+", label):
        return False
    if re.search(r"\b(?:assets|http|https|www)\b", normalized):
        return False
    return bool(tokenize(label))


def clean_candidate_label(value: Any, strip_trailing_page: bool = False) -> str:
    text = clean_display_text(value)
    text = re.sub(r"\b(?:\.\./)?assets/[^\s]+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\b[\w.-]+\.pdf\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\.{2,}\s*[ivxlcdm\d-]+\s*$", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\.{2,}", " ", text)
    if strip_trailing_page:
        text = re.sub(r"\s+[ivxlcdm\d-]+\s*$", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:figure|table)\s+\d+[a-z]?[.:]?\s+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\s*(?:[a-z]?\d+(?:\.\d+)*|[ivxlcdm]+)\s*[\.)-]?\s+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(final|draft)\s+report\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" :-")
    text = collapse_adjacent_duplicate_words(text)
    return title_if_needed(text)


def clean_display_text(value: Any) -> str:
    text = str(value or "")
    text = remove_markdown_images(text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"[*_`#]+", " ", text)
    text = text.replace("|", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -")


def remove_markdown_images(text: str) -> str:
    return re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", str(text or ""))


def normalize_label(value: Any) -> str:
    text = clean_candidate_label(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token and token not in STOPWORDS]
    return " ".join(tokens)


def tokenize(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9-]{1,}", normalize_label(value))


def token_jaccard(left: Any, right: Any) -> float:
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def collapse_adjacent_duplicate_words(text: str) -> str:
    words = text.split()
    collapsed = []
    for word in words:
        if collapsed and collapsed[-1].lower() == word.lower():
            continue
        collapsed.append(word)
    return " ".join(collapsed)


def title_if_needed(text: str) -> str:
    if text.isupper() or text.islower():
        return text.title()
    return text


def normalize_curation(counter: Counter[str]) -> dict[str, int]:
    return {
        "key_evidence": counter.get("key_evidence", 0),
        "keep": counter.get("keep", 0),
        "maybe": counter.get("maybe", 0),
        "unmarked": counter.get("unmarked", 0),
        "reject": counter.get("reject", 0),
    }


def add_curation(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    keys = {"key_evidence", "keep", "maybe", "unmarked", "reject"}
    return {key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in keys}


def dedupe_preserve(items: Any) -> list[str]:
    result = []
    seen = set()
    for item in items:
        text = str(item).strip()
        key = text.lower()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def first_or_blank(items: list[str]) -> str:
    return items[0] if items else ""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine ToC and heading subtopics, then synthesize a Markdown topic list.")
    parser.add_argument("--corpus", default="latest", help="latest, corpus id, or corpus path")
    parser.add_argument("--max-chunks", type=int, default=None)
    parser.add_argument("--include-maybe", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-unmarked", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-rejected", action="store_true")
    parser.add_argument("--batch-size", type=int, default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_topic_discovery(
        corpus=args.corpus,
        max_chunks=args.max_chunks,
        include_maybe=args.include_maybe,
        include_unmarked=args.include_unmarked,
        include_rejected=args.include_rejected,
    )
    corpus_path = Path(result["corpus"]["path"])
    print(
        "Topic discovery complete: "
        f"{result['summary']['deduped_subtopic_count']} deduped subtopics from {result['summary']['unit_count']} chunks"
    )
    print(corpus_path / "atlas" / "topics" / "topic_tree.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
