from __future__ import annotations

import re
from typing import Any

from .config import DEPTH_RESULTS, FINAL_SOURCE_DEFAULT, FINAL_SOURCE_MAX
from .llm import LLMClient
from .prompts import ARCHETYPES, RUBRIC_DIMENSIONS, classifier_prompt, rubric_prompt


def fallback_archetypes(topic: str, context: str) -> dict[str, Any]:
    text = f"{topic} {context}".lower()
    primary = "Deep Dive"
    secondary: list[str] = ["Evidence Review"]
    signals: list[str] = []
    rules = [
        (["market", "tam", "sam", "som", "opportunity", "pricing"], "Market Opportunity Assessment"),
        (["competitor", "competitive", "vendor", "startup", "company"], "Competitive Intelligence"),
        (["feasible", "feasibility", "architecture", "technical", "engineering"], "Technical Feasibility Study"),
        (["forecast", "projection", "future", "outlook"], "Market Forecast"),
        (["regulation", "policy", "legal", "compliance"], "Regulatory Analysis"),
        (["paper", "literature", "academic", "study", "scientific"], "Scientific Literature Review"),
        (["data", "dataset", "statistical", "model", "quantitative"], "Data / Dataset Analysis"),
        (["history", "historical", "prior art", "failed"], "Historical Analysis"),
        (["investment", "valuation", "thesis", "fundraising"], "Investment Thesis"),
    ]
    for terms, archetype in rules:
        if any(term in text for term in terms):
            if primary == "Deep Dive":
                primary = archetype
            elif archetype not in secondary:
                secondary.append(archetype)
            signals.append(archetype)
    if "publication" in text or "arxiv" in text or "paper" in text:
        secondary.append("Publication-Grade Research Paper")
    secondary = [a for a in dict.fromkeys(secondary) if a != primary and a in ARCHETYPES][:8]
    return {
        "primary_archetype": primary,
        "secondary_archetypes": secondary,
        "rejected_archetypes": [],
        "classification_rationale": {
            "primary_reason": "Deterministic fallback selected the closest archetype from topic and context signals.",
            "secondary_reasons": [{"archetype": a, "reason": "Relevant to source strategy or quality gates."} for a in secondary],
            "key_signals": signals or ["broad research request"],
            "ambiguities": ["Detailed downstream intent may need refinement in later phases."],
        },
        "expected_research_implications": {
            "clarification_dimensions": ["objective", "scope", "evidence standard", "source classes"],
            "source_classes": ["web", "pdf", "academic", "government", "company", "dataset", "counterevidence"],
            "quality_gates": ["source diversity", "coverage by evidence dimension", "current and historical coverage"],
            "analysis_methods": ["qualitative synthesis", "source ranking", "gap analysis"],
            "report_sections": ["overview", "evidence map", "risks", "source index"],
        },
        "user_confirmation_required": True,
    }


def fallback_rubric(payload: dict[str, Any]) -> dict[str, Any]:
    explicit = {
        "Research Objective": payload.get("objective"),
        "Decision Context": payload.get("context"),
        "Audience": payload.get("audience"),
        "Research Depth": payload.get("depth"),
        "Research Breadth": payload.get("breadth"),
        "Final Source Count": str(payload.get("final_source_count")),
        "Geographic Scope": payload.get("geographic_scope"),
        "Time Horizon": payload.get("time_horizon"),
    }
    defaults = comprehensive_defaults(payload)
    assessment = []
    counts = {"explicit": 0, "inferable": 0, "ambiguous": 0, "missing": 0, "not_applicable": 0}
    for dimension in RUBRIC_DIMENSIONS:
        value = explicit.get(dimension)
        if value:
            status = "explicit"
            known = str(value)
            default = None
        else:
            status = "inferable"
            known = defaults.get(dimension, "Use comprehensive defaults suitable for corpus creation.")
            default = known
        counts[status] += 1
        assessment.append(
            {
                "dimension": dimension,
                "rubric_question": f"Is {dimension.lower()} known enough to guide the research run?",
                "status": status,
                "known_or_inferred_value": known,
                "basis": "User settings" if status == "explicit" else "Comprehensive default policy",
                "why_it_matters": "Controls source strategy, quality gates, and downstream report utility.",
                "risk_if_unresolved": "Research may over- or under-collect sources for the intended use.",
                "downstream_impact": {
                    "clarification_questions": True,
                    "source_strategy": True,
                    "quality_gates": True,
                    "analysis_methods": True,
                    "report_structure": True,
                    "publication_quality": True,
                },
                "should_ask": False,
                "default_assumption": default,
            }
        )
    return {
        "rubric_assessment": assessment,
        "summary": {
            "explicit_count": counts["explicit"],
            "inferable_count": counts["inferable"],
            "ambiguous_count": 0,
            "missing_count": 0,
            "not_applicable_count": 0,
            "ask_count": 0,
            "highest_risk_missing_dimensions": [],
            "safe_defaults": [
                {"dimension": d, "default_assumption": v}
                for d, v in defaults.items()
                if d not in explicit or not explicit.get(d)
            ],
        },
    }


