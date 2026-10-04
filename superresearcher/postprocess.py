from __future__ import annotations

import json
import re
import threading
import time
import traceback
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import atomic_write_json, atomic_write_text
from .doc_convert import convert_file_to_markdown, detect_magic, pdf_via_pymupdf, pdf_via_pymupdf4llm, preferred_suffix, source_identifier, unique_path
from .search import content_hash


BOT_PATTERNS = re.compile(
    r"before you continue|unusual traffic|verify you are human|captcha|cloudflare|access denied|enable javascript|please enable cookies",
    re.I,
)
RAW_PDF_TOKEN_PATTERN = re.compile(r"\b(?:\d+\s+\d+\s+obj|endobj|stream|endstream|xref)\b|%PDF-|/FlateDecode")
MARKDOWN_POSTPROCESS_JOBS: dict[str, "MarkdownPostprocessJob"] = {}


EXTRACTED_PDF_HEADING = "## Extracted PDF Text"  # what ingest writes above plain PDF text


def markdown_readability_dependency_status() -> dict[str, Any]:
    modules = {}
    for module in ("pymupdf4llm", "fitz"):
        try:
            __import__(module)
            modules[module] = True
        except Exception:
            modules[module] = False
    return {
        "python": modules,
        "ready": modules["pymupdf4llm"] or modules["fitz"],
        "install_commands": [
            "python3 -m venv .venv",
            ".venv/bin/python -m pip install -r requirements-atlas.txt",
        ],
    }


class MarkdownPostprocessJob:
    def __init__(self, corpus: Path) -> None:
        self.corpus = corpus.resolve()
        self.job_id = f"postprocess-{self.corpus.name}-{int(time.time())}"
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
        MARKDOWN_POSTPROCESS_JOBS[self.job_id] = self
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
            result = postprocess_markdown_readability(self.corpus, progress=self.event)
            with self._lock:
                self.status["state"] = "completed"
                self.status["stage"] = "Post-processing complete"
                self.status["progress"] = 100
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")
                self.status["counts"].update(result)
        except Exception as exc:
            log_path = self.corpus / "logs" / "markdown-postprocess-error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(traceback.format_exc(), encoding="utf-8")
            with self._lock:
                self.status["state"] = "failed"
                self.status["error"] = str(exc)
                self.status["completed_at"] = datetime.now().isoformat(timespec="seconds")


def start_markdown_postprocess_job(corpus: Path) -> MarkdownPostprocessJob:
    corpus = corpus.resolve()
    for job in MARKDOWN_POSTPROCESS_JOBS.values():
        snap = job.snapshot()
        if snap["corpus_path"] == str(corpus) and snap["state"] in {"queued", "running"}:
            return job
    job = MarkdownPostprocessJob(corpus)
    job.start()
    return job


def get_markdown_postprocess_job(job_id: str) -> MarkdownPostprocessJob | None:
    return MARKDOWN_POSTPROCESS_JOBS.get(job_id)


def postprocess_markdown_readability(corpus: Path, progress=None) -> dict[str, int]:
    ingested_path = corpus / "ingested_sources.jsonl"
    selected_path = corpus / "selected_sources.jsonl"
    rows = read_jsonl(ingested_path)
    selected_rows = read_jsonl(selected_path)
    summary = {
        "scanned": 0,
        "pdf_scanned": 0,
        "flagged": 0,
        "reconverted": 0,
        "reflowed": 0,
        "unchanged": 0,
        "failed": 0,
        "atlas_invalidated": 0,
    }
    if progress:
        progress("Scanning Markdown sidecars", 5, **summary)
    updated_rows: list[dict[str, Any]] = []
    updated_by_key: dict[str, dict[str, Any]] = {}
    total = max(1, len(rows))
    for index, row in enumerate(rows, start=1):
        processed = postprocess_markdown_row(corpus, row)
        updated_rows.append(processed["row"])
        for key in row_match_keys(processed["row"]):
            updated_by_key[key] = processed["row"]
        for key in ("scanned", "pdf_scanned", "flagged", "reconverted", "reflowed", "unchanged", "failed"):
            summary[key] += int(processed.get(key, 0))
        if progress and (index == 1 or index % 10 == 0 or index == len(rows)):
            pct = 5 + int((index / total) * 80)
            progress("Repairing damaged PDF Markdown", pct, **summary)
    if summary["reconverted"] or summary["reflowed"]:
        write_jsonl(ingested_path, updated_rows)
        if selected_path.exists():
            write_jsonl(selected_path, [merge_updated_row(row, updated_by_key) for row in selected_rows])
        invalidate_atlas_outputs(corpus)
        summary["atlas_invalidated"] = 1
    if progress:
        progress("Validating readability fixes", 90, **summary)
    return summary


