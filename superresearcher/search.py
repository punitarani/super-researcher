from __future__ import annotations

import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}


def discover_candidates(
    search_plan: dict[str, Any], keys: dict[str, str], progress=None, max_batches: int | None = None, should_stop=None
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    report: dict[str, Any] = {"answered": 0, "errors": {}}
    reported: set[str] = set()
    batches = search_plan["batches"][: max_batches or len(search_plan["batches"])]
    for idx, batch in enumerate(batches, start=1):
        if should_stop and should_stop():
            break
        if progress and (idx == 1 or idx % 10 == 0):
            progress(f"Discovery running: {idx}/{len(batches)} searches checked, {len(candidates)} candidates found.")
        rows = search_batch(batch, keys, report)
        candidates.extend(rows)
        for provider, message in report["errors"].items():
            if progress and provider not in reported:
                progress(message)
            reported.add(provider)
        time.sleep(0.1)
    if report["errors"] and not report["answered"] and not (should_stop and should_stop()):
        # Every search failed (bad key, no credits, offline): say why instead of finishing with no sources.
        raise RuntimeError(" ".join(report["errors"].values()))
    return candidates


def search_batch(batch: dict[str, Any], keys: dict[str, str], report: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Search with the first configured provider that returns results.

    `report`, when given, counts provider calls that answered and keeps the first error per provider.
    """
    query = adapt_query(batch)
    limit = int(batch.get("results_per_query", 10))
    providers = [
        (name, key, provider)
        for name, key, provider in (("Exa", "EXA_API_KEY", search_exa), ("Serper", "SERPER_API_KEY", search_serper), ("SerpAPI", "SERP_API_KEY", search_serpapi))
        if keys.get(key)
    ]
    results: list[dict[str, Any]] = []
    for name, key, provider in providers:
        try:
            results = provider(query, limit, keys)
        except Exception as exc:
            if report is not None:
                report["errors"].setdefault(name, search_error_message(name, key, exc))
            continue
        if report is not None:
            report["answered"] += 1
        if results:
            break
    enriched = []
    for result in results[:limit]:
        url = result.get("url") or result.get("link")
        if not url:
            continue
        enriched.append(
            {
                "url": normalize_url(url),
                "title": clean_text(result.get("title") or result.get("text") or url),
                "publisher": publisher_from_url(url),
                "source_type": infer_source_type(url, result.get("title", ""), batch),
                "discovery_query": batch["query"],
                "search_query_used": query,
                "search_mechanism": batch["search_mechanism"],
                "focus_area": batch["focus_area"],
                "time_bucket": batch["time_bucket"],
                "evidence_dimension": batch["evidence_dimension"],
                "expected_usefulness": score_source(url, result.get("title", ""), batch),
                "artifact_type": batch["expected_artifact_type"],
                "fetch_status": "pending",
                "snippet": clean_text(result.get("snippet") or result.get("summary") or ""),
            }
        )
    return enriched


def search_error_message(name: str, key: str, exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return f"{name} search failed: the API key was rejected (HTTP {exc.code}). Check {key}."
        if exc.code in (402, 429):
            return f"{name} search failed: out of credits or rate limited (HTTP {exc.code})."
        return f"{name} search failed: HTTP {exc.code} {exc.reason}."
    if isinstance(exc, urllib.error.URLError):
        return f"{name} search failed: couldn't reach the service ({exc.reason})."
    return f"{name} search failed: {exc}"


def adapt_query(batch: dict[str, Any]) -> str:
    query = batch["query"]
    mechanism = batch["search_mechanism"]
    if mechanism == "pdf_doc_search" and "filetype:pdf" not in query.lower():
        return f"{query} filetype:pdf"
    if mechanism == "dataset_search":
        return f"{query} dataset data csv xlsx"
    if mechanism == "government_regulatory_search" and "site:.gov" not in query.lower():
        return f"{query} site:.gov OR site:.europa.eu"
    if mechanism == "academic_search":
        return f"{query} study paper pdf site:.edu OR site:arxiv.org"
    return query


def search_serper(query: str, limit: int, keys: dict[str, str]) -> list[dict[str, Any]]:
    payload = {"q": query, "num": limit}
    req = urllib.request.Request(
        "https://google.serper.dev/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"X-API-KEY": keys["SERPER_API_KEY"], "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    rows = data.get("organic", []) + data.get("news", [])
    return [
        {"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet")}
        for r in rows
        if r.get("link")
    ]


def search_serpapi(query: str, limit: int, keys: dict[str, str]) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "engine": "google",
            "q": query,
            "num": limit,
            "api_key": keys["SERP_API_KEY"],
        }
    )
    with urllib.request.urlopen(f"https://serpapi.com/search.json?{params}", timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    rows = data.get("organic_results", []) + data.get("news_results", [])
    return [
        {"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet")}
        for r in rows
        if r.get("link")
    ]


def search_exa(query: str, limit: int, keys: dict[str, str]) -> list[dict[str, Any]]:
    payload = {"query": query, "numResults": limit, "type": "auto"}
    req = urllib.request.Request(
        "https://api.exa.ai/search",
        data=json.dumps(payload).encode("utf-8"),
        headers={"x-api-key": keys["EXA_API_KEY"], "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return [
        {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("text") or r.get("summary")}
        for r in data.get("results", [])
        if r.get("url")
    ]


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if k.lower() not in TRACKING_PARAMS]
    netloc = parsed.netloc.lower()
    scheme = parsed.scheme or "https"
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((scheme, netloc, path, urllib.parse.urlencode(query), ""))


def publisher_from_url(url: str) -> str:
    netloc = urllib.parse.urlsplit(url).netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def infer_source_type(url: str, title: str, batch: dict[str, Any] | None = None) -> str:
    lower = f"{url} {title} {json.dumps(batch or {})}".lower()
    if lower.endswith(".pdf") or ".pdf" in urllib.parse.urlsplit(url).path.lower() or "filetype:pdf" in lower:
        return "pdf"
    if any(ext in lower for ext in [".csv", ".xlsx", ".xls", ".zip", ".json"]):
        return "data"
    if "arxiv.org" in lower or "doi.org" in lower or "academic" in lower or "study" in lower:
        return "academic"
    if ".gov" in lower or "regulatory" in lower:
        return "government"
    if "standards" in lower or "patent" in lower:
        return "standards_or_patent"
    if "market" in lower or "company" in lower or "annual report" in lower:
        return "company_or_market"
    return "html"


def score_source(url: str, title: str, batch: dict[str, Any]) -> int:
    lower = f"{url} {title} {json.dumps(batch)}".lower()
    score = 40
    boosts = {
        ".gov": 20,
        ".edu": 16,
        "arxiv.org": 18,
        "doi.org": 18,
        "filetype:pdf": 8,
        ".pdf": 10,
        "dataset": 10,
        "report": 8,
        "study": 8,
        "standard": 8,
        "annual": 6,
        "sec.gov": 14,
        "criticism": 6,
        "risk": 6,
    }
    penalties = {"blog": -5, "sponsored": -8, "press release": -6}
    for term, boost in boosts.items():
        if term in lower:
            score += boost
    for term, penalty in penalties.items():
        if term in lower:
            score += penalty
    return max(1, min(score, 100))


def dedupe_candidates(candidates: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    seen: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for item in candidates:
        key = dedupe_key(item)
        if key in seen:
            duplicate_count += 1
            existing = seen[key]
            existing.setdefault("duplicate_discoveries", []).append(
                {
                    "query": item.get("discovery_query"),
                    "mechanism": item.get("search_mechanism"),
                }
            )
            existing["expected_usefulness"] = max(existing.get("expected_usefulness", 0), item.get("expected_usefulness", 0))
            existing["score"] = max(existing.get("score", 0), score_candidate(item))
        else:
            row = dict(item)
            row["dedupe_key"] = key
            row["score"] = score_candidate(item)
            seen[key] = row
    return list(seen.values()), duplicate_count


def dedupe_key(item: dict[str, Any]) -> str:
    url = normalize_url(item.get("url", ""))
    doi = extract_doi(url + " " + item.get("title", ""))
    if doi:
        return f"doi:{doi.lower()}"
    arxiv = extract_arxiv(url)
    if arxiv:
        return f"arxiv:{arxiv}"
    title = re.sub(r"[^a-z0-9]+", " ", item.get("title", "").lower()).strip()
    if title:
        return f"title:{title[:140]}"
    return f"url:{url}"


def extract_doi(text: str) -> str | None:
    match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, re.I)
    return match.group(0).rstrip(".") if match else None


def extract_arxiv(text: str) -> str | None:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", text, re.I)
    return match.group(1) if match else None


def score_candidate(item: dict[str, Any]) -> int:
    return max(item.get("expected_usefulness", 0), score_source(item.get("url", ""), item.get("title", ""), item))


def clean_text(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", str(value or ""))).strip()


def content_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
