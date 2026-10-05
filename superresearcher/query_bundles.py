from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import threading
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from superresearcher.config import atomic_write_json, atomic_write_text
from superresearcher.topic_discovery import (
    clean_candidate_label,
    clean_display_text,
    dedupe_preserve,
    deduped_subtopics_path,
    get_topic_payload,
    is_meaningful_label,
    iter_section_paths,
    load_topic_units,
    normalize_curation,
    normalize_label,
    path_labels,
    read_json_list,
    read_curated_topic_tree,
    resolve_topic_corpus,
    token_jaccard,
)


DEFAULT_SUBTOPIC = "Charging coordination, UAM charging demand, and power grid flow"
MAX_ANCHORS = 80
MAX_WINDOW_UNITS = 180
MAX_TERMS = 240
MAX_QUERIES = 60
QUERY_BUNDLE_VERSION = "compose-query-bundles-v1"
MAX_COMPOSE_TAGS_PER_SUBTOPIC = 120
COMPOSE_JOBS: dict[str, "ComposeBuildJob"] = {}
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "these",
    "this",
    "those",
    "to",
    "was",
    "were",
    "with",
}
BAD_BOUNDARY_STOPWORDS = STOPWORDS - {"on"}
GENERIC_LABELS = {
    "background and motivation",
    "constraints",
    "data overview",
    "decision variable",
    "decision variables",
    "executive summary",
    "introduction",
    "overview",
    "our contribution",
    "related literature",
}


def run_query_bundle_experiment(
    corpus: str = "latest",
    subtopic: str = DEFAULT_SUBTOPIC,
    include_maybe: bool = True,
    include_unmarked: bool = True,
    include_rejected: bool = False,
    max_chunks: int | None = None,
) -> dict[str, Any]:
    corpus_path = resolve_topic_corpus(corpus)
    context = load_query_bundle_context(
        corpus_path,
        include_maybe=include_maybe,
        include_unmarked=include_unmarked,
        include_rejected=include_rejected,
        max_chunks=max_chunks,
    )
    result = build_single_query_bundle(corpus_path, context, subtopic)
    write_outputs(corpus_path, result)
    return result


def load_query_bundle_context(
    corpus_path: Path,
    include_maybe: bool = True,
    include_unmarked: bool = True,
    include_rejected: bool = False,
    max_chunks: int | None = None,
) -> dict[str, Any]:
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
        raise RuntimeError("No Atlas chunks available after filters.")

    topic_payload = get_topic_payload(str(corpus_path))
    return {
        "units": units,
        "filter_counts": filter_counts,
        "topic_payload": topic_payload,
        "topic_nodes": flatten_topics(topic_payload.get("topics", [])),
        "deduped": read_json_list(deduped_subtopics_path(corpus_path)),
        "toc_entries": read_json_list(corpus_path / "atlas" / "topics" / "toc_entries.json"),
        "tfidf_corpus": build_tfidf_corpus_index(units),
    }


def build_single_query_bundle(
    corpus_path: Path,
    context: dict[str, Any],
    subtopic: str,
    matched_topic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    units = context["units"]
    filter_counts = context["filter_counts"]
    topic_nodes = context["topic_nodes"]
    deduped = context["deduped"]
    toc_entries = context["toc_entries"]
    matched_topic = matched_topic or find_topic_node(subtopic, topic_nodes)
    matched_deduped = match_candidate_records(subtopic, deduped, minimum=0.34)
    matched_toc = match_candidate_records(subtopic, toc_entries, minimum=0.34)
    seed = build_seed_terms(subtopic, matched_topic, matched_deduped, matched_toc)
    anchors = find_anchor_chunks(units, seed, matched_deduped + matched_toc)
    window_units = build_evidence_window(units, anchors, seed, matched_topic)
    term_stats, acronyms = extract_candidate_terms(units, window_units, anchors, seed, matched_topic, matched_deduped, matched_toc, context.get("tfidf_corpus"))
    scored_terms = score_and_dedupe_terms(term_stats, seed)
    query_bundle = build_query_bundle(subtopic, matched_topic, seed, scored_terms, acronyms)
    coverage = build_coverage_report(units, anchors, window_units, scored_terms, query_bundle, filter_counts)

    result = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "selected_subtopic": subtopic,
        "matched_topic": matched_topic,
        "summary": {
            "unit_count": len(units),
            "topic_node_count": len(topic_nodes),
            "matched_deduped_records": len(matched_deduped),
            "matched_toc_records": len(matched_toc),
            "anchor_chunk_count": len(anchors),
            "evidence_window_count": len(window_units),
            "candidate_term_count": len(scored_terms),
            "retrieval_query_count": len(query_bundle.get("retrieval_queries", [])),
            "filters": filter_counts,
        },
        "query_bundle": query_bundle,
        "candidate_terms": scored_terms[:MAX_TERMS],
        "anchor_chunks": anchors,
        "coverage": coverage,
    }
    return result


class ComposeBuildJob:
    def __init__(self, corpus: Path, force: bool = False) -> None:
        self.corpus = corpus.resolve()
        self.force = force
        self.job_id = f"compose-{self.corpus.name}-{int(time.time())}"
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
        COMPOSE_JOBS[self.job_id] = self
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
            result = build_compose_query_bundles(str(self.corpus), force=self.force, progress=self.event)
            summary = result.get("summary", {})
            with self._lock:
                self.status["state"] = "completed"
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")
                self.status["progress"] = 100
                self.status["stage"] = "Ready"
                self.status["counts"].update(summary)
        except Exception as exc:
            error_path = compose_dir(self.corpus) / "build-error.log"
            error_path.parent.mkdir(parents=True, exist_ok=True)
            error_path.write_text(traceback.format_exc(), encoding="utf-8")
            with self._lock:
                self.status["state"] = "failed"
                self.status["error"] = str(exc)
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")


def start_compose_build_job(corpus_id_or_path: str, force: bool = False) -> ComposeBuildJob:
    corpus_path = resolve_topic_corpus(corpus_id_or_path)
    for job in COMPOSE_JOBS.values():
        snap = job.snapshot()
        if snap["corpus_path"] == str(corpus_path) and snap["state"] in {"queued", "running"}:
            return job
    job = ComposeBuildJob(corpus_path, force=force)
    job.start()
    return job


def get_compose_build_job(job_id: str) -> ComposeBuildJob | None:
    return COMPOSE_JOBS.get(job_id)


def compose_dir(corpus: Path) -> Path:
    return corpus / "atlas" / "compose"


def compose_query_bundles_path(corpus: Path) -> Path:
    return compose_dir(corpus) / "query_bundles.json"


