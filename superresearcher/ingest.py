from __future__ import annotations

import html
import mimetypes
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zlib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from .config import atomic_write_text, slugify
from .search import content_hash, infer_source_type


DATA_EXTS = {".csv", ".tsv", ".xlsx", ".xls", ".json", ".zip", ".parquet", ".sav", ".dta"}


def ingest_sources(sources: list[dict[str, Any]], dossier: Path, keys: dict[str, str], progress=None, should_stop=None) -> list[dict[str, Any]]:
    originals = dossier / "originals"
    markdown = dossier / "markdown"
    assets = dossier / "assets"
    data_dir = dossier / "data"
    for path in (originals, markdown, assets, data_dir):
        path.mkdir(parents=True, exist_ok=True)
    ingested = []
    for idx, source in enumerate(sources, start=1):
        if should_stop and should_stop():
            break
        if progress and (idx == 1 or idx % 10 == 0):
            progress(f"Ingestion running: {idx}/{len(sources)} sources processed.")
        row = dict(source)
        try:
            row = ingest_one(row, idx, originals, markdown, assets, data_dir, keys)
        except Exception as exc:
            row["fetch_status"] = "failed"
            row["fetch_error"] = str(exc)[:500]
        ingested.append(row)
    return ingested


def ingest_one(
    source: dict[str, Any],
    idx: int,
    originals: Path,
    markdown_dir: Path,
    assets_dir: Path,
    data_dir: Path,
    keys: dict[str, str],
) -> dict[str, Any]:
    url = source["url"]
    source_type = infer_source_type(url, source.get("title", ""), source)
    source["source_type"] = source_type
    suffix = suffix_for(url, source_type)
    base = f"{idx:04d}-{slugify(source.get('title') or source.get('publisher') or 'source')}"
    target_dir = data_dir if suffix.lower() in DATA_EXTS else originals
    original_path = unique_path(target_dir / f"{base}{suffix}")
    content, content_type, final_url = fetch_url(url, keys)
    original_path.write_bytes(content)
    source["url"] = final_url or url
    source["local_path"] = str(original_path)
    source["content_type"] = content_type
    source["content_hash"] = content_hash(original_path)
    source["fetch_status"] = "downloaded"
    md_path = markdown_dir / f"{base}.md"
    markdown = convert_to_markdown(content, original_path, content_type, source, assets_dir, keys)
    atomic_write_text(md_path, markdown)
    source["markdown_path"] = str(md_path)
    source["markdown_status"] = "created"
    return source