def postprocess_markdown_row(corpus: Path, row: dict[str, Any]) -> dict[str, Any]:
    result = {"row": dict(row), "scanned": 0, "pdf_scanned": 0, "flagged": 0, "reconverted": 0, "reflowed": 0, "unchanged": 0, "failed": 0}
    md_path = Path(row.get("markdown_path") or "")
    local_path = Path(row.get("local_path") or "")
    if not md_path.exists():
        return result
    result["scanned"] = 1
    if not is_pdf_source(row, local_path):
        result["unchanged"] = 1
        return result
    result["pdf_scanned"] = 1
    text = md_path.read_text(encoding="utf-8", errors="replace")
    header, body = split_markdown_sidecar(text)
    metrics = readability_metrics(body)
    flat = lacks_headings(body)
    if not metrics["damaged"] and not flat:
        result["unchanged"] = 1
        return result
    result["flagged"] = 1
    asset_dir = corpus / "assets" / source_identifier(local_path, row)
    asset_dir.mkdir(parents=True, exist_ok=True)
    for method, converter in (("pymupdf4llm", pdf_via_pymupdf4llm), ("pymupdf_blocks", pdf_via_pymupdf)):
        if not local_path.exists() or detect_magic(local_path) != "pdf":
            break
        try:
            candidate = normalize_pdf_asset_links(converter(local_path, asset_dir), asset_dir).strip()
        except Exception:
            continue
        if candidate_is_better(body, candidate) or (flat and adds_headings(body, candidate)):
            atomic_write_text(md_path, header + candidate + "\n")
            result["row"]["conversion_notes"] = append_note(row, f"postprocess_{method}_reconverted")
            result["reconverted"] = 1
            return result
    if not metrics["damaged"]:
        # Readable, just without headings, and no converter could add them (e.g. pymupdf4llm isn't installed).
        result["unchanged"] = 1
        return result
    cleaned = cleanup_newline_damage(body)
    if candidate_is_better(body, cleaned):
        atomic_write_text(md_path, header + cleaned.strip() + "\n")
        result["row"]["conversion_notes"] = append_note(row, "postprocess_newline_reflowed")
        result["reflowed"] = 1
        return result
    result["failed"] = 1
    result["row"]["conversion_notes"] = append_note(row, "postprocess_newline_repair_failed")
    return result


def lacks_headings(body: str) -> bool:
    """True for PDF text saved without the document's own headings, which topic discovery needs."""
    return not any(line.startswith("#") and line.strip() != EXTRACTED_PDF_HEADING for line in body.splitlines())


def adds_headings(old_body: str, candidate_body: str) -> bool:
    """A clean conversion that brings back headings without losing much of the text."""
    if lacks_headings(candidate_body):
        return False
    old, new = readability_metrics(old_body), readability_metrics(candidate_body)
    return not new["damaged"] and new["alnum_count"] >= old["alnum_count"] * 0.45


def is_pdf_source(row: dict[str, Any], local_path: Path) -> bool:
    text = f"{row.get('source_type', '')} {row.get('content_type', '')} {row.get('url', '')} {local_path.suffix}".lower()
    return "pdf" in text