def comprehensive_defaults(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "Research Objective": payload.get("objective") or "Build a durable source corpus that can support decision-grade synthesis.",
        "Decision Context": payload.get("context") or "General strategic research and evidence gathering.",
        "Audience": payload.get("audience") or "Executives and expert reviewers who value concise, source-backed analysis.",
        "Evidence Standard": "Decision-grade, with citation-quality sources and explicit gaps.",
        "Topic Boundary": payload.get("topic_boundary") or "Include directly relevant applications, actors, risks, datasets, and counterarguments.",
        "Geographic Scope": payload.get("geographic_scope") or "Global, with geography-specific sources where the topic requires it.",
        "Time Horizon": payload.get("time_horizon") or "Current state plus forward-looking outlook over the next 3-5 years.",
        "Historical Depth": "Include historical context, predecessor attempts, and legacy terminology when useful.",
        "Currentness Requirement": "Prioritize recent sources while preserving authoritative older sources.",
        "Source Classes": "Academic, government, regulatory, datasets, standards, company primary sources, credible news, expert analysis, market reports, counterevidence.",
        "Source Preferences": "Prefer primary, authoritative, transparent, and data-rich sources.",
        "Inclusion Criteria": "Sources must materially improve coverage, evidence quality, or contradiction handling.",
        "Exclusion Criteria": "Exclude SEO spam, tabloids, duplicate summaries, broken files, and unsupported commentary.",
        "Comparison Set": "Discover relevant alternatives, competitors, benchmarks, historical analogs, or baseline scenarios.",
        "Key Variables": "Cost, adoption, performance, risk, demand, supply, regulation, maturity, and uncertainty.",
        "Analytical Methods": "Qualitative synthesis, source triangulation, market or technical analysis where supported by data.",
        "Modeling Depth": "Use explicit assumptions and reproducible calculations when quantitative claims are needed.",
        "Data Requirements": "Download actual data files where available, not only landing pages.",
        "Experiment Requirement": "Run code-based analysis later if the corpus includes suitable datasets.",
        "Success Criteria": "A reviewer can inspect what was searched, selected, rejected, and where evidence is weak.",
        "Claim Granularity": "Corpus-level source metadata now; atomic cited claims in later phases.",
        "Citation Requirement": "Every downstream factual claim should trace back to source material.",
        "Contradiction Handling": "Actively collect counterevidence and preserve unresolved disagreement.",
        "Risk Treatment": "Be conservative when evidence is weak, stale, biased, or contradictory.",
        "Assumption Policy": "Make defaults explicit and preserve them in the protocol.",
        "Stakeholder Lens": "Operator, investor, researcher, and executive lenses where relevant.",
        "Confidentiality Boundary": "Use public sources unless private context is explicitly supplied.",
        "Deliverable Constraints": "Human-readable local dossier with JSON, Markdown reports, originals, and sidecars.",
        "Follow-Up Interaction": "Autonomous run with inspectable progress and output artifacts.",
    }