def compose_finalized_json_path(corpus: Path) -> Path:
    return compose_dir(corpus) / "finalized_terms.json"


def compose_finalized_md_path(corpus: Path) -> Path:
    return compose_dir(corpus) / "finalized_terms.md"


def get_compose_payload(corpus: str = "latest") -> dict[str, Any]:
    corpus_path = resolve_topic_corpus(corpus)
    selected_subtopics = selected_leaf_subtopics(corpus_path)
    signature = compose_cache_signature(corpus_path)
    generated = read_json_object(compose_query_bundles_path(corpus_path))
    finalized = read_json_object(compose_finalized_json_path(corpus_path))
    generated_valid = bool(generated) and generated.get("cache_signature") == signature
    return {
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "cache_signature": signature,
        "selected_subtopics": selected_subtopics,
        "generated": {
            "exists": bool(generated),
            "valid": generated_valid,
            "path": str(compose_query_bundles_path(corpus_path)),
            "payload": generated if generated_valid else None,
        },
        "finalized": {
            "exists": bool(finalized),
            "path": str(compose_finalized_json_path(corpus_path)),
            "markdown_path": str(compose_finalized_md_path(corpus_path)),
            "payload": finalized if finalized else None,
        },
        "active": finalized if finalized else (generated if generated_valid else None),
    }


def build_compose_query_bundles(corpus: str = "latest", force: bool = False, progress=None) -> dict[str, Any]:
    corpus_path = resolve_topic_corpus(corpus)
    signature = compose_cache_signature(corpus_path)
    cached = read_json_object(compose_query_bundles_path(corpus_path))
    if not force and cached.get("cache_signature") == signature:
        cached["cached"] = True
        if progress:
            progress("Using cached terms", 100, subtopic_count=len(cached.get("subtopics", [])), cached=1)
        return cached

    subtopics = selected_leaf_subtopics(corpus_path)
    if not subtopics:
        raise RuntimeError("No selected leaf subtopics found. Save curated topics first.")
    if progress:
        progress("Loading curated corpus", 8, subtopic_count=len(subtopics))
    context = load_query_bundle_context(corpus_path)
    rows = []
    total = len(subtopics)
    for index, subtopic in enumerate(subtopics, start=1):
        label = subtopic["label"]
        if progress:
            progress(
                f"Generating terms for {label}",
                10 + int((index - 1) / max(total, 1) * 82),
                completed_subtopics=index - 1,
                subtopic_count=total,
                current_subtopic=label,
            )
        result = build_single_query_bundle(corpus_path, context, label, matched_topic=subtopic)
        rows.append(compose_subtopic_from_result(subtopic, result))
    payload = {
        "version": QUERY_BUNDLE_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "cache_signature": signature,
        "subtopics": rows,
        "summary": compose_summary(rows),
    }
    compose_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    atomic_write_json(compose_query_bundles_path(corpus_path), payload)
    if progress:
        progress("Saved generated terms", 96, completed_subtopics=total, current_subtopic=None, **payload["summary"])
    return payload


def save_finalized_compose_terms(corpus: str, payload: dict[str, Any]) -> dict[str, Any]:
    corpus_path = resolve_topic_corpus(corpus)
    subtopics = normalize_compose_subtopics(payload.get("subtopics", []), selected_only=True)
    out = {
        "version": QUERY_BUNDLE_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "cache_signature": compose_cache_signature(corpus_path),
        "subtopics": subtopics,
        "summary": compose_summary(subtopics),
    }
    compose_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    atomic_write_json(compose_finalized_json_path(corpus_path), out)
    atomic_write_text(compose_finalized_md_path(corpus_path), render_finalized_compose_markdown(out))
    return get_compose_payload(str(corpus_path))


def selected_leaf_subtopics(corpus: Path) -> list[dict[str, Any]]:
    curated = read_curated_topic_tree(corpus)
    if not curated:
        raise RuntimeError("Curated topic tree not found. Save curated topics first.")
    rows: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]], path: list[str]) -> None:
        selected_siblings = [clean_display_text(node.get("label", "")) for node in nodes if node.get("selected", True)]
        for node in nodes:
            if not node.get("selected", True):
                continue
            label = clean_display_text(node.get("label", ""))
            if not label:
                continue
            current_path = path + [label]
            children = node.get("children", [])
            if not children:
                rows.append(
                    {
                        "id": node.get("id", ""),
                        "topic_id": node.get("id", ""),
                        "label": label,
                        "path": current_path,
                        "parent_label": path[-1] if path else "",
                        "sibling_labels": [item for item in selected_siblings if item != label],
                        "children": [],
                        "match_score": 1.0,
                    }
                )
            walk(children, current_path)

    walk(curated.get("topics", []), [])
    return rows


