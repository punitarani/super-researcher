from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .config import BREADTH_LEVELS, atomic_write_json, atomic_write_text
from .llm import LLMClient
from .prompts import heuristic_prompt


QUERY_KEYS = [
    "baseline_search_queries",
    "structured_search_queries",
    "analysis_ready_search_queries",
    "critical_search_queries",
    "frontier_search_queries",
]


def generate_heuristics(protocol: dict[str, Any], llm: LLMClient, out_path: Path) -> dict[str, Any]:
    breadth = protocol["scope"].get("research_breadth", "high")
    max_level = BREADTH_LEVELS.get(breadth, 5)
    levels = []
    for level in range(1, max_level + 1):
        fallback = fallback_level(level, protocol)
        generated = llm.json_call(heuristic_prompt(level, protocol), fallback)
        if not isinstance(generated, dict) or "level" not in generated:
            generated = fallback
        levels.append(generated)
    heuristics = {
        "breadth": breadth,
        "levels_generated": max_level,
        "levels": levels,
        "all_queries": collect_queries(levels),
    }
    atomic_write_json(out_path, heuristics)
    return heuristics


def fallback_level(level: int, protocol: dict[str, Any]) -> dict[str, Any]:
    topic = protocol["research_topic"]["user_input"]
    source_classes = protocol["source_requirements"]["required_source_classes"]
    base_patterns = {
        1: ["definition", "overview", "taxonomy", "landscape", "report pdf", "primer"],
        2: ["stakeholders", "ecosystem", "companies", "use cases", "value chain", "constraints"],
        3: ["dataset", "benchmark", "statistics", "metrics", "market size", "forecast data"],
        4: ["criticism", "failure", "risks", "lawsuit", "limitations", "opposition", "historical"],
        5: ["future", "emerging", "research agenda", "open problems", "scenario", "second order effects"],
    }[level]
    query_key = {
        1: "baseline_search_queries",
        2: "structured_search_queries",
        3: "analysis_ready_search_queries",
        4: "critical_search_queries",
        5: "frontier_search_queries",
    }[level]
    queries = [
        {
            "query": f"{topic} {pattern}",
            "intended_use": pattern.replace(" ", "_"),
            "expected_source_class": source_classes[i % len(source_classes)],
            "linked_item": pattern,
        }
        for i, pattern in enumerate(base_patterns)
    ]
    queries.extend(
        [
            {
                "query": f'site:.gov "{topic}" report pdf',
                "intended_use": "government_source_discovery",
                "expected_source_class": "government/regulatory",
                "linked_item": "government evidence",
            },
            {
                "query": f'site:.edu "{topic}" study pdf',
                "intended_use": "academic_source_discovery",
                "expected_source_class": "academic research",
                "linked_item": "academic evidence",
            },
            {
                "query": f'"{topic}" dataset csv OR xlsx',
                "intended_use": "dataset_discovery",
                "expected_source_class": "datasets",
                "linked_item": "data acquisition",
            },
        ]
    )
    return {
        "level": level,
        "purpose": ["baseline_orientation", "structured_domain_map", "evidence_and_analysis_map", "critical_and_historical_map", "frontier_and_synthesis_map"][level - 1],
        "canonical_topic": topic,
        "scope_interpretation": {
            "included": protocol["scope"].get("must_include", []),
            "excluded": protocol["scope"].get("must_exclude", []),
            "geographic_focus": protocol["scope"].get("geographic_scope", ""),
            "time_focus": protocol["scope"].get("time_horizon", ""),
            "historical_depth_needed": protocol["scope"].get("historical_depth", ""),
        },
        "focus_areas": [
            {
                "name": pattern,
                "why_it_matters_for_search": "Improves corpus coverage and source diversity.",
                "candidate_query_patterns": [f"{topic} {pattern}", f'"{topic}" {pattern} pdf'],
                "source_classes": source_classes,
                "evidence_dimensions": protocol["source_requirements"]["evidence_dimensions"],
            }
            for pattern in base_patterns
        ],
        query_key: queries,
    }