def build_protocol(payload: dict[str, Any], llm: LLMClient) -> dict[str, Any]:
    topic = payload["topic"].strip()
    context = payload.get("context", "").strip()
    archetypes = llm.json_call(classifier_prompt(topic, context), fallback_archetypes(topic, context), step="Protocol: classify the topic")
    if archetypes.get("primary_archetype") not in ARCHETYPES:
        archetypes = fallback_archetypes(topic, context)
    rubric = llm.json_call(rubric_prompt(topic, context, archetypes), fallback_rubric(payload), step="Protocol: scoring rubric")
    if "rubric_assessment" not in rubric:
        rubric = fallback_rubric(payload)
    defaults = comprehensive_defaults(payload)
    depth = payload.get("depth", "high")
    breadth = payload.get("breadth", "high")
    final_source_count = int(payload.get("final_source_count") or FINAL_SOURCE_DEFAULT)
    final_source_count = min(max(final_source_count, 1), FINAL_SOURCE_MAX)
    must_include = split_list(payload.get("must_include", ""))
    must_exclude = split_list(payload.get("must_exclude", ""))
    source_classes = [
        "academic research",
        "government/regulatory",
        "datasets",
        "standards/specifications",
        "company primary sources",
        "market reports",
        "credible news",
        "expert analysis",
        "counterarguments/failure cases",
    ]
    protocol = {
        "research_topic": {"user_input": topic},
        "user_context": {"user_input": context},
        "archetypes": {
            "primary": archetypes["primary_archetype"],
            "secondary": archetypes.get("secondary_archetypes", []),
            "raw": archetypes,
        },
        "clarification": [
            {
                "dimension": row["dimension"],
                "rubric_question": row["rubric_question"],
                "assessment": row["status"],
                "question": None,
                "answer": row.get("known_or_inferred_value") or row.get("default_assumption"),
            }
            for row in rubric.get("rubric_assessment", [])
        ],
        "scope": {
            "objective": payload.get("objective") or defaults["Research Objective"],
            "research_depth": depth,
            "research_breadth": breadth,
            "decision_context": context or defaults["Decision Context"],
            "audience": payload.get("audience") or defaults["Audience"],
            "output_format": "local dossier corpus with Markdown and JSON reports",
            "evidence_standard": defaults["Evidence Standard"],
            "topic_boundary": payload.get("topic_boundary") or defaults["Topic Boundary"],
            "geographic_scope": payload.get("geographic_scope") or defaults["Geographic Scope"],
            "time_horizon": payload.get("time_horizon") or defaults["Time Horizon"],
            "historical_depth": defaults["Historical Depth"],
            "must_include": must_include,
            "must_exclude": must_exclude,
        },
        "source_requirements": {
            "required_source_classes": source_classes,
            "preferred_sources": split_list(payload.get("preferred_sources", "")),
            "disallowed_sources": split_list(payload.get("disallowed_sources", "")),
            "time_buckets": ["historical", "recent", "current", "forward-looking"],
            "source_recency_policy": defaults["Currentness Requirement"],
            "evidence_dimensions": [
                "definitions and taxonomy",
                "current state",
                "market or ecosystem",
                "technical feasibility",
                "data and benchmarks",
                "regulation and standards",
                "risks and counterevidence",
                "future outlook",
            ],
            "minimum_coverage": {
                "target_final_sources": final_source_count,
                "results_per_query": DEPTH_RESULTS.get(depth, 10),
                "minimum_independent_publishers": min(30, max(5, final_source_count // 4)),
                "required_counterevidence_sources": max(3, final_source_count // 20),
            },
        },
        "analysis_requirements": {
            "key_variables": split_list(payload.get("key_variables", "")) or [
                "cost",
                "adoption",
                "performance",
                "risk",
                "demand",
                "supply",
                "regulation",
                "technical maturity",
            ],
            "analytical_methods": ["source triangulation", "gap analysis", "scenario framing", "dataset analysis when available"],
            "modeling_depth": defaults["Modeling Depth"],
            "experiment_requirement": defaults["Experiment Requirement"],
        },
        "run_settings": {
            "depth": depth,
            "breadth": breadth,
            "results_per_query": DEPTH_RESULTS.get(depth, 10),
            "final_source_count": final_source_count,
            "max_final_source_count": FINAL_SOURCE_MAX,
            "use_comprehensive_defaults": True,
        },
        "rubric_assessment": rubric,
    }
    return protocol


def split_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if not raw:
        return []
    return [item.strip() for item in re.split(r"[,;\n]+", str(raw)) if item.strip()]