def fetch_url(url: str, keys: dict[str, str]) -> tuple[bytes, str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SuperResearcher/0.2 (+local research corpus builder)",
            "Accept": "text/html,application/xhtml+xml,application/pdf,application/json,text/csv,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        content = resp.read()
        content_type = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        final_url = resp.geturl()
    return content, content_type, final_url


def firecrawl_markdown(url: str, key: str) -> str:
    import json

    payload = {"url": url, "formats": ["markdown", "html"], "onlyMainContent": True}
    req = urllib.request.Request(
        "https://api.firecrawl.dev/v1/scrape",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    if data.get("success") is False:
        return ""
    payload = data.get("data") or data
    return payload.get("markdown") or ""


def convert_to_markdown(
    content: bytes,
    original_path: Path,
    content_type: str,
    source: dict[str, Any],
    assets_dir: Path,
    keys: dict[str, str],
) -> str:
    title = source.get("title") or source.get("url", "Untitled source")
    header = [
        f"# {title}",
        "",
        f"- URL: {source.get('url', '')}",
        f"- Publisher: {source.get('publisher', '')}",
        f"- Source type: {source.get('source_type', '')}",
        f"- Original file: {original_path}",
        "",
        "---",
        "",
    ]
    suffix = original_path.suffix.lower()
    if "html" in content_type or suffix in {".html", ".htm", ""}:
        body = ""
        if keys.get("FIRECRAWL_API_KEY") and source.get("url"):
            try:
                body = firecrawl_markdown(source.get("url", ""), keys["FIRECRAWL_API_KEY"])
            except Exception:
                body = ""
        if not body:
            body = html_to_markdown(content.decode("utf-8", errors="replace"), source.get("url", ""), assets_dir)
    elif content_type == "text/markdown":
        body = content.decode("utf-8", errors="replace")
    elif "pdf" in content_type or suffix == ".pdf":
        body = pdf_to_markdown(content, original_path)
    elif suffix in DATA_EXTS or content_type in {"text/csv", "application/json"}:
        body = data_to_markdown(content, original_path)
    else:
        body = text_to_markdown(content)
    return "\n".join(header) + body.strip() + "\n"


def html_to_markdown(text: str, base_url: str, assets_dir: Path) -> str:
    parser = ReadableHTMLParser(base_url, assets_dir)
    parser.feed(text)
    lines = []
    if parser.title:
        lines.extend([f"## {parser.title}", ""])
    lines.extend(parser.lines)
    if parser.images:
        lines.extend(["", "## Extracted Images", ""])
        lines.extend(parser.images)
    return "\n".join(lines)


class ReadableHTMLParser(HTMLParser):
    def __init__(self, base_url: str, assets_dir: Path) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.assets_dir = assets_dir
        self.lines: list[str] = []
        self.images: list[str] = []
        self._tag_stack: list[str] = []
        self._href: str | None = None
        self._title_capture = False
        self._title_parts: list[str] = []
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k.lower(): v or "" for k, v in attrs}
        self._tag_stack.append(tag)
        if tag == "title":
            self._title_capture = True
        if tag in {"h1", "h2", "h3"}:
            self.lines.append("")
            self.lines.append("#" * int(tag[1]) + " ")
        if tag == "p":
            self.lines.append("")
        if tag == "li":
            self.lines.append("- ")
        if tag == "a":
            self._href = urllib.parse.urljoin(self.base_url, attrs_dict.get("href", ""))
        if tag == "img" and len(self.images) < 30:
            src = attrs_dict.get("src")
            if src:
                full = urllib.parse.urljoin(self.base_url, src)
                alt = attrs_dict.get("alt") or "image"
                self.images.append(f"![{clean_inline(alt)}]({full})")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._title_capture = False
            self.title = clean_inline(" ".join(self._title_parts))
        if tag in {"h1", "h2", "h3", "p", "li", "div", "section", "article"}:
            self.lines.append("")
        if tag == "a":
            self._href = None
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        value = clean_inline(data)
        if not value:
            return
        if self._title_capture:
            self._title_parts.append(value)
            return
        if self._tag_stack and self._tag_stack[-1] in {"script", "style", "nav", "footer"}:
            return
        if self._href and self._href.startswith(("http://", "https://")):
            self.lines.append(f"[{value}]({self._href})")
        else:
            self.lines.append(value)


def pdf_to_markdown(content: bytes, original_path: Path) -> str:
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            result = subprocess.run([pdftotext, "-layout", str(original_path), "-"], capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout.strip():
                return "## Extracted PDF Text\n\n" + result.stdout
        except Exception:
            pass
    extracted = extract_pdf_text_rough(content)
    if extracted.strip():
        return "## Extracted PDF Text\n\n" + extracted
    return "PDF preserved locally. Full text extraction was not available for this file, so it should not be treated as citation-ready until parsed by a stronger PDF extractor.\n"


def extract_pdf_text_rough(content: bytes) -> str:
    streams = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", content, flags=re.S)
    chunks: list[str] = []
    for stream in streams[:200]:
        data = stream.strip()
        for candidate in (data, try_flate(data)):
            if not candidate:
                continue
            text = extract_pdf_text_operators(candidate)
            if text:
                chunks.append(text)
                break
    if chunks:
        return "\n\n".join(chunks)
    ascii_text = re.sub(rb"[^\x09\x0A\x0D\x20-\x7E]+", b" ", content)
    return ascii_text[:200000].decode("utf-8", errors="replace")


def try_flate(data: bytes) -> bytes:
    try:
        return zlib.decompress(data)
    except Exception:
        return b""


def extract_pdf_text_operators(data: bytes) -> str:
    text = data.decode("latin-1", errors="ignore")
    parts: list[str] = []
    for block in re.findall(r"BT(.*?)ET", text, flags=re.S):
        strings = re.findall(r"\((?:\\.|[^\\)])*\)\s*Tj|\[(.*?)\]\s*TJ", block, flags=re.S)
        direct = re.findall(r"\((?:\\.|[^\\)])*\)\s*Tj", block, flags=re.S)
        for item in direct:
            parts.append(unescape_pdf_string(item[:-2].strip()))
        for array in strings:
            for raw in re.findall(r"\((?:\\.|[^\\)])*\)", array):
                parts.append(unescape_pdf_string(raw))
    cleaned = "\n".join(clean_inline(p) for p in parts if clean_inline(p))
    return cleaned


def unescape_pdf_string(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1]
    raw = raw.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
    raw = raw.replace(r"\n", "\n").replace(r"\r", "\n").replace(r"\t", "\t")
    return raw


def data_to_markdown(content: bytes, original_path: Path) -> str:
    suffix = original_path.suffix.lower()
    if suffix in {".csv", ".tsv", ".json"} or len(content) < 200000:
        sample = content[:20000].decode("utf-8", errors="replace")
        return f"## Data File Preview\n\nOriginal data file is preserved at `{original_path}`.\n\n```text\n{sample}\n```\n"
    return f"Data file preserved locally at `{original_path}`. Preview skipped because the file is large or binary.\n"


def text_to_markdown(content: bytes) -> str:
    return "## Extracted Text\n\n" + content.decode("utf-8", errors="replace")


def suffix_for(url: str, source_type: str) -> str:
    path = urllib.parse.urlsplit(url).path
    suffix = Path(path).suffix
    if suffix:
        return suffix[:16]
    if source_type == "pdf":
        return ".pdf"
    if source_type == "data":
        return ".dat"
    guessed = mimetypes.guess_extension(source_type)
    return guessed or ".html"


def looks_binary_url(url: str) -> bool:
    return Path(urllib.parse.urlsplit(url).path).suffix.lower() in DATA_EXTS | {".pdf", ".doc", ".docx", ".ppt", ".pptx"}


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for i in range(2, 10000):
        candidate = path.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find unique path for {path}")


def clean_inline(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", value or "")).strip()