def collect_queries(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    rows = []
    for level in levels:
        for key in QUERY_KEYS:
            for item in level.get(key, []):
                query = item.get("query") if isinstance(item, dict) else None
                if not query:
                    continue
                normalized = re.sub(r"\s+", " ", query.strip().lower())
                if normalized in seen:
                    continue
                seen.add(normalized)
                row = dict(item)
                row["level"] = level.get("level")
                rows.append(row)
    return rows


def create_search_batches(protocol: dict[str, Any], heuristics: dict[str, Any], out_path: Path) -> dict[str, Any]:
    results_per_query = protocol["run_settings"]["results_per_query"]
    depth = protocol["run_settings"]["depth"]
    breadth = protocol["run_settings"]["breadth"]
    batches = []
    for idx, item in enumerate(heuristics["all_queries"], start=1):
        source_class = item.get("expected_source_class", "general web")
        query = item["query"]
        mechanisms = mechanisms_for(source_class, query)
        for mechanism in mechanisms:
            batches.append(
                {
                    "id": f"search-{idx:03d}-{mechanism}",
                    "query": query,
                    "focus_area": item.get("linked_item", item.get("intended_use", "general")),
                    "source_class": source_class,
                    "time_bucket": infer_time_bucket(item),
                    "evidence_dimension": item.get("intended_use", "source_discovery"),
                    "priority": priority_for(item, mechanism),
                    "search_mechanism": mechanism,
                    "fallback_mechanism": fallback_for(mechanism),
                    "expected_artifact_type": artifact_for(source_class, query),
                    "results_per_query": results_per_query,
                }
            )
    batches = sorted(batches, key=lambda x: x["priority"], reverse=True)
    plan = {
        "depth": depth,
        "breadth": breadth,
        "planned_search_count": len(batches),
        "estimated_candidate_checks": len(batches) * results_per_query,
        "batches": batches,
    }
    atomic_write_json(out_path, plan)
    return plan


def mechanisms_for(source_class: str, query: str) -> list[str]:
    q = query.lower()
    mechanisms = ["general_web_search"]
    if "pdf" in q or "report" in q:
        mechanisms.append("pdf_doc_search")
    if "academic" in source_class or "study" in q or "paper" in q:
        mechanisms.append("academic_search")
    if "government" in source_class or "regulatory" in source_class or "site:.gov" in q:
        mechanisms.append("government_regulatory_search")
    if "dataset" in source_class or "dataset" in q or "csv" in q:
        mechanisms.append("dataset_search")
    if "company" in source_class or "market" in source_class:
        mechanisms.append("company_filing_search")
    if "standards" in source_class:
        mechanisms.append("standards_patent_search")
    return list(dict.fromkeys(mechanisms))[:3]


def fallback_for(mechanism: str) -> str:
    return {
        "general_web_search": "alternate terminology and source-site search",
        "pdf_doc_search": "filetype:pdf query and direct browser fetch",
        "academic_search": "site:.edu, arXiv, Semantic Scholar, and citation chasing",
        "government_regulatory_search": "site:.gov and agency direct search",
        "dataset_search": "data portal direct search and filetype:csv/xlsx",
        "company_filing_search": "SEC/company investor relations direct search",
        "standards_patent_search": "standards body and patent database direct search",
    }.get(mechanism, "alternate terminology")


def artifact_for(source_class: str, query: str) -> str:
    q = query.lower()
    if "dataset" in source_class or "csv" in q or "xlsx" in q:
        return "data"
    if "pdf" in q or "report" in q:
        return "pdf"
    return "html_or_document"


def infer_time_bucket(item: dict[str, Any]) -> str:
    text = json.dumps(item).lower()
    if "histor" in text or "legacy" in text:
        return "historical"
    if "future" in text or "forecast" in text or "scenario" in text:
        return "forward-looking"
    if "current" in text or "latest" in text or "recent" in text:
        return "current"
    return "recent"


def priority_for(item: dict[str, Any], mechanism: str) -> int:
    score = 50
    if mechanism != "general_web_search":
        score += 15
    text = json.dumps(item).lower()
    for term in ["dataset", "pdf", "government", "academic", "risk", "criticism", "benchmark"]:
        if term in text:
            score += 5
    return min(score, 100)


def write_dedupe_report(path: Path, raw_count: int, deduped: list[dict[str, Any]], duplicate_count: int) -> None:
    by_type = Counter(item.get("source_type", "unknown") for item in deduped)
    by_publisher = Counter(item.get("publisher", "unknown") for item in deduped)
    lines = [
        "# Dedupe Report",
        "",
        f"Raw candidate discoveries: {raw_count}",
        f"Deduped candidates: {len(deduped)}",
        f"Duplicates removed or merged: {duplicate_count}",
        "",
        "## Source Types",
        "",
    ]
    lines.extend(f"- {k}: {v}" for k, v in by_type.most_common())
    lines.extend(["", "## Top Publishers", ""])
    lines.extend(f"- {k}: {v}" for k, v in by_publisher.most_common(20))
    atomic_write_text(path, "\n".join(lines) + "\n")


def write_corpus_index(path: Path, selected: list[dict[str, Any]]) -> None:
    lines = ["# Research Corpus Index", ""]
    for i, source in enumerate(selected, start=1):
        title = source.get("title") or source.get("url")
        lines.append(f"## {i}. {title}")
        lines.append("")
        lines.append(f"- URL: {source.get('url', '')}")
        lines.append(f"- Publisher: {source.get('publisher', 'unknown')}")
        lines.append(f"- Source type: {source.get('source_type', 'unknown')}")
        lines.append(f"- Discovery query: {source.get('discovery_query', '')}")
        lines.append(f"- Fetch status: {source.get('fetch_status', '')}")
        if source.get("local_path"):
            lines.append(f"- Original: {source['local_path']}")
        if source.get("markdown_path"):
            lines.append(f"- Markdown: {source['markdown_path']}")
        lines.append("")
    atomic_write_text(path, "\n".join(lines))


def write_quality_report(path: Path, protocol: dict[str, Any], selected: list[dict[str, Any]], deduped: list[dict[str, Any]]) -> dict[str, Any]:
    target = protocol["run_settings"]["final_source_count"]
    publisher_count = len({s.get("publisher") for s in selected if s.get("publisher")})
    types = Counter(s.get("source_type", "unknown") for s in selected)
    warnings = []
    failures = []
    if len(selected) < target:
        warnings.append(f"Selected {len(selected)} sources against target {target}.")
    if publisher_count < protocol["source_requirements"]["minimum_coverage"]["minimum_independent_publishers"]:
        warnings.append("Independent publisher diversity is below target.")
    if not any(t in types for t in ["pdf", "academic", "government"]):
        warnings.append("Corpus may lack high-authority PDF, academic, or government sources.")
    if not any("risk" in (s.get("evidence_dimension", "") + s.get("focus_area", "")).lower() or "critic" in json.dumps(s).lower() for s in selected):
        warnings.append("Counterargument and risk coverage may be thin.")
    if not selected:
        failures.append("No sources were selected or ingested.")
    verdict = "Fail" if failures else ("Pass with warnings" if warnings else "Pass")
    report = {
        "verdict": verdict,
        "selected_sources": len(selected),
        "deduped_candidates": len(deduped),
        "independent_publishers": publisher_count,
        "source_types": dict(types),
        "warnings": warnings,
        "failures": failures,
    }
    lines = [
        "# Corpus Quality Report",
        "",
        f"Verdict: {verdict}",
        f"Selected sources: {len(selected)}",
        f"Deduped candidates: {len(deduped)}",
        f"Independent publishers: {publisher_count}",
        "",
        "## Warnings",
        "",
    ]
    lines.extend(f"- {w}" for w in warnings or ["None"])
    lines.extend(["", "## Failures", ""])
    lines.extend(f"- {f}" for f in failures or ["None"])
    lines.extend(["", "## Source Type Mix", ""])
    lines.extend(f"- {k}: {v}" for k, v in types.most_common())
    atomic_write_text(path, "\n".join(lines) + "\n")
    return report


def select_sources(deduped: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    publisher_counts: dict[str, int] = defaultdict(int)
    max_per_publisher = max(3, target // 8)
    selected = []
    skipped = []
    for item in sorted(deduped, key=lambda s: s.get("score", 0), reverse=True):
        publisher = item.get("publisher", "unknown")
        if publisher_counts[publisher] >= max_per_publisher and len(selected) < target * 0.75:
            skipped.append(item)
            continue
        selected.append(item)
        publisher_counts[publisher] += 1
        if len(selected) >= target:
            break
    # The cap only spreads picks across publishers; if too few others exist, backfill with the best skipped ones.
    return selected + skipped[: max(0, target - len(selected))]