def split_markdown_sidecar(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    for idx, line in enumerate(lines[:60]):
        if line.strip() == "---":
            return "\n".join(lines[: idx + 1]).rstrip() + "\n\n", "\n".join(lines[idx + 1 :]).strip()
    return "", text.strip()


def readability_metrics(text: str) -> dict[str, Any]:
    lines = prose_metric_lines(text)
    nonempty = [line.strip() for line in lines if line.strip()]
    words = len(re.findall(r"\b[\w'-]+\b", "\n".join(nonempty)))
    avg_len = sum(len(line) for line in nonempty) / max(1, len(nonempty))
    tiny_ratio = sum(1 for line in nonempty if len(line) <= 2) / max(1, len(nonempty))
    short_ratio = sum(1 for line in nonempty if len(line) <= 4) / max(1, len(nonempty))
    lines_per_word = len(nonempty) / max(1, words)
    nul_count = text.count("\x00")
    control_count = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    visible_count = sum(1 for char in text if not char.isspace())
    control_ratio = control_count / max(1, visible_count)
    binary_corrupt = nul_count > 0 or (control_count >= 40 and control_ratio >= 0.01)
    raw_pdf = raw_pdf_corruption_metrics(text)
    newline_damaged = len(nonempty) > 80 and (
        (tiny_ratio >= 0.50 and avg_len <= 10)
        or (short_ratio >= 0.72 and avg_len <= 14)
        or (short_ratio >= 0.90 and avg_len <= 16)
        or (lines_per_word >= 0.58 and avg_len <= 16)
    )
    damaged = binary_corrupt or newline_damaged or raw_pdf["raw_pdf_corrupt"]
    damage_score = tiny_ratio * 2.0 + short_ratio + lines_per_word + max(0.0, (24.0 - avg_len) / 24.0)
    if binary_corrupt:
        damage_score += 5.0 + min(5.0, control_ratio * 20.0)
    if raw_pdf["raw_pdf_corrupt"]:
        damage_score += 8.0 + min(5.0, raw_pdf["raw_pdf_density"] * 100.0)
    return {
        "line_count": len(nonempty),
        "word_count": words,
        "avg_line_length": avg_len,
        "tiny_ratio": tiny_ratio,
        "short_ratio": short_ratio,
        "lines_per_word": lines_per_word,
        "alnum_count": len(re.findall(r"[A-Za-z0-9]", text)),
        "nul_count": nul_count,
        "control_count": control_count,
        "control_ratio": control_ratio,
        "binary_corrupt": binary_corrupt,
        "raw_pdf_corrupt": raw_pdf["raw_pdf_corrupt"],
        "raw_pdf_marker_count": raw_pdf["raw_pdf_marker_count"],
        "raw_pdf_object_count": raw_pdf["raw_pdf_object_count"],
        "raw_pdf_stream_count": raw_pdf["raw_pdf_stream_count"],
        "raw_pdf_density": raw_pdf["raw_pdf_density"],
        "newline_damaged": newline_damaged,
        "damage_score": damage_score,
        "damaged": damaged,
    }


def raw_pdf_corruption_metrics(text: str) -> dict[str, Any]:
    sample = text[:200000]
    line_count = max(1, sample.count("\n") + 1)
    marker_count = len(RAW_PDF_TOKEN_PATTERN.findall(sample))
    object_count = len(re.findall(r"\b\d+\s+\d+\s+obj\b", sample))
    stream_count = len(re.findall(r"\bstream\b", sample))
    endstream_count = len(re.findall(r"\bendstream\b", sample))
    endobj_count = len(re.findall(r"\bendobj\b", sample))
    has_pdf_header = "%PDF-" in sample
    has_xref = bool(re.search(r"\bxref\b|/Type/XRef", sample))
    flate_count = sample.count("/FlateDecode")
    density = (object_count + stream_count + endstream_count + endobj_count) / line_count
    raw_pdf_corrupt = (
        (has_pdf_header and (object_count >= 1 or stream_count >= 1))
        or (flate_count >= 2 and endstream_count >= 2)
        or (object_count >= 8 and stream_count >= 5 and density >= 0.015)
        or (endobj_count >= 5 and endstream_count >= 3 and marker_count >= 10)
        or (has_xref and object_count >= 5 and stream_count >= 3)
    )
    return {
        "raw_pdf_corrupt": raw_pdf_corrupt,
        "raw_pdf_marker_count": marker_count,
        "raw_pdf_object_count": object_count,
        "raw_pdf_stream_count": stream_count,
        "raw_pdf_density": density,
    }


def prose_metric_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not stripped or in_fence or is_structural_markdown_line(stripped):
            continue
        lines.append(stripped)
    return lines


def is_structural_markdown_line(line: str) -> bool:
    return bool(
        line.startswith(("#", ">", "![", "|"))
        or re.match(r"^[-*+]\s+", line)
        or re.match(r"^\d+[.)]\s+", line)
    )


def candidate_is_better(old_body: str, candidate_body: str) -> bool:
    candidate = candidate_body.strip()
    if len(candidate) < 100:
        return False
    old = readability_metrics(old_body)
    new = readability_metrics(candidate)
    if old["raw_pdf_corrupt"]:
        min_retention = 0.10
    elif old["binary_corrupt"]:
        min_retention = 0.25
    else:
        min_retention = 0.45
    if old["alnum_count"] > 1000 and new["alnum_count"] < old["alnum_count"] * min_retention:
        return False
    return (not new["damaged"] and new["damage_score"] < old["damage_score"] * 0.75) or new["damage_score"] < old["damage_score"] * 0.45


def normalize_pdf_asset_links(text: str, asset_dir: Path) -> str:
    link = f"../assets/{asset_dir.name}".replace(" ", "%20")
    normalized = text
    paths = {str(asset_dir), str(asset_dir.resolve()), str(asset_dir.absolute())}
    for path in paths:
        normalized = normalized.replace(path, link)
        normalized = normalized.replace(path.replace(" ", "%20"), link)
    asset_name = re.escape(asset_dir.name)
    normalized = re.sub(
        rf"\((?:file://)?[^)\n]*?/assets/{asset_name}/([^)\n]+)\)",
        lambda match: f"({link}/{Path(urllib.parse.unquote(match.group(1))).name})",
        normalized,
    )
    normalized = re.sub(r"\(/private(\.\./assets/[^)\n]+)\)", r"(\1)", normalized)
    return normalized


def cleanup_newline_damage(text: str) -> str:
    blocks: list[str] = []
    prose: list[str] = []
    in_fence = False

    def flush_prose() -> None:
        nonlocal prose
        if prose:
            blocks.append(reflow_prose_group(prose))
            prose = []

    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            flush_prose()
            in_fence = not in_fence
            blocks.append(raw)
        elif in_fence or is_structural_markdown_line(stripped):
            flush_prose()
            blocks.append(raw.rstrip())
        elif not stripped:
            flush_prose()
            if blocks and blocks[-1] != "":
                blocks.append("")
        else:
            prose.append(stripped)
    flush_prose()
    cleaned = "\n".join(blocks)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def reflow_prose_group(lines: list[str]) -> str:
    metrics = readability_metrics("\n".join(lines))
    if metrics["damaged"] or metrics["avg_line_length"] < 18:
        return fragments_to_words(lines)
    return soft_wrap_lines(lines)


def soft_wrap_lines(lines: list[str]) -> str:
    text = ""
    for line in lines:
        if not text:
            text = line
        elif text.endswith("-") and line[:1].islower():
            text = text[:-1] + line
        else:
            text += " " + line
    return re.sub(r"\s+", " ", text).strip()


def fragments_to_words(lines: list[str]) -> str:
    words: list[str] = []
    current = ""
    for token in [line.strip() for line in lines if line.strip()]:
        if " " in token or len(token) > 16:
            if current:
                words.append(current)
                current = ""
            words.extend(token.split())
            continue
        if re.fullmatch(r"[^\w]+", token):
            current = (current or "").rstrip() + token
            continue
        if not current:
            current = token
        elif should_join_fragment(current, token):
            current += token
        else:
            words.append(current)
            current = token
    if current:
        words.append(current)
    text = " ".join(words)
    text = re.sub(r"\s+([,.;:%)])", r"\1", text)
    text = re.sub(r"([(])\s+", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def should_join_fragment(current: str, token: str) -> bool:
    if current.endswith("-"):
        return True
    if current.isupper() and token.isupper() and len(current) < 8 and len(token) <= 2:
        return True
    if current.isupper() and re.match(r"^[A-Z][a-z]", token):
        return False
    if current[-1:].islower() and token[:1].isupper():
        return False
    if len(current) == 1 and current.isupper() and token[:1].islower():
        return True
    if current[-1:].islower() and token[:1].islower():
        return True
    if len(token) <= 2 and token.islower():
        return True
    return len(current) < 14 and len(token) <= 3 and token.isalpha()


def append_note(row: dict[str, Any], note: str) -> list[str]:
    notes = list(row.get("conversion_notes") or [])
    return notes if note in notes else notes + [note]


def row_match_keys(row: dict[str, Any]) -> set[str]:
    return {value for value in (row.get("url"), row.get("local_path"), row.get("markdown_path"), row.get("content_hash")) if value}


def merge_updated_row(row: dict[str, Any], updated_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in row_match_keys(row):
        if key in updated_by_key:
            return updated_by_key[key]
    return row


def invalidate_atlas_outputs(corpus: Path) -> None:
    atlas_dir = corpus / "atlas"
    for name in ("manifest.json", "points.jsonl", "chunks.jsonl"):
        path = atlas_dir / name
        if path.exists():
            path.unlink()


def postprocess_research_runs(root: Path, refetch: bool = True) -> dict[str, Any]:
    root = root.resolve()
    summary = {
        "root": str(root),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "corpora": [],
        "renamed": 0,
        "refetched": 0,
        "deleted": 0,
        "removed_records": 0,
        "markdown_regenerated": 0,
        "still_failed": 0,
    }
    for corpus in sorted(p for p in root.iterdir() if p.is_dir() and p.name.endswith("_Corpus")):
        corpus_summary = process_corpus(corpus, refetch=refetch)
        summary["corpora"].append(corpus_summary)
        for key in ("renamed", "refetched", "deleted", "removed_records", "markdown_regenerated", "still_failed"):
            summary[key] += corpus_summary[key]
    summary["completed_at"] = datetime.now().isoformat(timespec="seconds")
    append_repair_summary(root / "corpus_validation_report.md", summary)
    return summary


def process_corpus(corpus: Path, refetch: bool = True) -> dict[str, Any]:
    summary = {
        "corpus": str(corpus),
        "renamed": 0,
        "refetched": 0,
        "deleted": 0,
        "removed_records": 0,
        "markdown_regenerated": 0,
        "still_failed": 0,
        "actions": [],
    }
    ingested_path = corpus / "ingested_sources.jsonl"
    selected_path = corpus / "selected_sources.jsonl"
    if not ingested_path.exists():
        return summary
    rows = read_jsonl(ingested_path)
    selected = read_jsonl(selected_path)
    retained: list[dict[str, Any]] = []
    removed_urls: set[str] = set()
    deleted_paths: set[Path] = set()
    for row in rows:
        status = row.get("fetch_status")
        local_path = Path(row["local_path"]) if row.get("local_path") else None
        if status != "downloaded" or not local_path or not local_path.exists():
            recovered = targeted_refetch(row, corpus) if refetch and confident_refetch_candidate(row) else None
            if recovered:
                row.update(recovered)
                summary["refetched"] += 1
                summary["actions"].append(f"refetched:{row.get('title') or row.get('url')}")
                local_path = Path(row["local_path"])
            else:
                removed_urls.add(row.get("url", ""))
                summary["removed_records"] += 1
                summary["actions"].append(f"removed_missing:{row.get('title') or row.get('url')}")
                continue
        assert local_path is not None
        kind = detect_magic(local_path)
        if expected_pdf_payload(row, local_path) and kind != "pdf":
            recovered = targeted_refetch(row, corpus) if refetch and confident_refetch_candidate(row) else None
            if recovered:
                old = local_path
                row.update(recovered)
                local_path = Path(row["local_path"])
                if old.exists() and old != local_path:
                    safe_unlink(old, corpus)
                    deleted_paths.add(old)
                summary["refetched"] += 1
                summary["actions"].append(f"refetched_wrong_pdf:{row.get('title') or row.get('url')}")
            else:
                delete_source_files(row, corpus, deleted_paths)
                removed_urls.add(row.get("url", ""))
                summary["deleted"] += 1
                summary["removed_records"] += 1
                summary["actions"].append(f"deleted_corrupt:{local_path.name}")
                continue
        local_path, renamed = normalize_extension(local_path)
        if renamed:
            row["local_path"] = str(local_path)
            summary["renamed"] += 1
            summary["actions"].append(f"renamed:{renamed.name}->{local_path.name}")
        markdown_path = markdown_path_for(row, corpus, local_path)
        conversion = convert_file_to_markdown(local_path, row, markdown_path, corpus / "assets")
        row["markdown_path"] = conversion["markdown_path"]
        row["markdown_status"] = "created"
        row["local_path"] = str(local_path)
        row["content_hash"] = content_hash(local_path)
        row["content_type"] = content_type_for(local_path)
        row["source_type"] = source_type_for(local_path, row)
        if conversion.get("conversion_notes"):
            row["conversion_notes"] = conversion["conversion_notes"]
        summary["markdown_regenerated"] += 1
        retained.append(row)
    write_jsonl(ingested_path, retained)
    if selected_path.exists():
        write_jsonl(selected_path, retained)
    write_corpus_index(corpus / "research_corpus_index.md", retained)
    update_run_and_summary(corpus, retained)
    summary["still_failed"] = count_still_failed(retained)
    return summary


def normalize_extension(path: Path) -> tuple[Path, Path | None]:
    suffix = preferred_suffix(path)
    if suffix == path.suffix.lower():
        return path, None
    target = unique_path(path.with_suffix(suffix))
    path.rename(target)
    return target, path


def markdown_path_for(row: dict[str, Any], corpus: Path, original_path: Path) -> Path:
    current = row.get("markdown_path")
    if current:
        path = Path(current)
        if path.parent.exists():
            return path
    return corpus / "markdown" / f"{original_path.stem}.md"


def confident_refetch_candidate(row: dict[str, Any]) -> bool:
    url = row.get("url", "")
    error = row.get("fetch_error", "")
    text = f"{url} {row.get('title', '')} {row.get('source_type', '')} {error}".lower()
    direct_signals = [".pdf", "bitstream", "/content", "download", "download_pub", "ntrs.nasa.gov", "rosap.ntl.bts.gov"]
    blocked_domains = ["academia.edu", "sciencedirect.com", "mdpi.com"]
    if any(domain in text for domain in blocked_domains) and ("403" in text or "forbidden" in text):
        return False
    return any(signal in text for signal in direct_signals)


def expected_pdf_payload(row: dict[str, Any], local_path: Path) -> bool:
    text = f"{local_path.name} {row.get('url', '')} {row.get('title', '')} {row.get('source_type', '')} {row.get('artifact_type', '')}".lower()
    return local_path.suffix.lower() == ".pdf" or ".pdf" in text or " pdf" in text or "filetype:pdf" in text


def targeted_refetch(row: dict[str, Any], corpus: Path) -> dict[str, Any] | None:
    urls = candidate_urls(row)
    if row.get("local_path"):
        local = Path(row["local_path"])
        if local.exists() and detect_magic(local) == "html":
            urls.extend(extract_pdf_links(local.read_text(encoding="utf-8", errors="replace"), row.get("url", "")))
    for url in dict.fromkeys(urls):
        try:
            content, content_type, final_url = fetch_direct(url)
        except Exception:
            continue
        if not content:
            continue
        expected_pdf = "pdf" in (row.get("source_type", "") + row.get("url", "") + row.get("title", "")).lower()
        if expected_pdf and not content.startswith(b"%PDF-"):
            continue
        target_dir = corpus / "originals"
        base = Path(row.get("local_path") or "").stem or "refetched-source"
        suffix = ".pdf" if content.startswith(b"%PDF-") else ".html" if b"<html" in content[:4096].lower() else ".dat"
        target = unique_path(target_dir / f"{base}{suffix}")
        target.write_bytes(content)
        return {
            "url": final_url,
            "local_path": str(target),
            "content_type": content_type,
            "content_hash": content_hash(target),
            "fetch_status": "downloaded",
            "fetch_error": None,
        }
    return None


def candidate_urls(row: dict[str, Any]) -> list[str]:
    url = row.get("url", "")
    if not url:
        return []
    urls = [url]
    decoded = urllib.parse.unquote(url)
    if decoded != url:
        urls.append(decoded)
    if "%3F" in url or "%3D" in url or "%26" in url:
        urls.append(decoded.replace(" ", "%20"))
    return urls


def fetch_direct(url: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 SuperResearcherPostProcessor/0.2",
            "Accept": "application/pdf,text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read(), resp.headers.get("Content-Type", "").split(";")[0].lower(), resp.geturl()


def extract_pdf_links(text: str, base_url: str) -> list[str]:
    links = re.findall(r"""href=["']([^"']+\.pdf(?:\?[^"']*)?)["']""", text, flags=re.I)
    return [urllib.parse.urljoin(base_url, html_unescape(link)) for link in links]


def html_unescape(value: str) -> str:
    return value.replace("&amp;", "&")


def delete_source_files(row: dict[str, Any], corpus: Path, deleted_paths: set[Path]) -> None:
    for key in ("local_path", "markdown_path"):
        raw = row.get(key)
        if raw:
            path = Path(raw)
            if path.exists():
                safe_unlink(path, corpus)
                deleted_paths.add(path)


def safe_unlink(path: Path, corpus: Path) -> None:
    path = path.resolve()
    corpus = corpus.resolve()
    if corpus not in path.parents:
        raise RuntimeError(f"Refusing to delete outside corpus: {path}")
    path.unlink()


def content_type_for(path: Path) -> str:
    kind = detect_magic(path)
    return {
        "pdf": "application/pdf",
        "html": "text/html",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "zip": "application/zip",
    }.get(kind, "application/octet-stream")


def source_type_for(path: Path, row: dict[str, Any]) -> str:
    kind = detect_magic(path)
    if kind == "pdf":
        return "pdf"
    if kind == "html":
        return row.get("source_type") if row.get("source_type") not in {"pdf", "data"} else "html"
    return row.get("source_type") or kind


def count_still_failed(rows: list[dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        path = Path(row.get("local_path", ""))
        if not path.exists() or row.get("fetch_status") != "downloaded":
            count += 1
        elif path.suffix.lower() == ".pdf" and detect_magic(path) != "pdf":
            count += 1
    return count


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
    tmp.replace(path)


def write_corpus_index(path: Path, selected: list[dict[str, Any]]) -> None:
    lines = ["# Research Corpus Index", ""]
    for i, source in enumerate(selected, start=1):
        title = source.get("title") or source.get("url")
        lines.extend([f"## {i}. {title}", ""])
        lines.append(f"- URL: {source.get('url', '')}")
        lines.append(f"- Publisher: {source.get('publisher', 'unknown')}")
        lines.append(f"- Source type: {source.get('source_type', 'unknown')}")
        lines.append(f"- Discovery query: {source.get('discovery_query', '')}")
        lines.append(f"- Fetch status: {source.get('fetch_status', '')}")
        if source.get("local_path"):
            lines.append(f"- Original: {source['local_path']}")
        if source.get("markdown_path"):
            lines.append(f"- Markdown: {source['markdown_path']}")
        if source.get("conversion_notes"):
            lines.append(f"- Conversion notes: {', '.join(source['conversion_notes'])}")
        lines.append("")
    atomic_write_text(path, "\n".join(lines))


def update_run_and_summary(corpus: Path, retained: list[dict[str, Any]]) -> None:
    downloaded = sum(1 for row in retained if row.get("fetch_status") == "downloaded")
    for name in ("run.json",):
        path = corpus / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        data.setdefault("counts", {})["selected_sources"] = len(retained)
        data.setdefault("counts", {})["ingested_sources"] = downloaded
        atomic_write_json(path, data)


def append_repair_summary(report_path: Path, summary: dict[str, Any]) -> None:
    existing = report_path.read_text(encoding="utf-8", errors="replace") if report_path.exists() else "# Corpus File Validation Report\n"
    marker = "\n## Post-Processing Repair Summary\n"
    if marker in existing:
        existing = existing.split(marker)[0].rstrip() + "\n"
    lines = [
        "## Post-Processing Repair Summary",
        "",
        f"Completed: {summary['completed_at']}",
        "",
        "| Metric | Count |",
        "|---|---:|",
        f"| Renamed by content type | {summary['renamed']} |",
        f"| Targeted re-fetches recovered | {summary['refetched']} |",
        f"| Corrupted files deleted | {summary['deleted']} |",
        f"| Records removed from usable corpus | {summary['removed_records']} |",
        f"| Markdown sidecars regenerated | {summary['markdown_regenerated']} |",
        f"| Still failed after repair | {summary['still_failed']} |",
        "",
    ]
    for corpus in summary["corpora"]:
        lines.extend([f"Corpus: `{corpus['corpus']}`", ""])
        if corpus["actions"]:
            action_counts = Counter(action.split(":", 1)[0] for action in corpus["actions"])
            for action, count in action_counts.most_common():
                lines.append(f"- {action}: {count}")
        else:
            lines.append("- No changes required.")
        lines.append("")
    atomic_write_text(report_path, existing.rstrip() + "\n\n" + "\n".join(lines))