def compose_cache_signature(corpus: Path) -> str:
    inputs = {
        "version": QUERY_BUNDLE_VERSION,
        "curated_topic_tree": read_text_if_exists(corpus / "atlas" / "topics" / "curated_topic_tree.json"),
        "atlas_signature": read_json_object(corpus / "atlas" / "manifest.json").get("signature"),
        "deduped_subtopics": read_text_if_exists(deduped_subtopics_path(corpus)),
        "toc_entries": read_text_if_exists(corpus / "atlas" / "topics" / "toc_entries.json"),
    }
    raw = json.dumps(inputs, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compose_subtopic_from_result(subtopic: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    tags = compose_tags_from_result(subtopic.get("id") or subtopic.get("topic_id") or "topic", result)
    summary = result.get("summary", {})
    return {
        "topic_id": subtopic.get("topic_id") or subtopic.get("id", ""),
        "label": subtopic.get("label", result.get("selected_subtopic", "")),
        "path": subtopic.get("path") or result.get("matched_topic", {}).get("path", []),
        "parent_label": subtopic.get("parent_label", ""),
        "enabled": True,
        "summary": {
            "anchor_chunk_count": summary.get("anchor_chunk_count", 0),
            "evidence_window_count": summary.get("evidence_window_count", 0),
            "candidate_term_count": summary.get("candidate_term_count", 0),
            "retrieval_query_count": summary.get("retrieval_query_count", 0),
        },
        "tags": tags,
    }


def compose_tags_from_result(topic_id: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_index = {
        item.get("normalized"): item
        for item in result.get("candidate_terms", [])
        if item.get("normalized")
    }
    rows: dict[str, dict[str, Any]] = {}

    def add(label: Any, category: str) -> None:
        text = clean_candidate_label(label)
        if not is_usable_term(text):
            return
        normalized = normalize_label(text)
        if not normalized:
            return
        candidate = candidate_index.get(normalized, {})
        row = rows.setdefault(
            normalized,
            {
                "id": "",
                "label": text,
                "normalized": normalized,
                "selected": True,
                "categories": [],
                "score": float(candidate.get("score", 0)),
                "source_count": int(candidate.get("source_count", 0)),
                "chunk_count": int(candidate.get("chunk_count", 0)),
            },
        )
        if category not in row["categories"]:
            row["categories"].append(category)
        for candidate_category in candidate.get("categories", {}):
            if candidate_category not in row["categories"]:
                row["categories"].append(candidate_category)
        row["score"] = max(float(row.get("score", 0)), float(candidate.get("score", 0) or 0))
        row["source_count"] = max(int(row.get("source_count", 0)), int(candidate.get("source_count", 0) or 0))
        row["chunk_count"] = max(int(row.get("chunk_count", 0)), int(candidate.get("chunk_count", 0) or 0))

    bundle = result.get("query_bundle", {})
    ordered_groups = [
        "seed_terms",
        "aliases",
        "heading_terms",
        "toc_terms",
        "tfidf_terms",
        "cooccurring_terms",
        "bigrams_trigrams",
        "noun_phrases",
        "source_title_terms",
        "table_figure_terms",
        "retrieval_queries",
    ]
    for group in ordered_groups:
        for value in bundle.get(group, []):
            add(value, group)
    for pair in bundle.get("acronyms", []):
        add(pair.get("acronym", ""), "acronym")
        add(pair.get("expansion", ""), "acronym")
        add(pair.get("term", ""), "acronym")
    for item in result.get("candidate_terms", []):
        add(item.get("term", ""), "candidate")

    tags = sorted(rows.values(), key=lambda item: (item["score"], item["source_count"], item["chunk_count"]), reverse=True)
    for index, tag in enumerate(tags[:MAX_COMPOSE_TAGS_PER_SUBTOPIC], start=1):
        tag["id"] = f"{topic_id or 'topic'}-tag-{index:03d}"
        tag["categories"] = sorted(tag["categories"])
        tag["score"] = round(float(tag["score"]), 4)
    return tags[:MAX_COMPOSE_TAGS_PER_SUBTOPIC]


def normalize_compose_subtopics(subtopics: Any, selected_only: bool = False) -> list[dict[str, Any]]:
    if not isinstance(subtopics, list):
        return []
    rows = []
    for subtopic in subtopics:
        if not isinstance(subtopic, dict):
            continue
        enabled = bool(subtopic.get("enabled", True))
        if selected_only and not enabled:
            continue
        tags = normalize_compose_tags(subtopic.get("tags", []), selected_only=selected_only)
        if selected_only and not tags:
            continue
        rows.append(
            {
                "topic_id": str(subtopic.get("topic_id") or subtopic.get("id") or ""),
                "label": clean_display_text(subtopic.get("label", "")),
                "path": [clean_display_text(item) for item in subtopic.get("path", []) if clean_display_text(item)],
                "parent_label": clean_display_text(subtopic.get("parent_label", "")),
                "enabled": enabled,
                "summary": subtopic.get("summary", {}) if isinstance(subtopic.get("summary"), dict) else {},
                "tags": tags,
            }
        )
    return rows


def normalize_compose_tags(tags: Any, selected_only: bool = False) -> list[dict[str, Any]]:
    if not isinstance(tags, list):
        return []
    rows = []
    seen = set()
    for tag in tags:
        if not isinstance(tag, dict):
            continue
        selected = bool(tag.get("selected", True))
        if selected_only and not selected:
            continue
        label = clean_candidate_label(tag.get("label", ""))
        normalized = normalize_label(tag.get("normalized") or label)
        if not normalized or normalized in seen or not is_usable_term(label):
            continue
        seen.add(normalized)
        rows.append(
            {
                "id": str(tag.get("id") or f"tag-{len(rows) + 1:03d}"),
                "label": label,
                "normalized": normalized,
                "selected": selected,
                "categories": sorted(str(item) for item in tag.get("categories", []) if str(item).strip()),
                "score": round(float(tag.get("score", 0) or 0), 4),
                "source_count": int(tag.get("source_count", 0) or 0),
                "chunk_count": int(tag.get("chunk_count", 0) or 0),
            }
        )
    return rows


def compose_summary(subtopics: list[dict[str, Any]]) -> dict[str, Any]:
    enabled = [item for item in subtopics if item.get("enabled", True)]
    selected_tags = [
        tag
        for item in enabled
        for tag in item.get("tags", [])
        if tag.get("selected", True)
    ]
    return {
        "subtopic_count": len(subtopics),
        "enabled_subtopic_count": len(enabled),
        "tag_count": sum(len(item.get("tags", [])) for item in subtopics),
        "selected_tag_count": len(selected_tags),
    }


def render_finalized_compose_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Finalized Compose Terms", ""]
    for subtopic in payload.get("subtopics", []):
        path = " > ".join(subtopic.get("path", []))
        lines.extend([f"## {subtopic.get('label', '')}", ""])
        if path:
            lines.extend([f"Path: {path}", ""])
        tags = [tag for tag in subtopic.get("tags", []) if tag.get("selected", True)]
        if not tags:
            lines.append("No selected terms.")
        else:
            for tag in tags:
                lines.append(f"- {tag.get('label', '')}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def read_text_if_exists(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def flatten_topics(topics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]], path: list[str]) -> None:
        labels = [clean_display_text(node.get("label", "")) for node in nodes if node.get("selected", True)]
        for node in nodes:
            if not node.get("selected", True):
                continue
            label = clean_display_text(node.get("label", ""))
            if not label:
                continue
            current_path = path + [label]
            rows.append(
                {
                    "id": node.get("id", ""),
                    "label": label,
                    "path": current_path,
                    "parent_label": path[-1] if path else "",
                    "sibling_labels": [item for item in labels if item != label],
                    "children": [clean_display_text(child.get("label", "")) for child in node.get("children", []) if child.get("selected", True)],
                    "match_score": 0.0,
                }
            )
            walk(node.get("children", []), current_path)

    walk(topics, [])
    return rows


def find_topic_node(subtopic: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    if not nodes:
        return {"id": "", "label": subtopic, "path": [subtopic], "parent_label": "", "sibling_labels": [], "children": [], "match_score": 0.0}
    ranked = sorted(nodes, key=lambda node: label_similarity(subtopic, node.get("label", "")), reverse=True)
    best = dict(ranked[0])
    best["match_score"] = label_similarity(subtopic, best.get("label", ""))
    if best["match_score"] < 0.24:
        return {"id": "", "label": subtopic, "path": [subtopic], "parent_label": "", "sibling_labels": [], "children": [], "match_score": best["match_score"]}
    return best


def match_candidate_records(query: str, records: list[dict[str, Any]], minimum: float = 0.34) -> list[dict[str, Any]]:
    matches = []
    for record in records:
        labels = [record.get("label", "")]
        labels.extend(record.get("aliases", []))
        labels.extend(record.get("section_paths", []))
        score = max([label_similarity(query, label) for label in labels if label] or [0.0])
        if score >= minimum:
            item = dict(record)
            item["match_score"] = score
            matches.append(item)
    return sorted(matches, key=lambda item: (item.get("match_score", 0), item.get("source_count", 0), item.get("occurrence_count", 0)), reverse=True)


def build_seed_terms(
    subtopic: str,
    matched_topic: dict[str, Any],
    matched_deduped: list[dict[str, Any]],
    matched_toc: list[dict[str, Any]],
) -> dict[str, Any]:
    aliases = []
    for record in matched_deduped[:40] + matched_toc[:24]:
        aliases.append(record.get("label", ""))
        aliases.extend(record.get("aliases", []))
    path_terms = matched_topic.get("path", []) if matched_topic else []
    sibling_terms = matched_topic.get("sibling_labels", []) if matched_topic else []
    children = matched_topic.get("children", []) if matched_topic else []
    return {
        "selected": clean_display_text(subtopic),
        "topic_path": dedupe_preserve(path_terms),
        "aliases": dedupe_preserve(aliases),
        "sibling_terms": dedupe_preserve(sibling_terms),
        "child_terms": dedupe_preserve(children),
        "anchor_terms": dedupe_preserve([subtopic] + aliases),
        "all": dedupe_preserve([subtopic] + path_terms + aliases),
    }


def find_anchor_chunks(units: list[dict[str, Any]], seed: dict[str, Any], matched_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    record_chunk_ids = set()
    for record in matched_records:
        record_chunk_ids.update(record.get("chunk_ids", []))
    anchors = []
    seed_terms = seed.get("anchor_terms", []) or seed.get("all", [])
    compiled_seed = compile_query_terms(seed_terms)
    for index, unit in enumerate(units):
        unit_id = unit.get("id", "")
        meta = metadata(unit)
        search_text = " ".join(
            [
                meta.get("title", ""),
                meta.get("publisher", ""),
                meta.get("section_path", ""),
                " ".join(str(path) for path in meta.get("section_paths", [])[:6]),
                unit.get("text", "")[:1800],
            ]
        )
        search_norm = normalize_label(search_text)
        similarity = text_overlap_score(compiled_seed, search_text, precomputed_text_norm=search_norm)
        exact_hits = [item["label"] for item in compiled_seed if item["normalized"] in search_norm]
        score = similarity + 0.18 * len(exact_hits)
        reasons = []
        if unit_id in record_chunk_ids:
            score += 1.0
            reasons.append("matched deduped/ToC chunk id")
        if exact_hits:
            reasons.append("exact normalized term hit")
        if similarity >= 0.24:
            reasons.append("fuzzy/semantic lexical overlap")
        if score >= 0.34:
            anchors.append(anchor_row(unit, index, score, reasons or ["weak lexical overlap"]))
    return sorted(anchors, key=lambda row: row["score"], reverse=True)[:MAX_ANCHORS]


def build_evidence_window(
    units: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    seed: dict[str, Any],
    matched_topic: dict[str, Any],
) -> list[dict[str, Any]]:
    by_id = {unit.get("id"): (index, unit) for index, unit in enumerate(units)}
    selected: dict[str, tuple[float, dict[str, Any]]] = {}
    anchor_ids = {anchor["chunk_id"] for anchor in anchors}
    seed_terms = seed.get("all", []) + seed.get("sibling_terms", [])
    compiled_seed = compile_query_terms(seed_terms)

    def add(unit: dict[str, Any], score: float) -> None:
        unit_id = unit.get("id")
        if not unit_id:
            return
        existing = selected.get(unit_id)
        if existing is None or score > existing[0]:
            selected[unit_id] = (score, unit)

    for anchor in anchors:
        index, unit = by_id.get(anchor["chunk_id"], (-1, {}))
        if not unit:
            continue
        add(unit, 2.0 + anchor.get("score", 0))
        for offset in (-2, -1, 1, 2):
            neighbor_index = index + offset
            if 0 <= neighbor_index < len(units) and same_source(unit, units[neighbor_index]):
                add(units[neighbor_index], 1.15)

    anchor_sections = [anchor.get("section_path", "") for anchor in anchors]
    compiled_anchor_sections = compile_query_terms(anchor_sections[:24])
    for unit in units:
        unit_id = unit.get("id")
        if unit_id in anchor_ids:
            continue
        meta = metadata(unit)
        haystack = " ".join([meta.get("title", ""), meta.get("section_path", ""), unit.get("text", "")[:1200]])
        similarity = text_overlap_score(compiled_seed, haystack)
        section_similarity = text_overlap_score(compiled_anchor_sections, meta.get("section_path", ""))
        curated = unit.get("selection_status") in {"keep", "key_evidence"}
        if similarity >= 0.24 or section_similarity >= 0.5 or (curated and similarity >= 0.15):
            add(unit, similarity + section_similarity + (0.6 if curated else 0))

    ranked = sorted(selected.values(), key=lambda item: item[0], reverse=True)
    return [unit for _, unit in ranked[:MAX_WINDOW_UNITS]]


def extract_candidate_terms(
    corpus_units: list[dict[str, Any]],
    window_units: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    seed: dict[str, Any],
    matched_topic: dict[str, Any],
    matched_deduped: list[dict[str, Any]],
    matched_toc: list[dict[str, Any]],
    tfidf_corpus: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    stats: dict[str, dict[str, Any]] = {}
    anchor_ids = {anchor["chunk_id"] for anchor in anchors}

    for term in [seed.get("selected", "")] + seed.get("aliases", []):
        add_term(stats, term, "alias", None, 3.0)
    for term in seed.get("sibling_terms", []):
        add_term(stats, term, "sibling", None, 1.2)
    for term in seed.get("topic_path", []):
        add_term(stats, term, "heading", None, 1.6)

    for record in matched_deduped:
        add_term(stats, record.get("label", ""), "alias", None, 2.0)
        for alias in record.get("aliases", []):
            add_term(stats, alias, "alias", None, 1.5)
        for path in record.get("section_paths", []):
            for label in path_labels(path):
                add_term(stats, label, "heading", None, 1.0)
    for record in matched_toc:
        add_term(stats, record.get("label", ""), "toc", None, 2.0)

    for unit in window_units:
        meta = metadata(unit)
        for path in iter_section_paths(unit):
            for label in path_labels(path):
                add_term(stats, label, "heading", unit, 1.8)
        source_label = clean_candidate_label(meta.get("title", ""))
        if is_meaningful_label(source_label):
            add_term(stats, source_label, "source_title", unit, 0.8)
        for caption in extract_table_figure_terms(unit.get("text", "")):
            add_term(stats, caption, "table_figure", unit, 1.6)
        for phrase in extract_ngrams(unit.get("text", ""), sizes=(2, 3), limit=80):
            add_term(stats, phrase, "bigrams_trigrams", unit, 0.7)
        for phrase in extract_repeated_noun_phrases(unit.get("text", ""), limit=50):
            add_term(stats, phrase, "noun_phrases", unit, 0.8)
        if unit.get("id") in anchor_ids:
            for phrase in extract_ngrams(unit.get("text", ""), sizes=(2, 3), limit=60):
                add_term(stats, phrase, "cooccurring", unit, 1.0)

    for term, tfidf_score in extract_tfidf_terms(corpus_units, window_units, limit=120, corpus_index=tfidf_corpus).items():
        add_term(stats, term, "tfidf", None, tfidf_score)

    acronyms = extract_acronym_pairs(window_units)
    for pair in acronyms:
        add_term(stats, pair["term"], "acronym", None, 2.2)
        add_term(stats, pair["acronym"], "acronym", None, 1.2)
        add_term(stats, pair["expansion"], "acronym", None, 1.8)

    return stats, acronyms


def add_term(stats: dict[str, dict[str, Any]], term: Any, category: str, unit: dict[str, Any] | None, weight: float) -> None:
    label = clean_candidate_label(term)
    if not is_usable_term(label):
        return
    normalized = normalize_label(label)
    if not normalized:
        return
    row = stats.setdefault(
        normalized,
        {
            "term": label,
            "normalized": normalized,
            "categories": Counter(),
            "frequency": 0,
            "raw_weight": 0.0,
            "chunk_ids": set(),
            "source_ids": set(),
            "source_titles": set(),
            "curation_mix": Counter(),
            "score_components": {},
        },
    )
    if len(label) > len(row["term"]) and len(label) <= 90:
        row["term"] = label
    row["categories"][category] += 1
    row["frequency"] += 1
    row["raw_weight"] += weight
    if unit:
        meta = metadata(unit)
        if unit.get("id"):
            row["chunk_ids"].add(unit["id"])
        if meta.get("source_id"):
            row["source_ids"].add(meta["source_id"])
        if meta.get("title"):
            row["source_titles"].add(clean_display_text(meta["title"]))
        row["curation_mix"][unit.get("selection_status", "unmarked")] += 1


def score_and_dedupe_terms(stats: dict[str, dict[str, Any]], seed: dict[str, Any]) -> list[dict[str, Any]]:
    seed_terms = seed.get("all", [])
    compiled_seed = compile_query_terms(seed_terms)
    rows = []
    for row in stats.values():
        categories = row["categories"]
        source_count = len(row["source_ids"])
        curation = row["curation_mix"]
        curation_weight = curation.get("key_evidence", 0) * 3.0 + curation.get("keep", 0) * 2.0 + curation.get("maybe", 0)
        similarity = fast_similarity_to_compiled(row["normalized"], compiled_seed)
        category_boost = (
            categories.get("alias", 0) * 4.0
            + categories.get("toc", 0) * 3.2
            + categories.get("heading", 0) * 2.4
            + categories.get("table_figure", 0) * 2.0
            + categories.get("tfidf", 0) * 1.6
            + categories.get("cooccurring", 0) * 1.3
            + categories.get("acronym", 0) * 2.1
            + categories.get("source_title", 0) * 0.6
        )
        score = row["raw_weight"] + category_boost + source_count * 1.4 + curation_weight + similarity * 8.0 + math.log1p(row["frequency"])
        rows.append(
            {
                "term": row["term"],
                "normalized": row["normalized"],
                "score": round(score, 4),
                "frequency": row["frequency"],
                "source_count": source_count,
                "chunk_count": len(row["chunk_ids"]),
                "categories": dict(categories),
                "chunk_ids": sorted(row["chunk_ids"]),
                "source_ids": sorted(row["source_ids"]),
                "source_titles": sorted(row["source_titles"])[:8],
                "curation_mix": normalize_curation(curation),
                "score_components": {
                    "raw_weight": round(row["raw_weight"], 4),
                    "category_boost": round(category_boost, 4),
                    "source_diversity": round(source_count * 1.4, 4),
                    "curation_weight": round(curation_weight, 4),
                    "subtopic_similarity": round(similarity * 8.0, 4),
                },
            }
        )
    rows = sorted(rows, key=lambda item: item["score"], reverse=True)[:700]
    return dedupe_scored_terms(rows)[:MAX_TERMS]


def dedupe_scored_terms(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for row in rows:
        match = next((item for item in selected if terms_should_merge(item, row)), None)
        if match:
            match["score"] = round(max(match["score"], row["score"]) + min(match["score"], row["score"]) * 0.08, 4)
            match["frequency"] += row.get("frequency", 0)
            match["categories"] = dict(Counter(match.get("categories", {})) + Counter(row.get("categories", {})))
            match["chunk_ids"] = dedupe_preserve(match.get("chunk_ids", []) + row.get("chunk_ids", []))
            match["source_ids"] = dedupe_preserve(match.get("source_ids", []) + row.get("source_ids", []))
            match["source_count"] = len(match["source_ids"])
            match["source_titles"] = dedupe_preserve(match.get("source_titles", []) + row.get("source_titles", []))[:8]
            match["curation_mix"] = add_curation_dicts(match.get("curation_mix", {}), row.get("curation_mix", {}))
        else:
            selected.append(json.loads(json.dumps(row)))
    return sorted(selected, key=lambda item: item["score"], reverse=True)


def build_query_bundle(
    subtopic: str,
    matched_topic: dict[str, Any],
    seed: dict[str, Any],
    scored_terms: list[dict[str, Any]],
    acronyms: list[dict[str, str]],
) -> dict[str, Any]:
    by_category = terms_by_category(scored_terms)
    bundle = {
        "subtopic": clean_display_text(subtopic),
        "matched_topic_path": matched_topic.get("path", []),
        "seed_terms": dedupe_preserve([subtopic] + matched_topic.get("path", [])),
        "aliases": top_terms_from_values(seed.get("aliases", []), 30),
        "sibling_terms": top_terms_from_values(seed.get("sibling_terms", []), 20),
        "heading_terms": by_category.get("heading", []),
        "toc_terms": by_category.get("toc", []),
        "source_title_terms": by_category.get("source_title", []),
        "table_figure_terms": by_category.get("table_figure", []),
        "tfidf_terms": by_category.get("tfidf", []),
        "bigrams_trigrams": by_category.get("bigrams_trigrams", []),
        "noun_phrases": by_category.get("noun_phrases", []),
        "acronyms": acronyms[:40],
        "cooccurring_terms": by_category.get("cooccurring", []),
    }
    bundle["retrieval_queries"] = compose_retrieval_queries(bundle, scored_terms)
    return bundle


def write_outputs(corpus_path: Path, result: dict[str, Any]) -> dict[str, str]:
    out_dir = corpus_path / "atlas" / "query_bundles"
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "query_bundle_json": out_dir / "query_bundle.json",
        "query_bundle_md": out_dir / "query_bundle.md",
        "candidate_terms_json": out_dir / "candidate_terms.json",
        "candidate_terms_md": out_dir / "candidate_terms.md",
        "anchor_chunks_json": out_dir / "anchor_chunks.json",
        "coverage_report_md": out_dir / "coverage_report.md",
    }
    atomic_write_json(paths["query_bundle_json"], result)
    atomic_write_text(paths["query_bundle_md"], render_query_bundle_markdown(result))
    atomic_write_json(paths["candidate_terms_json"], result.get("candidate_terms", []))
    atomic_write_text(paths["candidate_terms_md"], render_candidate_terms_markdown(result.get("candidate_terms", [])))
    atomic_write_json(paths["anchor_chunks_json"], result.get("anchor_chunks", []))
    atomic_write_text(paths["coverage_report_md"], render_coverage_markdown(result))
    return {key: str(path) for key, path in paths.items()}


def build_coverage_report(
    units: list[dict[str, Any]],
    anchors: list[dict[str, Any]],
    window_units: list[dict[str, Any]],
    scored_terms: list[dict[str, Any]],
    query_bundle: dict[str, Any],
    filter_counts: dict[str, int],
) -> dict[str, Any]:
    anchor_source_ids = {anchor.get("source_id") for anchor in anchors if anchor.get("source_id")}
    window_source_ids = {metadata(unit).get("source_id") for unit in window_units if metadata(unit).get("source_id")}
    publishers = {metadata(unit).get("publisher") for unit in window_units if metadata(unit).get("publisher")}
    curation = Counter(unit.get("selection_status", "unmarked") for unit in window_units)
    top_sources = Counter(metadata(unit).get("title", "Untitled source") for unit in window_units).most_common(10)
    gaps = []
    if len(anchors) < 5:
        gaps.append("Low anchor count; retrieval may depend heavily on expansion terms.")
    if len(window_source_ids) < 3:
        gaps.append("Low source diversity in local evidence window.")
    if len(query_bundle.get("retrieval_queries", [])) < 12:
        gaps.append("Few retrieval queries generated.")
    return {
        "anchor_chunk_count": len(anchors),
        "anchor_source_count": len(anchor_source_ids),
        "evidence_window_count": len(window_units),
        "evidence_window_source_count": len(window_source_ids),
        "publisher_count": len(publishers),
        "curation_mix": normalize_curation(curation),
        "top_sources": [{"title": title, "chunk_count": count} for title, count in top_sources],
        "top_terms": [{"term": item["term"], "score": item["score"]} for item in scored_terms[:20]],
        "retrieval_query_count": len(query_bundle.get("retrieval_queries", [])),
        "filters": filter_counts,
        "gaps": gaps,
    }


def render_query_bundle_markdown(result: dict[str, Any]) -> str:
    bundle = result.get("query_bundle", {})
    lines = [
        "# Query Bundle",
        "",
        f"Subtopic: {bundle.get('subtopic', '')}",
        f"Matched topic path: {' > '.join(bundle.get('matched_topic_path', [])) or 'none'}",
        "",
    ]
    for key, title in [
        ("seed_terms", "Seed Terms"),
        ("aliases", "Aliases"),
        ("sibling_terms", "Sibling Terms"),
        ("heading_terms", "Heading Terms"),
        ("toc_terms", "ToC Terms"),
        ("source_title_terms", "Source Title Terms"),
        ("table_figure_terms", "Table/Figure Terms"),
        ("tfidf_terms", "TF-IDF Terms"),
        ("bigrams_trigrams", "Bigrams/Trigrams"),
        ("noun_phrases", "Noun Phrases"),
        ("cooccurring_terms", "Co-occurring Terms"),
        ("retrieval_queries", "Retrieval Queries"),
    ]:
        lines.extend([f"## {title}", ""])
        values = bundle.get(key, [])
        if not values:
            lines.append("No terms.")
        else:
            for value in values[:MAX_QUERIES]:
                lines.append(f"- {value}")
        lines.append("")
    if bundle.get("acronyms"):
        lines.extend(["## Acronyms", ""])
        for pair in bundle["acronyms"]:
            lines.append(f"- {pair.get('acronym')} = {pair.get('expansion')}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_candidate_terms_markdown(terms: list[dict[str, Any]]) -> str:
    lines = ["# Candidate Terms", ""]
    for item in terms:
        categories = ", ".join(sorted(item.get("categories", {})))
        lines.append(f"- {item['term']} | score: {item['score']} | sources: {item['source_count']} | categories: {categories}")
    return "\n".join(lines).strip() + "\n"


def render_coverage_markdown(result: dict[str, Any]) -> str:
    coverage = result.get("coverage", {})
    lines = [
        "# Coverage Report",
        "",
        f"Anchor chunks: {coverage.get('anchor_chunk_count', 0)}",
        f"Anchor sources: {coverage.get('anchor_source_count', 0)}",
        f"Evidence window chunks: {coverage.get('evidence_window_count', 0)}",
        f"Evidence window sources: {coverage.get('evidence_window_source_count', 0)}",
        f"Publishers: {coverage.get('publisher_count', 0)}",
        f"Retrieval queries: {coverage.get('retrieval_query_count', 0)}",
        f"Curation mix: {json.dumps(coverage.get('curation_mix', {}), sort_keys=True)}",
        "",
        "## Top Sources",
        "",
    ]
    for source in coverage.get("top_sources", []):
        lines.append(f"- {source['title']} ({source['chunk_count']} chunks)")
    lines.extend(["", "## Top Terms", ""])
    for term in coverage.get("top_terms", []):
        lines.append(f"- {term['term']} ({term['score']})")
    if coverage.get("gaps"):
        lines.extend(["", "## Gaps", ""])
        for gap in coverage["gaps"]:
            lines.append(f"- {gap}")
    return "\n".join(lines).strip() + "\n"


def terms_by_category(scored_terms: list[dict[str, Any]], limit: int = 24) -> dict[str, list[str]]:
    categories = defaultdict(list)
    for item in scored_terms:
        for category in item.get("categories", {}):
            if len(categories[category]) < limit:
                categories[category].append(item["term"])
    return {key: dedupe_preserve(values) for key, values in categories.items()}


def compose_retrieval_queries(bundle: dict[str, Any], scored_terms: list[dict[str, Any]]) -> list[str]:
    queries = []
    seeds = bundle.get("seed_terms", [])[:4] or [bundle.get("subtopic", "")]
    for value in seeds + bundle.get("aliases", [])[:8] + bundle.get("heading_terms", [])[:10] + bundle.get("toc_terms", [])[:8]:
        add_query(queries, value)
    for value in bundle.get("tfidf_terms", [])[:14] + bundle.get("cooccurring_terms", [])[:14] + bundle.get("bigrams_trigrams", [])[:12]:
        add_query(queries, value)
        for seed in seeds[:2]:
            add_query(queries, f"{seed} {value}")
    for pair in bundle.get("acronyms", [])[:8]:
        add_query(queries, pair.get("acronym", ""))
        add_query(queries, pair.get("expansion", ""))
        add_query(queries, pair.get("term", ""))
    for item in [row for row in scored_terms if set(row.get("categories", {})) != {"source_title"}][:20]:
        add_query(queries, item["term"])
    return queries[:MAX_QUERIES]


def add_query(queries: list[str], value: Any) -> None:
    text = clean_display_text(value)
    if not text or len(text) > 140:
        return
    if not is_usable_term(text):
        return
    if normalize_label(text) and normalize_label(text) not in {normalize_label(item) for item in queries}:
        queries.append(text)


def build_tfidf_corpus_index(corpus_units: list[dict[str, Any]]) -> dict[str, Any]:
    corpus_df = Counter()
    sample_units = corpus_units
    if len(sample_units) > 1200:
        stride = max(len(sample_units) // 1200, 1)
        sample_units = sample_units[::stride]
    for unit in sample_units:
        corpus_df.update(set(fast_ngrams(" ".join([metadata(unit).get("section_path", ""), unit.get("text", "")]), sizes=(2, 3), limit=160)))
    return {"df": corpus_df, "total_docs": max(len(sample_units), 1)}


def extract_tfidf_terms(
    corpus_units: list[dict[str, Any]],
    window_units: list[dict[str, Any]],
    limit: int = 80,
    corpus_index: dict[str, Any] | None = None,
) -> dict[str, float]:
    corpus_index = corpus_index or build_tfidf_corpus_index(corpus_units)
    corpus_df = corpus_index["df"]
    window_tf = Counter()
    for unit in window_units:
        window_tf.update(fast_ngrams(" ".join([metadata(unit).get("section_path", ""), unit.get("text", "")]), sizes=(2, 3), limit=140))
    total_docs = max(int(corpus_index.get("total_docs", 1)), 1)
    scored = {}
    for term, tf in window_tf.items():
        df = corpus_df.get(term, 0)
        idf = math.log((total_docs + 1) / (df + 1)) + 1.0
        scored[term] = tf * idf
    return dict(sorted(scored.items(), key=lambda item: item[1], reverse=True)[:limit])


def fast_ngrams(text: str, sizes: tuple[int, ...] = (2, 3), limit: int = 120) -> list[str]:
    toks = token_list(text)
    phrases = []
    for size in sorted(sizes, reverse=True):
        for index in range(0, len(toks) - size + 1):
            gram = toks[index : index + size]
            if len(set(gram)) < size or not is_usable_token_phrase(gram):
                continue
            phrases.append(" ".join(gram).title())
            if len(phrases) >= limit:
                return phrases
    return phrases


def extract_ngrams(text: str, sizes: tuple[int, ...] = (2, 3), limit: int = 120) -> list[str]:
    toks = token_list(text)
    phrases = []
    for size in sorted(sizes, reverse=True):
        for index in range(0, len(toks) - size + 1):
            gram = toks[index : index + size]
            if len(set(gram)) < size or not is_usable_token_phrase(gram):
                continue
            phrase = " ".join(gram)
            label = clean_candidate_label(phrase)
            if is_usable_term(label):
                phrases.append(label)
            if len(phrases) >= limit:
                return phrases
    return phrases


def extract_repeated_noun_phrases(text: str, limit: int = 60) -> list[str]:
    phrases = Counter(extract_ngrams(text, sizes=(2, 3, 4), limit=300))
    return [phrase for phrase, count in phrases.most_common(limit) if count > 1 or len(phrase.split()) >= 3]


def extract_table_figure_terms(text: str) -> list[str]:
    terms = []
    for match in re.finditer(r"\b(?:Table|Figure)\s+\d+[A-Za-z]?[.:]?\s+(.{4,120})", text or "", flags=re.IGNORECASE):
        label = clean_candidate_label(match.group(1))
        if is_usable_term(label):
            terms.append(label)
    return dedupe_preserve(terms)


def extract_acronym_pairs(units: list[dict[str, Any]]) -> list[dict[str, str]]:
    pairs = {}
    for unit in units:
        meta = metadata(unit)
        text = " ".join([meta.get("title", ""), meta.get("section_path", ""), unit.get("text", "")[:4000]])
        for full, acronym in re.findall(r"\b([A-Z][A-Za-z/&-]+(?:\s+[A-Z][A-Za-z/&-]+){1,8})\s*\(([A-Z][A-Z0-9&/-]{1,})\)", text):
            add_acronym_pair(pairs, acronym, full)
        for acronym, full in re.findall(r"\b([A-Z][A-Z0-9&/-]{1,})\s*\(([A-Za-z][^)]{5,90})\)", text):
            add_acronym_pair(pairs, acronym, full)
    return sorted(pairs.values(), key=lambda item: item["acronym"])


def add_acronym_pair(pairs: dict[str, dict[str, str]], acronym: str, full: str) -> None:
    acronym = clean_display_text(acronym).upper()
    expansion = clean_candidate_label(full)
    if len(acronym) < 2 or not is_usable_term(expansion) or not acronym_matches_expansion(acronym, expansion):
        return
    key = acronym.lower()
    pairs[key] = {"acronym": acronym, "expansion": expansion, "term": f"{expansion} ({acronym})"}


def anchor_row(unit: dict[str, Any], index: int, score: float, reasons: list[str]) -> dict[str, Any]:
    meta = metadata(unit)
    return {
        "chunk_id": unit.get("id", ""),
        "index": index,
        "score": round(score, 4),
        "reasons": reasons,
        "source_id": meta.get("source_id", ""),
        "source_title": clean_display_text(meta.get("title", "Untitled source")),
        "publisher": clean_display_text(meta.get("publisher", "unknown")),
        "section_path": clean_display_text(meta.get("section_path", "")),
        "curation_status": unit.get("selection_status", "unmarked"),
        "text_preview": clean_display_text(unit.get("text", ""))[:500],
    }


def label_similarity(left: Any, right: Any) -> float:
    left_norm = normalize_label(left)
    right_norm = normalize_label(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    containment = 0.0
    if left_norm in right_norm or right_norm in left_norm:
        containment = min(len(left_norm), len(right_norm)) / max(len(left_norm), len(right_norm))
    return max(token_jaccard(left_norm, right_norm), SequenceMatcher(None, left_norm, right_norm).ratio(), containment)


def compile_query_terms(queries: list[str]) -> list[dict[str, Any]]:
    compiled = []
    seen = set()
    for query in queries:
        normalized = normalize_label(query)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        compiled.append({"label": clean_display_text(query), "normalized": normalized, "tokens": set(normalized.split())})
    return compiled


def text_overlap_score(queries: list[Any], text: str, precomputed_text_norm: str | None = None) -> float:
    text_norm = precomputed_text_norm if precomputed_text_norm is not None else normalize_label(text)
    if not text_norm:
        return 0.0
    text_tokens = set(text_norm.split())
    best = 0.0
    compiled = queries if queries and isinstance(queries[0], dict) else compile_query_terms(queries)
    for query in compiled:
        query_norm = query["normalized"]
        if not query_norm:
            continue
        query_tokens = query["tokens"]
        if not query_tokens:
            continue
        coverage = len(query_tokens & text_tokens) / len(query_tokens)
        containment = 1.0 if query_norm in text_norm else 0.0
        best = max(best, coverage * 0.82 + containment * 0.18)
    return best


def fast_similarity_to_compiled(normalized: str, compiled: list[dict[str, Any]]) -> float:
    if not normalized or not compiled:
        return 0.0
    tokens = set(normalized.split())
    if not tokens:
        return 0.0
    best = 0.0
    for query in compiled:
        query_tokens = query["tokens"]
        if not query_tokens:
            continue
        overlap = len(tokens & query_tokens)
        if not overlap:
            continue
        coverage = overlap / min(len(tokens), len(query_tokens))
        containment = 1.0 if normalized in query["normalized"] or query["normalized"] in normalized else 0.0
        best = max(best, coverage * 0.85 + containment * 0.15)
    return best


def terms_should_merge(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_norm = left.get("normalized", "")
    right_norm = right.get("normalized", "")
    if left_norm == right_norm:
        return True
    if token_jaccard(left_norm, right_norm) >= 0.86:
        return True
    return SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.92


def token_list(text: Any) -> list[str]:
    normalized = normalize_label(text)
    return [token for token in normalized.split() if len(token) > 1]


def top_terms_from_values(values: list[str], limit: int) -> list[str]:
    return dedupe_preserve(clean_candidate_label(value) for value in values if is_usable_term(clean_candidate_label(value)))[:limit]


def is_usable_term(label: Any) -> bool:
    raw_text = clean_display_text(label)
    if re.search(r"%[0-9A-Fa-f]{2}", raw_text):
        return False
    text = clean_candidate_label(label)
    if not is_meaningful_label(text):
        return False
    if normalize_label(text) in GENERIC_LABELS:
        return False
    tokens = token_list(text)
    if not tokens:
        return False
    if len(tokens) >= 2 and not is_usable_token_phrase(tokens):
        return False
    if any(len(token) > 28 for token in tokens):
        return False
    joined = " ".join(tokens)
    if "http" in joined or "www" in joined:
        return False
    return True


def is_usable_token_phrase(tokens: list[str]) -> bool:
    if not tokens:
        return False
    if any(token.isdigit() for token in tokens):
        return False
    if tokens[0] in BAD_BOUNDARY_STOPWORDS or tokens[-1] in BAD_BOUNDARY_STOPWORDS:
        return False
    stopword_count = sum(1 for token in tokens if token in STOPWORDS)
    if len(tokens) <= 3 and stopword_count >= 2:
        return False
    if stopword_count / max(len(tokens), 1) >= 0.5:
        return False
    return True


def acronym_matches_expansion(acronym: str, expansion: str) -> bool:
    letters = re.sub(r"[^A-Z0-9]", "", acronym.upper())
    if len(letters) < 2:
        return False
    words = [token for token in re.findall(r"[A-Za-z][A-Za-z0-9-]*", expansion) if normalize_label(token) not in STOPWORDS]
    initials = "".join(word[0].upper() for word in words)
    if not initials or len(initials) < min(len(letters), 2):
        return False
    if letters in initials:
        return True
    cursor = 0
    for char in initials:
        if cursor < len(letters) and char == letters[cursor]:
            cursor += 1
    return cursor == len(letters)


def metadata(unit: dict[str, Any]) -> dict[str, Any]:
    return unit.get("metadata") or {}


def same_source(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return bool(metadata(left).get("source_id")) and metadata(left).get("source_id") == metadata(right).get("source_id")


def add_curation_dicts(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
    keys = {"key_evidence", "keep", "maybe", "unmarked", "reject"}
    return {key: int(left.get(key, 0)) + int(right.get(key, 0)) for key in keys}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a corpus-native query bundle for one topic-tree subtopic.")
    parser.add_argument("--corpus", default="latest", help="latest, corpus id, or corpus path")
    parser.add_argument("--subtopic", default=DEFAULT_SUBTOPIC)
    parser.add_argument("--max-chunks", type=int, default=None)
    parser.add_argument("--include-maybe", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-unmarked", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-rejected", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_query_bundle_experiment(
        corpus=args.corpus,
        subtopic=args.subtopic,
        include_maybe=args.include_maybe,
        include_unmarked=args.include_unmarked,
        include_rejected=args.include_rejected,
        max_chunks=args.max_chunks,
    )
    corpus_path = Path(result["corpus"]["path"])
    print(
        "Query bundle complete: "
        f"{result['summary']['candidate_term_count']} terms, "
        f"{result['summary']['retrieval_query_count']} retrieval queries, "
        f"{result['summary']['anchor_chunk_count']} anchors"
    )
    print(corpus_path / "atlas" / "query_bundles" / "query_bundle.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
