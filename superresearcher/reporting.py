from __future__ import annotations

import base64
import html as html_lib
import mimetypes
import re
import shutil
import subprocess
import sysconfig
import tempfile
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import publish, query_bundles
from .config import atomic_write_json, atomic_write_text, slugify


REPORT_VERSION = "report-export-v1"
DEFAULT_TEMPLATE_ID = "mckinsey"
PRODUCT_NAME = "Challenger Deep"
ROOT = Path(__file__).resolve().parents[1]
LATEX_ORIGINAL_TEMPLATE_ID = "latex_original"
REPORTING_VENV = ROOT / ".reporting-venv"
INTER_FONT_URL = "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf"
INTER_FONT_PATH = ROOT / "web" / "assets" / "fonts" / "Inter-Variable.ttf"
META_FONT_CANDIDATES = ("Optimistic Display", "Optimistic Text", "Meta Sans")
_FONT_REGISTRY_CACHE: set[str] | None = None
REPORT_FORMATS = {
    "pdf": {"id": "pdf", "label": "PDF", "extension": "pdf"},
    "html": {"id": "html", "label": "HTML", "extension": "html"},
    "docx": {"id": "docx", "label": "DOCX", "extension": "docx"},
    "latex": {"id": "latex", "label": "LaTeX", "extension": "tex"},
}

PANDOC_FROM = "markdown+pipe_tables+fenced_divs+raw_html+tex_math_dollars+superscript+bracketed_spans"
CITATION_RE = re.compile(r"\[S(?P<source_id>[A-Za-z0-9_-]+):C(?P<chunk_id>[^\]]+)\]")
METADATA_FIELDS = ("title", "subtitle", "authors", "organization", "date", "website", "logo_path", "cover_image_path")
REFERENCE_CSS = """
a[href^="#ref-"] sup { font-size: .72em; margin-left: .08em; text-decoration: none; }
.reference-list { margin-top: 12px; padding-top: 12px; border-top: 1px solid #e5e7eb; }
.reference-list ol { padding-left: 1.45rem; }
.reference-list li { margin: 0 0 9px; line-height: 1.48; }
.reference-list a { overflow-wrap: anywhere; }
span[id^="ref-"] { scroll-margin-top: 24px; }
"""
RICH_REPORT_CSS = """
html { background: #f3f5f7; }
body.rich-report-page { margin: 0; padding: 0; max-width: none; background: #f3f5f7; color: #111827; }
.rich-report { min-height: 100vh; background: #fff; }
.report-cover { position: relative; display: grid; gap: 28px; padding: 58px clamp(30px, 7vw, 88px) 42px; overflow: hidden; }
.report-brand-row { display: flex; align-items: center; justify-content: space-between; gap: 18px; min-height: 34px; }
.report-logo { max-width: 170px; max-height: 54px; object-fit: contain; }
.report-logo-text { font-weight: 700; letter-spacing: .01em; color: inherit; }
.report-kicker { margin: 0; font-size: 12px; line-height: 1.25; text-transform: uppercase; letter-spacing: .12em; color: #64748b; }
.report-title-block h1 { margin: 10px 0 0; max-width: 980px; font-family: inherit; letter-spacing: 0; }
.report-subtitle { max-width: 820px; margin: 16px 0 0; color: #475569; font-size: 20px; line-height: 1.38; }
.report-byline { margin-top: 18px; font-weight: 700; }
.report-metadata { display: flex; flex-wrap: wrap; gap: 11px 22px; margin: 22px 0 0; color: #475569; font-size: 13px; }
.report-metadata div { display: inline-flex; gap: 6px; align-items: baseline; }
.report-metadata dt { margin: 0; font-weight: 800; color: #111827; }
.report-metadata dd { margin: 0; }
.report-opening { max-width: 920px; }
.report-opening h2 { margin: 0 0 12px; border: 0; padding: 0; }
.report-opening ul { margin: 0; padding-left: 1.15rem; }
.report-opening li { margin-bottom: 8px; }
.report-toc { margin: 0 auto; padding: 28px clamp(30px, 7vw, 88px); max-width: 1040px; }
.report-toc h2 { margin: 0 0 14px; padding: 0; border: 0; }
.report-toc ol { margin: 0; padding-left: 1.2rem; }
.report-toc li { margin: 5px 0; }
.report-toc ul { margin: 5px 0 12px; padding-left: 1.2rem; }
.report-body { max-width: 980px; margin: 0 auto; padding: 34px clamp(30px, 7vw, 88px) 70px; }
.report-chapter { margin-top: 54px; }
.report-chapter-title { margin: 0 0 22px; letter-spacing: 0; }
.report-section { margin-top: 32px; }
.report-section-title { margin: 0 0 12px; letter-spacing: 0; }
.report-section-body p { margin: 0 0 14px; }
.report-section-body ul, .report-section-body ol { margin-top: 0; }
.report-exhibit { margin: 28px 0; border: 1px solid #e2e8f0; background: #fff; }
.report-exhibit img { display: block; width: 100%; max-width: 100%; margin: 0; border: 0; border-radius: 0; padding: 0; background: transparent; }
.report-exhibit figcaption { padding: 10px 14px; color: #475569; font-size: 12.5px; line-height: 1.35; border-top: 1px solid #e2e8f0; background: #f8fafc; }
.report-references { max-width: 980px; margin: 0 auto; padding: 34px clamp(30px, 7vw, 88px) 72px; border-top: 1px solid #e5e7eb; }
.report-references h2 { margin-top: 0; }
.report-references ol { padding-left: 1.35rem; }
.report-references li { margin: 0 0 10px; overflow-wrap: anywhere; }
.template-meta_research { max-width: 1060px; margin: 0 auto; padding-top: 26px; box-sizing: border-box; font-family: "Optimistic Text", "Meta Sans", "Challenger Inter", Inter, "Helvetica Neue", Arial, sans-serif; color: #1f2933; background: #fff; font-weight: 400; }
.template-meta_research h1, .template-meta_research h2, .template-meta_research h3, .template-meta_research .report-logo-text, .template-meta_research .report-kicker, .template-meta_research .report-byline, .template-meta_research .report-metadata { font-family: "Optimistic Display", "Meta Sans", "Challenger Inter", Inter, Arial, Helvetica, sans-serif; }
.template-meta_research .report-cover { display: block; box-sizing: border-box; margin: 0 auto 26px; max-width: 1000px; padding: 28px 24px 26px; border: 0; border-radius: 14px; background: #f1f3f5; overflow: visible; }
.template-meta_research .report-brand-row { justify-content: flex-start; min-height: 32px; margin-bottom: 28px; }
.template-meta_research .report-logo { max-width: 128px; max-height: 38px; }
.template-meta_research .report-logo-text { display: inline-flex; align-items: center; gap: 10px; color: #111827; font-size: 18px; line-height: 1; font-weight: 700; letter-spacing: 0; }
.template-meta_research .wordmark-emoji { font-size: 22px; line-height: 1; }
.template-meta_research .report-kicker { display: none; }
.template-meta_research .report-title-block h1 { max-width: 760px; margin: 0; font-size: clamp(16px, 2.5vw, 24px); line-height: 1.16; font-weight: 700; color: #050505; }
.template-meta_research .report-subtitle { max-width: 760px; margin-top: 12px; color: #3f4852; font-family: "Optimistic Text", "Meta Sans", "Challenger Inter", Inter, "Helvetica Neue", Arial, sans-serif; font-size: 18px; line-height: 1.32; font-weight: 400; }
.template-meta_research .report-byline { margin-top: 18px; color: #111827; font-size: 16px; font-weight: 600; }
.template-meta_research .report-metadata { display: grid; gap: 5px; margin-top: 26px; color: #111827; font-size: 14px; }
.template-meta_research .report-metadata div { display: flex; gap: 7px; align-items: baseline; }
.template-meta_research .report-metadata dt { color: #050505; font-weight: 800; }
.template-meta_research .report-metadata dd { color: #27313b; }
.template-meta_research .report-opening { max-width: none; margin-top: 22px; padding: 0; border: 0; border-radius: 0; background: transparent; color: #39434d; font-family: "Optimistic Text", "Meta Sans", "Challenger Inter", Inter, "Helvetica Neue", Arial, sans-serif; font-size: 10px; line-height: 1.28; font-weight: 400; }
.template-meta_research .report-opening h2 { display: none; }
.template-meta_research .report-opening p { margin: 0 0 5px; font-size: 10px; line-height: 1.28; text-align: justify; text-justify: inter-word; hyphens: auto; }
.template-meta_research .report-opening p:last-child { margin-bottom: 0; }
.template-meta_research .report-toc { display: none; }
.template-meta_research .report-body, .template-meta_research .report-references { max-width: 1000px; padding-left: 17px; padding-right: 17px; font-family: "Optimistic Text", "Meta Sans", "Challenger Inter", Inter, "Helvetica Neue", Arial, sans-serif; font-size: 12.5px; line-height: 1.45; font-weight: 400; }
.template-meta_research .report-body { padding-top: 0; }
.template-meta_research .report-body p, .template-meta_research .report-body li, .template-meta_research .report-references li { font-size: 12.5px; line-height: 1.45; font-weight: 400; text-align: justify; text-justify: inter-word; hyphens: auto; }
.template-meta_research .report-body strong, .template-meta_research .report-references strong { font-weight: 600; }
.template-meta_research a { color: #0866ff; }
.template-meta_research .report-chapter { margin-top: 26px; }
.template-meta_research .report-chapter-title { margin-bottom: 12px; border: 0; padding-top: 0; color: #111827; font-family: "Optimistic Display", "Meta Sans", "Challenger Inter", Inter, Arial, Helvetica, sans-serif; font-size: 21px; line-height: 1.14; font-weight: 700; }
.template-meta_research .report-section { margin-top: 18px; }
.template-meta_research .report-section-title { color: #111827; font-family: "Optimistic Display", "Meta Sans", "Challenger Inter", Inter, Arial, Helvetica, sans-serif; font-size: 15.5px; line-height: 1.18; font-weight: 700; }
.template-meta_research .report-section-body p { margin-bottom: 9px; }
.template-meta_research .report-exhibit { margin: 26px auto; border: 0; background: transparent; }
.template-meta_research .report-exhibit img { border: 0; border-radius: 0; }
.template-meta_research .report-exhibit figcaption { padding: 8px 0 0; border: 0; background: transparent; color: #333b45; font-size: 11.5px; }
.template-meta_research table { width: 100%; border-collapse: collapse; border-top: 2px solid #111827; border-bottom: 2px solid #111827; font-size: 11.5px; }
.template-meta_research th, .template-meta_research td { padding: 7px 6px; border: 0; border-bottom: 1px solid #b8c0ca; text-align: left; }
.template-meta_research th { background: transparent; color: #111827; font-weight: 760; }
.template-meta_research .report-references { border-top: 1px solid #cfd4da; }
.template-mckinsey { font-family: Arial, Helvetica, sans-serif; background: #f4f6f8; color: #1d252d; }
.template-mckinsey .report-cover, .template-mckinsey .report-toc, .template-mckinsey .report-body, .template-mckinsey .report-references { padding-left: 24px; padding-right: 24px; }
.template-mckinsey .report-cover { min-height: 520px; color: #fff; background-color: #062445; background-image: linear-gradient(115deg, rgba(38, 84, 255, .94), rgba(4, 26, 49, .94)), var(--cover-image, none); background-size: cover; background-position: center; align-content: start; }
.template-mckinsey .report-logo-text { font-family: Georgia, "Times New Roman", serif; font-size: 25px; line-height: .98; font-weight: 500; white-space: pre-line; }
.template-mckinsey .report-title-block h1 { font-family: Georgia, "Times New Roman", serif; font-size: clamp(38px, 6vw, 70px); line-height: .98; font-weight: 500; color: #fff; max-width: 920px; }
.template-mckinsey .report-kicker { color: rgba(255,255,255,.62); }
.template-mckinsey .report-subtitle, .template-mckinsey .report-metadata, .template-mckinsey .report-metadata dt { color: rgba(255,255,255,.86); }
.template-mckinsey .report-subtitle { font-size: 15px; }
.template-mckinsey .report-metadata { display: flex; gap: 24px; align-items: flex-start; max-width: 760px; font-size: 11.5px; }
.template-mckinsey .report-metadata div { display: inline-flex; gap: 8px; min-width: 132px; }
.template-mckinsey .report-metadata dt, .template-mckinsey .report-metadata dd { white-space: nowrap; }
.template-mckinsey a { color: #9be7ff; }
.template-mckinsey .report-opening { margin-top: 24px; padding: 18px; background: rgba(255,255,255,.12); border-left: 6px solid #45c7ff; backdrop-filter: blur(4px); }
.template-mckinsey .report-toc { background: #e9ecef; max-width: none; display: grid; grid-template-columns: minmax(180px, 260px) minmax(0, 1fr); gap: 28px; }
.template-mckinsey .report-body { max-width: 1000px; column-count: 2; column-gap: 38px; font-size: 12px; }
.template-mckinsey .report-chapter { break-inside: avoid; margin-top: 42px; }
.template-mckinsey .report-chapter-title { column-span: all; font-family: Georgia, "Times New Roman", serif; font-size: 34px; line-height: 1.02; margin-bottom: 22px; }
.template-mckinsey .report-section-title { font-size: 14px; color: #0f3b66; }
.template-mckinsey .report-exhibit { column-span: all; border: 0; border-top: 5px solid #2452ff; box-shadow: 0 18px 42px rgba(15,23,42,.12); }
.template-mckinsey .report-exhibit figcaption { background: #0b1f38; color: #fff; border: 0; }
.template-iclr { font-family: Georgia, "Times New Roman", serif; color: #111; background: #fff; }
.template-iclr .report-cover, .template-iclr .report-toc, .template-iclr .report-body, .template-iclr .report-references { padding-left: 18px; padding-right: 18px; }
.template-iclr .report-cover { max-width: 900px; margin: 0 auto; padding-top: 32px; text-align: center; border-bottom: 1px solid #d8d8d8; }
.template-iclr .report-title-block h1 { font-size: 22px; line-height: 1.12; margin-left: auto; margin-right: auto; }
.template-iclr .report-subtitle, .template-iclr .report-byline, .template-iclr .report-metadata { font-size: 11.5px; }
.template-iclr .report-opening { text-align: left; border: 1px solid #d8d8d8; background: #fafafa; padding: 12px 14px; margin: 8px auto 0; font-size: 11.5px; }
.template-iclr .report-body, .template-iclr .report-toc, .template-iclr .report-references { max-width: 900px; font-size: 12px; }
.template-iclr .report-chapter-title { font-size: 15.5px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
.template-iclr .report-section-title { font-size: 12.5px; font-style: italic; }
.template-neurips { font-family: "Times New Roman", Times, serif; color: #161616; background: #fff; }
.template-neurips .report-cover, .template-neurips .report-toc, .template-neurips .report-body, .template-neurips .report-references { padding-left: 18px; padding-right: 18px; }
.template-neurips .report-cover { max-width: 900px; margin: 0 auto; padding-top: 30px; text-align: center; }
.template-neurips .report-title-block h1 { font-size: 21px; line-height: 1.08; margin-left: auto; margin-right: auto; }
.template-neurips .report-subtitle, .template-neurips .report-byline, .template-neurips .report-metadata { font-size: 11.5px; }
.template-neurips .report-opening { text-align: left; margin: 8px auto 0; max-width: 760px; padding: 0 22px; border-left: 0; font-size: 11.5px; }
.template-neurips .report-opening h2 { text-align: center; font-family: Arial, Helvetica, sans-serif; font-size: 12px; }
.template-neurips .report-body, .template-neurips .report-toc, .template-neurips .report-references { max-width: 900px; font-size: 12px; }
.template-neurips .report-chapter-title { font-family: Arial, Helvetica, sans-serif; font-size: 14.5px; color: #2f4b7c; }
.template-neurips .report-section-title { font-family: Arial, Helvetica, sans-serif; font-size: 12.5px; }
.template-nature_review { font-family: Georgia, "Times New Roman", serif; color: #1f2933; background: #fbfbfa; }
.template-nature_review .report-cover, .template-nature_review .report-toc, .template-nature_review .report-body, .template-nature_review .report-references { padding-left: 20px; padding-right: 20px; }
.template-nature_review .report-cover { max-width: 960px; margin: 0 auto; padding-top: 34px; border-top: 5px solid #b31b1b; }
.template-nature_review .report-title-block h1 { font-size: clamp(26px, 4vw, 40px); line-height: 1.08; }
.template-nature_review .report-subtitle, .template-nature_review .report-byline, .template-nature_review .report-metadata { font-size: 12px; }
.template-nature_review .report-opening { border-top: 3px solid #b31b1b; border-bottom: 1px solid #d7d7d7; padding: 14px 0; font-size: 12px; }
.template-nature_review .report-body, .template-nature_review .report-toc, .template-nature_review .report-references { max-width: 960px; font-size: 12.5px; }
.template-nature_review .report-chapter-title { color: #b31b1b; font-size: 19px; }
.template-ieee_acm { font-family: "Times New Roman", Times, serif; color: #111; background: #fff; }
.template-ieee_acm .report-cover, .template-ieee_acm .report-toc, .template-ieee_acm .report-body, .template-ieee_acm .report-references { padding-left: 16px; padding-right: 16px; }
.template-ieee_acm .report-cover { max-width: 900px; margin: 0 auto; padding-top: 24px; text-align: center; border-bottom: 1px solid #444; }
.template-ieee_acm .report-title-block h1 { font-size: 20px; line-height: 1.08; margin-left: auto; margin-right: auto; color: #111; }
.template-ieee_acm .report-kicker { color: #111; text-transform: none; letter-spacing: 0; font-size: 11px; }
.template-ieee_acm .report-subtitle, .template-ieee_acm .report-byline, .template-ieee_acm .report-metadata { font-size: 11px; }
.template-ieee_acm .report-metadata { justify-content: center; gap: 18px; }
.template-ieee_acm .report-metadata div { gap: 5px; min-width: 104px; white-space: nowrap; }
.template-ieee_acm .report-body, .template-ieee_acm .report-toc, .template-ieee_acm .report-references { max-width: 900px; font-size: 11.5px; }
.template-ieee_acm .report-body { column-count: 2; column-gap: 24px; }
.template-ieee_acm .report-chapter-title { column-span: all; font-size: 13.5px; text-transform: none; color: #111; }
.template-ieee_acm .report-section-title { font-size: 12px; color: #111; }
.template-ieee_acm a { color: #111; }
@media (max-width: 760px) {
  .report-cover, .report-toc, .report-body, .report-references { padding-left: 22px; padding-right: 22px; }
  .template-mckinsey .report-cover { min-height: 520px; }
  .template-mckinsey .report-body, .template-ieee_acm .report-body { column-count: 1; }
  .template-mckinsey .report-toc { display: block; }
  .template-mckinsey .report-chapter-title { font-size: 36px; }
}
"""


REPORT_TEMPLATES: dict[str, dict[str, Any]] = {
    "mckinsey": {
        "id": "mckinsey",
        "name": "McKinsey",
        "description": "Executive report with strong hierarchy, dense exhibits, and restrained consulting-style typography.",
        "layout_id": "consulting-report",
        "cover_treatment": "dark-blue-cover",
        "opening_block": "insight-card",
        "body_flow": "two-column-executive",
        "figure_treatment": "exhibit",
        "table_treatment": "consulting-grid",
        "citation_style": "numbered-superscript",
        "accent": "#145c64",
        "font_stack": "Arial, Helvetica, sans-serif",
        "heading_font_stack": "Arial, Helvetica, sans-serif",
        "body_font_stack": "Arial, Helvetica, sans-serif",
        "summary_block": "insight-card",
        "toc_depth": 2,
        "chapter_numbering": True,
        "max_width": "940px",
        "geometry": "margin=0.35in",
        "fontsize": "9pt",
        "css": """
@page { size: A4; margin: 0.35in 0.32in 0.38in; }
body { font-family: Arial, Helvetica, sans-serif; color: #111827; background: #eef2f3; margin: 0 auto; max-width: 980px; padding: 34px 18px 54px; }
h1 { color: #0f2f38; font-size: 30px; line-height: 1.04; margin: 0 0 12px; padding-bottom: 16px; border-bottom: 6px solid #145c64; }
h1 + p, .report-kicker { color: #52616b; font-size: 11px; text-transform: uppercase; letter-spacing: .07em; }
h2 { color: #0f2f38; font-size: 18px; line-height: 1.14; margin-top: 32px; padding-top: 12px; border-top: 1px solid #145c64; }
h3 { color: #182b33; font-size: 14px; line-height: 1.22; margin-top: 20px; }
h4, h5, h6 { color: #334155; }
p, li { font-size: 12px; line-height: 1.48; }
.template-summary-box { background: #ffffff; border: 1px solid #cbd9dc; border-left: 6px solid #145c64; padding: 16px 18px; margin: 20px 0; box-shadow: 0 14px 34px rgba(15, 47, 56, .10); }
.template-summary-box h2 { border: 0; margin-top: 0; padding-top: 0; }
.report-toc { background: #f8fbfb; border: 1px solid #d3e0e3; padding: 14px 16px; margin: 20px 0; }
.report-toc h2 { border: 0; margin-top: 0; padding-top: 0; }
blockquote { border-left: 3px solid #145c64; margin-left: 0; padding-left: 14px; color: #334155; background: #e6eff1; }
img { max-width: 100%; display: block; margin: 16px auto 7px; border: 1px solid #d8e0e3; background: #fff; padding: 6px; }
em { color: #52616b; }
table { width: 100%; border-collapse: collapse; font-size: 11px; background: #fff; }
th, td { border-bottom: 1px solid #d8e0e3; padding: 6px 5px; text-align: left; }
th { color: #0f2f38; background: #eef4f5; }
""",
    },
    "iclr": {
        "id": "iclr",
        "name": "ICLR",
        "description": "Academic paper style with compact single-column flow and neutral serif typography.",
        "layout_id": "academic-single-column",
        "cover_treatment": "centered-title",
        "opening_block": "abstract",
        "body_flow": "single-column-paper",
        "figure_treatment": "academic-figure",
        "table_treatment": "academic-table",
        "citation_style": "numbered-superscript",
        "accent": "#111827",
        "font_stack": "Georgia, 'Times New Roman', serif",
        "heading_font_stack": "Georgia, 'Times New Roman', serif",
        "body_font_stack": "Georgia, 'Times New Roman', serif",
        "summary_block": "abstract",
        "toc_depth": 2,
        "chapter_numbering": True,
        "max_width": "760px",
        "geometry": "margin=0.35in",
        "fontsize": "9pt",
        "css": """
@page { size: A4; margin: 0.35in 0.32in 0.38in; }
body { font-family: Georgia, "Times New Roman", serif; color: #111; background: #fff; max-width: 900px; margin: 0 auto; padding: 30px 16px 52px; }
h1 { font-size: 22px; text-align: center; line-height: 1.12; margin-bottom: 20px; }
h2 { font-size: 15.5px; margin-top: 24px; border-bottom: 1px solid #ddd; padding-bottom: 4px; }
h3 { font-size: 12.5px; margin-top: 18px; font-style: italic; }
p, li { font-size: 12px; line-height: 1.48; }
.template-summary-box { border: 1px solid #d7d7d7; background: #fafafa; padding: 12px 14px; margin: 18px 0; }
.template-summary-box h2 { border: 0; margin-top: 0; padding-bottom: 0; text-align: center; font-size: 12.5px; }
.report-toc { border-top: 1px solid #d7d7d7; border-bottom: 1px solid #d7d7d7; padding: 10px 0; margin: 18px 0; }
img { max-width: 94%; display: block; margin: 14px auto 7px; }
em { color: #444; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 10.5px; }
""",
    },
    "neurips": {
        "id": "neurips",
        "name": "NeurIPS",
        "description": "ML proceedings-inspired layout with crisp headings and compact evidence density.",
        "layout_id": "academic-compact",
        "cover_treatment": "centered-title",
        "opening_block": "indented-abstract",
        "body_flow": "compact-paper",
        "figure_treatment": "academic-figure",
        "table_treatment": "compact-table",
        "citation_style": "numbered-superscript",
        "accent": "#2f4b7c",
        "font_stack": "'Times New Roman', Times, serif",
        "heading_font_stack": "Arial, Helvetica, sans-serif",
        "body_font_stack": "'Times New Roman', Times, serif",
        "summary_block": "abstract",
        "toc_depth": 2,
        "chapter_numbering": True,
        "max_width": "780px",
        "geometry": "margin=0.35in",
        "fontsize": "9pt",
        "css": """
@page { size: A4; margin: 0.35in 0.32in 0.38in; }
body { font-family: "Times New Roman", Times, serif; color: #161616; background: #fff; max-width: 900px; margin: 0 auto; padding: 28px 16px 52px; }
h1 { font-size: 21px; line-height: 1.08; text-align: center; margin-bottom: 18px; }
h2 { font-family: Arial, Helvetica, sans-serif; font-size: 14.5px; color: #2f4b7c; margin-top: 22px; }
h3 { font-family: Arial, Helvetica, sans-serif; font-size: 12.5px; color: #333; margin-top: 16px; }
p, li { font-size: 12px; line-height: 1.45; }
.template-summary-box { background: #f6f7fb; border: 1px solid #dce2f2; border-left: 4px solid #2f4b7c; padding: 12px 14px; margin: 18px 0; }
.template-summary-box h2 { margin-top: 0; }
.report-toc { background: #fbfcff; border: 1px solid #e2e7f2; padding: 12px 14px; margin: 18px 0; }
img { max-width: 96%; display: block; margin: 13px auto 7px; }
em { color: #4b5563; }
""",
    },
    "meta_research": {
        "id": "meta_research",
        "name": "Meta Research",
        "description": "Meta paper-inspired front matter with gray abstract panel, Meta-like wordmark treatment, and compact research typography.",
        "layout_id": "meta-paper",
        "cover_treatment": "gray-paper-front-matter",
        "opening_block": "meta-paper-abstract",
        "body_flow": "paper-single-column",
        "figure_treatment": "paper-figure",
        "table_treatment": "paper-horizontal-rules",
        "citation_style": "numbered-superscript",
        "accent": "#0866ff",
        "font_stack": "'Optimistic Text', 'Meta Sans', 'Challenger Inter', Inter, 'Helvetica Neue', Arial, sans-serif",
        "heading_font_stack": "'Optimistic Display', 'Meta Sans', 'Challenger Inter', Inter, Arial, Helvetica, sans-serif",
        "body_font_stack": "'Optimistic Text', 'Meta Sans', 'Challenger Inter', Inter, 'Helvetica Neue', Arial, sans-serif",
        "summary_block": "meta-paper-abstract",
        "toc_depth": 2,
        "chapter_numbering": True,
        "max_width": "900px",
        "geometry": "margin=0.45in",
        "fontsize": "10pt",
        "css": """
@page { size: A4; margin: 0.45in 0.38in 0.48in; }
body { font-family: "Optimistic Text", "Meta Sans", "Challenger Inter", Inter, "Helvetica Neue", Arial, sans-serif; color: #1f2933; background: #ffffff; max-width: 1000px; margin: 0 auto; padding: 40px 17px 64px; font-weight: 400; }
h1, h2, h3 { font-family: "Optimistic Display", "Meta Sans", "Challenger Inter", Inter, Arial, Helvetica, sans-serif; }
h1 { font-size: 30px; line-height: 1.08; color: #050505; margin: 0 0 14px; font-weight: 700; }
h1 + p, .report-kicker { color: #65676b; font-family: "Optimistic Display", "Meta Sans", "Challenger Inter", Inter, Arial, Helvetica, sans-serif; font-size: 11.5px; }
h2 { font-size: 21px; color: #111827; margin-top: 28px; padding-top: 0; border-top: 0; font-weight: 700; }
h3 { font-size: 15.5px; color: #111827; margin-top: 18px; font-weight: 700; }
p, li { font-size: 12.5px; line-height: 1.45; font-weight: 400; text-align: justify; text-justify: inter-word; hyphens: auto; }
strong { font-weight: 600; }
.template-summary-box, .meta-summary-box { background: #f1f3f5; border: 0; border-radius: 14px; padding: 26px 28px; margin: 32px 0; }
.template-summary-box h2, .meta-summary-box h2 { border: 0; margin-top: 0; padding-top: 0; color: #050505; }
.template-summary-box strong, .meta-summary-box strong { color: #111827; }
.meta-paper-abstract { background: #f1f3f5; border-radius: 14px; padding: 24px 28px; margin: 28px 0; color: #39434d; }
.template-meta_research .meta-paper-abstract p { font-size: 10px; line-height: 1.28; }
.report-toc { display: none; }
img { max-width: 100%; display: block; margin: 18px auto 7px; border: 0; border-radius: 0; }
table { width: 100%; border-collapse: collapse; border-top: 2px solid #111827; border-bottom: 2px solid #111827; font-size: 11.5px; }
th, td { border: 0; border-bottom: 1px solid #b8c0ca; padding: 7px 6px; text-align: left; }
th { background: transparent; color: #111827; }
em { color: #65676b; }
a { color: #0866ff; }
""",
    },
    "nature_review": {
        "id": "nature_review",
        "name": "Nature Review",
        "description": "Editorial review style with measured serif text, red accents, and readable long-form pacing.",
        "layout_id": "editorial-review",
        "cover_treatment": "editorial-title",
        "opening_block": "editorial-summary",
        "body_flow": "longform-review",
        "figure_treatment": "editorial-figure",
        "table_treatment": "editorial-table",
        "citation_style": "numbered-superscript",
        "accent": "#b31b1b",
        "font_stack": "Georgia, 'Times New Roman', serif",
        "heading_font_stack": "Georgia, 'Times New Roman', serif",
        "body_font_stack": "Georgia, 'Times New Roman', serif",
        "summary_block": "editorial",
        "toc_depth": 2,
        "chapter_numbering": True,
        "max_width": "800px",
        "geometry": "margin=0.35in",
        "fontsize": "9pt",
        "css": """
@page { size: A4; margin: 0.35in 0.32in 0.38in; }
body { font-family: Georgia, "Times New Roman", serif; color: #1f2933; background: #fbfbfa; max-width: 960px; margin: 0 auto; padding: 32px 18px 54px; }
h1 { font-size: 25px; line-height: 1.1; color: #111; margin-bottom: 16px; }
h2 { font-size: 17px; color: #b31b1b; margin-top: 24px; }
h3 { font-size: 13.5px; color: #3f3f46; margin-top: 18px; }
p, li { font-size: 12.5px; line-height: 1.5; }
.template-summary-box { background: #fff; border-top: 3px solid #b31b1b; border-bottom: 1px solid #d7d7d7; padding: 14px 0; margin: 20px 0; }
.template-summary-box h2 { margin-top: 0; }
.report-toc { background: #fff; border: 1px solid #dedbd6; padding: 13px 15px; margin: 20px 0; }
blockquote { border-left: 3px solid #b31b1b; padding-left: 12px; margin-left: 0; color: #4b5563; }
img { max-width: 98%; display: block; margin: 15px auto 7px; }
em { color: #5f6368; }
""",
    },
    "ieee_acm": {
        "id": "ieee_acm",
        "name": "IEEE/ACM Technical",
        "description": "Dense engineering paper style with compact headings and technical citation emphasis.",
        "layout_id": "technical-two-column",
        "cover_treatment": "technical-title",
        "opening_block": "technical-abstract",
        "body_flow": "two-column-technical",
        "figure_treatment": "technical-figure",
        "table_treatment": "technical-grid",
        "citation_style": "numbered-superscript",
        "accent": "#315f9a",
        "font_stack": "'Times New Roman', Times, serif",
        "heading_font_stack": "'Times New Roman', Times, serif",
        "body_font_stack": "'Times New Roman', Times, serif",
        "summary_block": "technical",
        "toc_depth": 2,
        "chapter_numbering": True,
        "max_width": "780px",
        "geometry": "margin=0.32in",
        "fontsize": "8.8pt",
        "css": """
@page { size: A4; margin: 0.32in 0.28in 0.35in; }
body { font-family: "Times New Roman", Times, serif; color: #111; background: #fff; max-width: 920px; margin: 0 auto; padding: 24px 14px 46px; }
h1 { font-size: 19px; text-align: center; line-height: 1.08; margin-bottom: 16px; color: #111; }
h1 + p, .report-kicker { color: #111; text-transform: none; letter-spacing: 0; font-size: 11px; }
h2 { font-size: 13.5px; color: #111; text-transform: none; margin-top: 20px; }
h3 { font-size: 12px; color: #111; margin-top: 15px; }
p, li { font-size: 11.5px; line-height: 1.38; }
.template-summary-box { border: 1px solid #bfc8d8; background: #f8faff; padding: 10px 12px; margin: 16px 0; }
.template-summary-box h2 { margin-top: 0; }
.report-toc { column-count: 2; column-gap: 22px; border-top: 1px solid #bfc8d8; border-bottom: 1px solid #bfc8d8; padding: 10px 0; margin: 16px 0; }
.report-toc h2 { column-span: all; margin-top: 0; }
img { max-width: 96%; display: block; margin: 12px auto 6px; }
em { color: #475569; }
a { color: #111; }
table { width: 100%; border-collapse: collapse; font-size: 10.5px; }
th, td { border: 1px solid #d1d5db; padding: 4px; }
""",
    },
    "latex_original": {
        "id": "latex_original",
        "name": "Latex - original",
        "description": "Original Pandoc/XeLaTeX export path with conventional paper formatting.",
        "layout_id": "latex-original",
        "cover_treatment": "pandoc-title",
        "opening_block": "template-summary-box",
        "body_flow": "latex-paper",
        "figure_treatment": "pandoc-figure",
        "table_treatment": "latex-table",
        "citation_style": "numbered-superscript",
        "accent": "#111827",
        "font_stack": "'Times New Roman', Times, serif",
        "heading_font_stack": "'Times New Roman', Times, serif",
        "body_font_stack": "'Times New Roman', Times, serif",
        "summary_block": "latex-original",
        "toc_depth": 2,
        "chapter_numbering": True,
        "max_width": "820px",
        "geometry": "margin=0.9in",
        "fontsize": "10pt",
        "css": """
body { font-family: "Times New Roman", Times, serif; color: #111; background: #fff; max-width: 820px; margin: 0 auto; padding: 42px 30px 74px; }
h1 { font-size: 30px; line-height: 1.1; text-align: center; margin: 0 0 22px; }
h2 { font-size: 21px; margin-top: 34px; }
h3 { font-size: 16px; margin-top: 24px; }
p, li { font-size: 14px; line-height: 1.55; }
.template-summary-box { border: 1px solid #d1d5db; background: #fafafa; padding: 16px 18px; margin: 24px 0; }
.report-toc { border-top: 1px solid #d1d5db; border-bottom: 1px solid #d1d5db; padding: 14px 0; margin: 24px 0; }
img { max-width: 96%; display: block; margin: 16px auto 8px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { border: 1px solid #d1d5db; padding: 6px; }
""",
    },
}


def report_md_path(corpus: Path) -> Path:
    return publish.publish_dir(corpus) / "report.md"


def report_manifest_path(corpus: Path) -> Path:
    return publish.publish_dir(corpus) / "report_manifest.json"


def report_preview_pdf_path(corpus: Path) -> Path:
    return publish.publish_dir(corpus) / "report_preview.pdf"


def report_preview_html_path(corpus: Path) -> Path:
    return publish.publish_dir(corpus) / "report_preview.html"


def report_preview_pages_dir(corpus: Path) -> Path:
    return publish.publish_dir(corpus) / "report_preview_pages"


def report_preview_pdf_path_for_corpus(corpus: str) -> Path:
    return report_preview_pdf_path(query_bundles.resolve_topic_corpus(corpus))


def report_preview_page_path_for_corpus(corpus: str, filename: str) -> Path:
    safe_name = Path(str(filename or "")).name
    if not re.fullmatch(r"page-\d+\.png", safe_name):
        raise ValueError("Invalid preview page.")
    return report_preview_pages_dir(query_bundles.resolve_topic_corpus(corpus)) / safe_name


def get_report_payload(corpus: str = "latest") -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    paper_path = publish.publish_paper_path(corpus_path)
    manifest = publish.read_json_object(report_manifest_path(corpus_path))
    warnings = readiness_warnings(corpus_path)
    return {
        "version": REPORT_VERSION,
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "ready": paper_path.exists(),
        "paper": {"exists": paper_path.exists(), "path": str(paper_path)},
        "report": {"exists": report_md_path(corpus_path).exists(), "path": str(report_md_path(corpus_path))},
        "source_index": {
            "exists": publish.publish_source_index_path(corpus_path).exists(),
            "path": str(publish.publish_source_index_path(corpus_path)),
        },
        "visual_plan": {
            "exists": publish.visual_plan_path(corpus_path).exists(),
            "path": str(publish.visual_plan_path(corpus_path)),
        },
        "templates": [public_template(row) for row in REPORT_TEMPLATES.values()],
        "formats": list(REPORT_FORMATS.values()),
        "default_template_id": DEFAULT_TEMPLATE_ID,
        "default_output_dir": str(publish.publish_dir(corpus_path) / "exports"),
        "metadata_defaults": default_report_metadata(corpus_path),
        "manifest": manifest or None,
        "dependency": dependency_status(),
        "warnings": warnings,
    }


def build_report_preview(corpus: str = "latest", template_id: str = DEFAULT_TEMPLATE_ID, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    template = validate_template(template_id)
    image_mode = "latex_preview" if uses_original_latex_pdf(template) else "preview"
    structure = build_report_structure(corpus_path, image_mode=image_mode, metadata=metadata)
    template_assets = prepare_template_assets(template, image_mode="preview", asset_dir=None)
    structure["template_assets"] = template_assets
    structure["warnings"].extend(template_assets["warnings"])
    markdown = render_structured_report_markdown(structure, template)
    publish.publish_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    atomic_write_text(report_md_path(corpus_path), markdown)
    if uses_original_latex_pdf(template):
        html = render_preview_html(markdown, template)
    else:
        html = render_rich_report_html(structure, template)
    preview_html = report_preview_html_path(corpus_path)
    preview_pdf = report_preview_pdf_path(corpus_path)
    atomic_write_text(preview_html, html)
    render_pdf_preview(markdown, html, template, preview_pdf, publish.publish_dir(corpus_path))
    generated_at = datetime.now().isoformat(timespec="seconds")
    page_paths, page_warnings = render_pdf_preview_pages(preview_pdf, report_preview_pages_dir(corpus_path))
    preview_page_urls = [
        f"/api/atlas/{quote(corpus_path.name)}/publish/report/preview-pages/{quote(path.name)}?t={quote(generated_at)}"
        for path in page_paths
    ]
    return {
        "version": REPORT_VERSION,
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "template": public_template(template),
        "metadata": structure["metadata"],
        "html": html,
        "preview_mode": "pdf",
        "preview_url": f"/api/atlas/{quote(corpus_path.name)}/publish/report/preview.pdf?t={quote(generated_at)}",
        "preview_page_urls": preview_page_urls,
        "pdf_preview_path": str(preview_pdf),
        "html_preview_path": str(preview_html),
        "report_path": str(report_md_path(corpus_path)),
        "warnings": readiness_warnings(corpus_path) + structure["warnings"] + page_warnings,
        "generated_at": generated_at,
    }


def export_report(corpus: str, template_id: str, output_dir: str, formats: list[str], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    corpus_path = query_bundles.resolve_topic_corpus(corpus)
    template = validate_template(template_id)
    selected_formats = validate_formats(formats)
    output_path = validate_output_dir(output_dir)
    ensure_export_dependencies(selected_formats, template)
    output_path.mkdir(parents=True, exist_ok=True)
    asset_dir = output_path / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)

    structure = build_report_structure(corpus_path, image_mode="export", asset_dir=asset_dir, metadata=metadata)
    template_assets = prepare_template_assets(template, image_mode="export", asset_dir=asset_dir)
    structure["template_assets"] = template_assets
    structure["warnings"].extend(template_assets["warnings"])
    report_markdown = render_structured_report_markdown(structure, template)
    publish.publish_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    atomic_write_text(report_md_path(corpus_path), report_markdown)
    report_name = slugify(structure["metadata"].get("title") or publish.paper_title(corpus_path), fallback="report")
    report_md = output_path / "report.md"
    css_path = output_path / "report.css"
    atomic_write_text(report_md, report_markdown)
    atomic_write_text(css_path, full_template_css(template, template_assets).strip() + "\n")

    outputs = [{"format": "markdown", "path": str(report_md), "bytes": report_md.stat().st_size}]
    commands = []
    rich_html_cache = ""
    for fmt in selected_formats:
        extension = REPORT_FORMATS[fmt]["extension"]
        out_path = output_path / f"{report_name}-{template['id']}.{extension}"
        if fmt == "html":
            html = render_html(report_markdown, template) if uses_original_latex_pdf(template) else render_rich_report_html(structure, template)
            if not uses_original_latex_pdf(template):
                rich_html_cache = html
            atomic_write_text(out_path, html)
            command = ["rich-html-renderer", str(report_md), str(out_path)]
        elif fmt == "pdf" and not uses_original_latex_pdf(template):
            if not rich_html_cache:
                rich_html_cache = render_rich_report_html(structure, template)
            command = render_pdf_from_template_html(rich_html_cache, out_path, output_path)
        else:
            command = pandoc_command(report_md, out_path, fmt, template)
            run_pandoc(command, output_path)
        outputs.append({"format": fmt, "path": str(out_path), "bytes": out_path.stat().st_size})
        commands.append(command)

    manifest = {
        "version": REPORT_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "corpus": {"id": corpus_path.name, "path": str(corpus_path)},
        "template": public_template(template),
        "metadata": structure["metadata"],
        "formats": selected_formats,
        "output_dir": str(output_path),
        "outputs": outputs,
        "warnings": readiness_warnings(corpus_path) + structure["warnings"],
        "commands": commands,
    }
    publish.publish_dir(corpus_path).mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_manifest_path(corpus_path), manifest)
    return manifest


def write_canonical_report(corpus: Path) -> None:
    structure = build_report_structure(corpus, image_mode="canonical")
    template = validate_template(DEFAULT_TEMPLATE_ID)
    markdown = render_structured_report_markdown(structure, template)
    publish.publish_dir(corpus).mkdir(parents=True, exist_ok=True)
    atomic_write_text(report_md_path(corpus), markdown)


def assemble_report_markdown(corpus: Path, image_mode: str, asset_dir: Path | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    structure = build_report_structure(corpus, image_mode=image_mode, asset_dir=asset_dir, metadata=metadata)
    template = validate_template(DEFAULT_TEMPLATE_ID)
    markdown = render_structured_report_markdown(structure, template)
    return {"markdown": markdown, "warnings": structure["warnings"]}


def build_report_structure(corpus: Path, image_mode: str = "canonical", asset_dir: Path | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    paper_path = publish.publish_paper_path(corpus)
    if not paper_path.exists():
        raise RuntimeError("Compiled paper not found. Compile the paper before publishing.")

    plan = publish.read_json_object(publish.publish_plan_path(corpus))
    state = publish.read_json_object(publish.publish_state_path(corpus))
    source_index = publish.read_json_object(publish.publish_source_index_path(corpus))
    visual_plan = publish.read_json_object(publish.visual_plan_path(corpus))
    candidates = {row.get("candidate_id"): row for row in publish.read_visual_candidates(corpus)}
    visuals_by_section = group_visuals_by_section(visual_plan)
    warnings: list[str] = []
    report_metadata, metadata_warnings = normalize_report_metadata(corpus, metadata, image_mode=image_mode, asset_dir=asset_dir)
    warnings.extend(metadata_warnings)
    groups = group_sections_by_source_path(plan, state)

    if groups:
        for group in groups:
            for section in group["sections"]:
                completed = section.get("completed") or {}
                path = Path(str(completed.get("path", "")))
                if not completed:
                    warnings.append(f"Section not compiled: {section.get('title', section.get('section_id', ''))}")
                    section["body"] = ""
                    continue
                if not path.exists():
                    warnings.append(f"Missing compiled section file: {path}")
                    section["body"] = ""
                    continue
                raw = path.read_text(encoding="utf-8", errors="replace")
                section["summary"] = extract_section_summary(raw)
                section["opening_summary"] = extract_section_summary(raw, sentence_limit=3, max_chars=None)
                section["body"] = normalize_section_body(raw)
                figure_lines, figure_warnings, figure_records = render_section_visuals(
                    corpus,
                    visuals_by_section.get(section["section_id"], []),
                    candidates,
                    image_mode,
                    asset_dir,
                )
                section["visual_lines"] = figure_lines
                section["visuals"] = figure_records
                warnings.extend(figure_warnings)
    else:
        warnings.append("Publish plan not found; assembled report from paper.md.")

    unassigned_visual_lines: list[str] = []
    unassigned_visuals: list[dict[str, str]] = []
    unassigned = [item for item in visual_plan.get("placeholders", []) if not item.get("section_id")]
    if unassigned:
        unassigned_visual_lines, figure_warnings, unassigned_visuals = render_section_visuals(corpus, unassigned, candidates, image_mode, asset_dir)
        warnings.extend(figure_warnings)

    return {
        "version": REPORT_VERSION,
        "title": report_metadata["title"],
        "generated_date": datetime.now().strftime("%Y-%m-%d"),
        "corpus": {"id": corpus.name, "path": str(corpus)},
        "metadata": report_metadata,
        "groups": groups,
        "summary_items": summary_items_from_groups(groups),
        "source_index": source_index,
        "fallback_markdown": strip_existing_source_index(paper_path.read_text(encoding="utf-8", errors="replace")).strip() if not groups else "",
        "unassigned_visual_lines": unassigned_visual_lines,
        "unassigned_visuals": unassigned_visuals,
        "warnings": dedupe_text(warnings),
    }


def group_sections_by_source_path(plan: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    completed_by_id = {item.get("section_id"): item for item in state.get("completed_sections", [])}
    groups: list[dict[str, Any]] = []
    group_by_key: dict[str, dict[str, Any]] = {}
    for index, section in enumerate(plan.get("sections") or [], start=1):
        source_path = [str(item).strip() for item in section.get("source_path", []) if str(item).strip()]
        group_title = source_path[0] if len(source_path) > 1 else "Report"
        group_key = slugify(group_title, fallback=f"group-{len(groups) + 1}")
        if group_key not in group_by_key:
            group = {
                "group_id": group_key,
                "title": group_title,
                "order": len(groups) + 1,
                "sections": [],
            }
            group_by_key[group_key] = group
            groups.append(group)
        title = publish.clean_title(section.get("title") or (source_path[-1] if source_path else f"Section {index}"))
        section_id = str(section.get("section_id") or f"section-{index:03d}")
        group_by_key[group_key]["sections"].append(
            {
                "section_id": section_id,
                "source_topic_id": section.get("source_topic_id", ""),
                "title": title,
                "source_path": source_path,
                "order": index,
                "completed": completed_by_id.get(section_id, {}),
                "summary": "",
                "body": "",
                "visual_lines": [],
            }
        )
    return groups


def extract_section_summary(markdown: str, sentence_limit: int = 2, max_chars: int | None = 360) -> str:
    text = publish.strip_markdown(strip_leading_heading(markdown))
    text = re.sub(r"\[S[^\]]+\]", "", text)
    text = clean_rendered_text_spacing(text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    summary = " ".join(sentences[:sentence_limit]).strip()
    if max_chars is not None and len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


def render_structured_report_markdown(structure: dict[str, Any], template: dict[str, Any]) -> str:
    metadata = structure.get("metadata") or default_report_metadata(Path(structure["corpus"]["path"]))
    parts = [
        f"# {metadata.get('title') or structure['title']}",
        "",
    ]
    if metadata.get("subtitle"):
        parts.extend([metadata["subtitle"], ""])
    if metadata.get("authors"):
        parts.extend([f"**{metadata['authors']}**", ""])
    meta_line = []
    if metadata.get("organization"):
        meta_line.append(metadata["organization"])
    if metadata.get("date"):
        meta_line.append(metadata["date"])
    if metadata.get("website"):
        meta_line.append(metadata["website"])
    meta_line.append(f"Prepared from {PRODUCT_NAME} compile artifacts.")
    parts.extend([f"_{' | '.join(meta_line)}_", ""])
    summary_items = structure.get("summary_items") or []
    if summary_items:
        classes = "template-summary-box"
        if template["id"] == "meta_research":
            classes += " meta-summary-box"
        parts.extend([f'::: {{class="{classes}"}}', "", "## Opening Summary", ""])
        for item in summary_items:
            if template["id"] == "meta_research":
                parts.append(str(item.get("opening_summary") or item["summary"]))
                parts.append("")
            else:
                parts.append(f"- **{item['title']}**: {item['summary']}")
        parts.extend(["", ":::", ""])

    groups = structure.get("groups") or []
    if groups:
        parts.extend(['::: {class="report-toc"}', "", "## Table of Contents", ""])
        for group in groups:
            parts.append(f"{group['order']}. {group['title']}")
            for section in group.get("sections", []):
                parts.append(f"   - {section['title']}")
        parts.extend(["", ":::", ""])
        for group in groups:
            parts.extend(["", f"## {group['title']}", ""])
            for section in group.get("sections", []):
                parts.extend([f"### {section['title']}", ""])
                if section.get("body"):
                    parts.extend([section["body"].strip(), ""])
                if section.get("visual_lines"):
                    parts.extend(section["visual_lines"])
    else:
        parts.extend([structure.get("fallback_markdown", ""), ""])

    if structure.get("unassigned_visual_lines"):
        parts.extend(["## Visual Evidence", "", *structure["unassigned_visual_lines"]])
    body_markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(part for part in parts if part is not None)).strip()
    body_markdown, references = replace_local_citations_with_references(body_markdown, structure.get("source_index") or {})
    final_parts = [body_markdown]
    append_references(final_parts, references)
    markdown = re.sub(r"\n{3,}", "\n\n", "\n".join(part for part in final_parts if part is not None)).strip() + "\n"
    return markdown


def summary_items_from_groups(groups: list[dict[str, Any]]) -> list[dict[str, str]]:
    executive = next((group for group in groups if normalize_label(group.get("title")) == "executive summary"), None)
    source_sections = (executive or (groups[0] if groups else {})).get("sections", [])
    rows = []
    for section in source_sections[:4]:
        summary = section.get("summary") or ""
        if summary:
            rows.append({"title": section.get("title", "Section"), "summary": summary, "opening_summary": section.get("opening_summary") or summary})
    return rows


def normalize_section_body(markdown: str) -> str:
    text = strip_leading_heading(markdown).strip()
    return demote_markdown_headings(text, offset=2)


def strip_leading_heading(markdown: str) -> str:
    lines = markdown.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#{1,6}\s+\S", lines[0]):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def demote_markdown_headings(markdown: str, offset: int = 2) -> str:
    out = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})(\s+.+)$", line)
        if match:
            level = min(6, len(match.group(1)) + offset)
            out.append("#" * level + match.group(2))
        else:
            out.append(line)
    return "\n".join(out).strip()


def group_visuals_by_section(visual_plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for placeholder in visual_plan.get("placeholders") or []:
        if isinstance(placeholder, dict):
            rows[str(placeholder.get("section_id", ""))].append(placeholder)
    return rows


def render_section_visuals(
    corpus: Path,
    placeholders: list[dict[str, Any]],
    candidates: dict[str, dict[str, Any]],
    image_mode: str,
    asset_dir: Path | None,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    lines: list[str] = []
    warnings: list[str] = []
    records: list[dict[str, str]] = []
    for placeholder in placeholders:
        visual_id = str(placeholder.get("visual_id", ""))
        candidate = candidates.get(visual_id, {})
        image_ref, image_warnings = resolve_visual_reference(corpus, visual_id, placeholder, candidate, image_mode, asset_dir)
        warnings.extend(image_warnings)
        if not image_ref:
            continue
        caption = clean_caption(placeholder.get("caption") or candidate.get("caption") or candidate.get("alt_text") or "Visual evidence")
        source = str(placeholder.get("source_id") or candidate.get("source_id") or "").strip()
        source_suffix = f" Source: S{source.zfill(4) if source.isdigit() else source}." if source else ""
        alt = caption.replace("]", ")")[:160]
        lines.extend(["", f"![{alt}]({image_ref})", "", f"*Figure: {caption}.{source_suffix}*", ""])
        records.append(
            {
                "image_ref": image_ref,
                "caption": caption,
                "source_id": source.zfill(4) if source.isdigit() else source,
                "alt": alt,
            }
        )
    return lines, warnings, records


def resolve_visual_reference(
    corpus: Path,
    visual_id: str,
    placeholder: dict[str, Any],
    candidate: dict[str, Any],
    image_mode: str,
    asset_dir: Path | None,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    caption = clean_caption(placeholder.get("caption") or candidate.get("caption") or visual_id or "visual")
    if not visual_id:
        return "", ["Visual placeholder is missing an id."]
    if candidate.get("missing"):
        return "", [f"Missing visual omitted: {caption}"]
    if candidate.get("is_remote"):
        url = str(candidate.get("asset_path") or placeholder.get("asset_path") or "")
        if image_mode == "preview" and url:
            return url, []
        return "", [f"Remote visual omitted from export: {caption}"]
    local_path = Path(str(candidate.get("local_path") or ""))
    if not local_path.exists():
        fallback = str(candidate.get("asset_path") or placeholder.get("asset_path") or "")
        candidate_path = corpus / fallback.replace("%20", " ")
        local_path = candidate_path if candidate_path.exists() else local_path
    if not local_path.exists():
        return "", [f"Missing visual omitted: {caption}"]
    if image_mode == "preview":
        return file_to_data_uri(local_path), []
    if image_mode == "latex_preview":
        return str(local_path), []
    if image_mode == "canonical":
        try:
            return str(local_path.resolve().relative_to(corpus.resolve())).replace(" ", "%20"), []
        except Exception:
            return str(local_path), []
    if image_mode == "export":
        if not asset_dir:
            return "", [f"Export asset directory missing for visual: {caption}"]
        suffix = local_path.suffix or ".png"
        asset_name = f"{slugify(visual_id, fallback='visual')}{suffix}"
        target = asset_dir / asset_name
        shutil.copy2(local_path, target)
        return f"assets/{asset_name}", []
    return "", [f"Unknown image mode: {image_mode}"]


def replace_local_citations_with_references(
    markdown: str,
    source_index: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    sources = source_index.get("sources") or {}
    if state is None:
        state = {"references": [], "by_source_id": {}}
    references: list[dict[str, Any]] = state.setdefault("references", [])
    by_source_id: dict[str, dict[str, Any]] = state.setdefault("by_source_id", {})

    def reference_for(source_id: str) -> dict[str, Any]:
        if source_id not in by_source_id:
            number = len(references) + 1
            source = sources.get(source_id) or sources.get(source_id.lstrip("0")) or {}
            reference = {
                "number": number,
                "anchor_id": f"ref-{number}",
                "source_id": source_id,
                "source": source,
            }
            by_source_id[source_id] = reference
            references.append(reference)
        return by_source_id[source_id]

    def replace_block(block: str) -> str:
        if not CITATION_RE.search(block):
            return block
        if is_markdown_list_block(block):
            return "\n".join(replace_citations_in_text_unit(line, reference_for) for line in block.splitlines())
        return replace_citations_in_text_unit(block, reference_for)

    parts = re.split(r"(\n\s*\n)", markdown)
    rendered = "".join(part if re.fullmatch(r"\n\s*\n", part or "") else replace_block(part) for part in parts)
    return rendered, references


def replace_citations_in_text_unit(text: str, reference_for: Any) -> str:
    grouped: list[dict[str, Any]] = []
    seen: set[str] = set()

    def collect(match: re.Match[str]) -> str:
        source_id = normalize_source_id(match.group("source_id"))
        if source_id not in seen:
            grouped.append(reference_for(source_id))
            seen.add(source_id)
        return ""

    cleaned = clean_rendered_text_spacing(CITATION_RE.sub(collect, text)).rstrip()
    if not grouped:
        return cleaned
    marker = "".join(f"[^{reference['number']}^](#{reference['anchor_id']})" for reference in grouped)
    if re.search(r'[.!?;:]["\')\]]*$', cleaned):
        return f"{cleaned}{marker}"
    return f"{cleaned} {marker}"


def is_markdown_list_block(block: str) -> bool:
    lines = [line for line in block.splitlines() if line.strip()]
    return bool(lines) and all(re.match(r"\s*(?:[-*+]|\d+[.)])\s+", line) for line in lines)


def clean_rendered_text_spacing(text: str) -> str:
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\s+([)\]}])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def append_references(parts: list[str], references: list[dict[str, Any]]) -> None:
    if not references:
        return
    parts.extend(["## References", "", '::: {class="reference-list"}', ""])
    for reference in references:
        parts.append(render_reference_list_item(reference))
    parts.extend(["", ":::", ""])


def strip_existing_source_index(markdown: str) -> str:
    return re.split(r"\n##\s+(?:Source Index|References)\s*\n", markdown, maxsplit=1)[0]


def render_reference_list_item(reference: dict[str, Any]) -> str:
    source = reference.get("source") or {}
    source_id = str(reference.get("source_id") or "").strip()
    title = clean_reference_text(source.get("title") or f"Source S{source_id}")
    publisher = clean_reference_text(source.get("publisher") or "")
    url = clean_reference_url(source.get("url") or "")
    source_type = clean_reference_text(source.get("source_type") or "")
    title_part = f"[{escape_markdown_link_text(title)}](<{url}>)" if url else escape_markdown_link_text(title)
    details = [title_part]
    if publisher and publisher != "unknown":
        details.append(publisher)
    if source_type and source_type != "unknown":
        details.append(source_type)
    if source_id:
        details.append(f"Source S{source_id}")
    return f"{reference['number']}. []{{#{reference['anchor_id']}}} " + ". ".join(details).rstrip(".") + "."


def normalize_source_id(value: Any) -> str:
    text = str(value or "").strip()
    return text.zfill(4) if text.isdigit() else text


def clean_reference_text(value: Any) -> str:
    return clean_rendered_text_spacing(re.sub(r"\s+", " ", str(value or "")).strip())


def clean_reference_url(value: Any) -> str:
    return str(value or "").strip().strip("<>")


def escape_markdown_link_text(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")


def default_report_metadata(corpus: Path) -> dict[str, str]:
    return {
        "title": publish.paper_title(corpus),
        "subtitle": "",
        "authors": "",
        "organization": PRODUCT_NAME,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "website": "",
        "logo_path": "",
        "cover_image_path": "",
    }


def normalize_report_metadata(
    corpus: Path,
    metadata: dict[str, Any] | None,
    image_mode: str,
    asset_dir: Path | None,
) -> tuple[dict[str, str], list[str]]:
    defaults = default_report_metadata(corpus)
    raw = metadata if isinstance(metadata, dict) else {}
    normalized = {key: clean_metadata_text(raw.get(key, defaults[key])) for key in METADATA_FIELDS}
    if not normalized["title"]:
        normalized["title"] = defaults["title"]
    if not normalized["date"]:
        normalized["date"] = defaults["date"]

    warnings: list[str] = []
    logo_ref, logo_warning = resolve_metadata_asset("Logo", normalized["logo_path"], image_mode, asset_dir, "logo")
    cover_ref, cover_warning = resolve_metadata_asset("Cover image", normalized["cover_image_path"], image_mode, asset_dir, "cover")
    warnings.extend([warning for warning in (logo_warning, cover_warning) if warning])
    normalized["logo_ref"] = logo_ref
    normalized["cover_image_ref"] = cover_ref
    return normalized, warnings


def resolve_metadata_asset(label: str, raw_path: str, image_mode: str, asset_dir: Path | None, prefix: str) -> tuple[str, str]:
    value = clean_metadata_text(raw_path)
    if not value:
        return "", ""
    if not value.startswith("~") and not Path(value).is_absolute():
        return "", f"{label} path must be absolute or start with ~; omitted."
    path = Path(value).expanduser()
    if not path.exists() or not path.is_file():
        return "", f"{label} file not found; omitted: {path}"
    if image_mode == "preview":
        return file_to_data_uri(path), ""
    if image_mode == "latex_preview":
        return str(path), ""
    if image_mode == "export" and asset_dir:
        suffix = path.suffix or mimetypes.guess_extension(mimetypes.guess_type(str(path))[0] or "") or ".png"
        target = asset_dir / f"{prefix}{suffix}"
        shutil.copy2(path, target)
        return f"assets/{target.name}", ""
    if image_mode == "canonical":
        return "", ""
    return str(path), ""


def file_to_data_uri(path: Path) -> str:
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{payload}"


def clean_metadata_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def full_template_css(template: dict[str, Any], template_assets: dict[str, Any] | None = None) -> str:
    font_css = str((template_assets or {}).get("font_css") or "").strip()
    return "\n".join(part for part in [REFERENCE_CSS.strip(), font_css, RICH_REPORT_CSS.strip(), template["css"].strip()] if part).strip()


def prepare_template_assets(template: dict[str, Any], image_mode: str, asset_dir: Path | None) -> dict[str, Any]:
    assets: dict[str, Any] = {"font_css": "", "warnings": []}
    if template["id"] != "meta_research":
        return assets

    if not any(font_family_available(font) for font in META_FONT_CANDIDATES):
        assets["warnings"].append("Meta/Optimistic fonts were not found locally; using bundled Inter as the closest open-source sans fallback.")

    font_path = ensure_inter_font_asset()
    if not font_path:
        assets["warnings"].append("Bundled Inter font is unavailable; the report may fall back to system sans fonts.")
        return assets

    if image_mode == "preview":
        font_ref = file_to_data_uri(font_path)
    elif image_mode == "export" and asset_dir:
        target = asset_dir / font_path.name
        shutil.copy2(font_path, target)
        font_ref = f"assets/{target.name}"
    else:
        font_ref = str(font_path)

    assets["font_css"] = (
        '@font-face { font-family: "Challenger Inter"; '
        f'src: url("{css_url(font_ref)}") format("truetype"); '
        "font-weight: 100 900; font-style: normal; font-display: swap; }"
    )
    return assets


def ensure_inter_font_asset() -> Path | None:
    if INTER_FONT_PATH.exists():
        return INTER_FONT_PATH
    try:
        INTER_FONT_PATH.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(INTER_FONT_URL, INTER_FONT_PATH)
    except Exception:
        return None
    return INTER_FONT_PATH if INTER_FONT_PATH.exists() else None


def font_family_available(name: str) -> bool:
    normalized = normalize_font_name(name)
    return any(normalized in available for available in available_font_names())


def available_font_names() -> set[str]:
    global _FONT_REGISTRY_CACHE
    if _FONT_REGISTRY_CACHE is not None:
        return _FONT_REGISTRY_CACHE
    names: set[str] = set()
    try:
        result = subprocess.run(["atsutil", "fonts", "-list"], text=True, capture_output=True, timeout=5)
        if result.returncode == 0:
            names.update(normalize_font_name(line) for line in result.stdout.splitlines() if line.strip())
    except Exception:
        pass
    for root in (Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library" / "Fonts"):
        if root.exists():
            for path in root.rglob("*"):
                if path.suffix.lower() in {".ttf", ".otf", ".ttc", ".dfont"}:
                    names.add(normalize_font_name(path.stem))
    _FONT_REGISTRY_CACHE = names
    return names


def normalize_font_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def render_rich_report_html(structure: dict[str, Any], template: dict[str, Any]) -> str:
    references_state: dict[str, Any] = {"references": [], "by_source_id": {}}
    body_markdown = render_rich_body_markdown(structure)
    body_markdown, references = replace_local_citations_with_references(body_markdown, structure.get("source_index") or {}, references_state)
    body_html = markdown_to_html_fragment(body_markdown)
    title = structure["metadata"].get("title") or structure.get("title") or "Report"
    article_class = f'rich-report template-{html_attr(template["id"])} layout-{html_attr(template.get("layout_id", template["id"]))}'
    html = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html_text(title)}</title>",
        f"<style>\n{full_template_css(template, structure.get('template_assets'))}\n</style>",
        "</head>",
        '<body class="rich-report-page">',
        f'<article class="{article_class}">',
        render_rich_cover(structure, template),
        render_rich_toc(structure),
        f'<main class="report-body">{body_html}</main>',
        render_rich_references(references),
        "</article>",
        "</body>",
        "</html>",
    ]
    return "\n".join(part for part in html if part)


def render_rich_cover(structure: dict[str, Any], template: dict[str, Any]) -> str:
    metadata = structure["metadata"]
    cover_style = ""
    if metadata.get("cover_image_ref"):
        cover_style = f' style="--cover-image: url(&quot;{css_url(metadata["cover_image_ref"])}&quot;)"'
    subtitle = metadata.get("subtitle", "")
    authors = metadata.get("authors", "")
    kicker = report_kicker(template)
    return "\n".join(
        [
            f'<header class="report-cover"{cover_style}>',
            '<div class="report-brand-row">',
            render_logo_html(metadata, template),
            "</div>",
            '<div class="report-title-block">',
            f'<p class="report-kicker">{html_text(kicker)}</p>',
            f'<h1>{html_text(metadata["title"])}</h1>',
            f'<p class="report-subtitle">{html_text(subtitle)}</p>' if subtitle else "",
            f'<div class="report-byline">{html_text(authors)}</div>' if authors else "",
            render_metadata_rows_html(metadata),
            "</div>",
            render_opening_summary_html(structure, template),
            "</header>",
        ]
    )


def render_logo_html(metadata: dict[str, str], template: dict[str, Any]) -> str:
    if metadata.get("logo_ref"):
        return f'<img class="report-logo" src="{html_attr(metadata["logo_ref"])}" alt="{html_attr(metadata.get("organization") or "Report logo")}">'
    if template["id"] == "meta_research":
        return f'<div class="report-logo-text challenger-wordmark"><span class="wordmark-emoji" aria-hidden="true">🤿</span><span>{html_text(PRODUCT_NAME)}</span></div>'
    label = metadata.get("organization") or ("Strategy report" if template["id"] == "mckinsey" else "Research report")
    if template["id"] == "mckinsey" and metadata.get("organization"):
        label = metadata["organization"].replace(" & ", "\n& ")
    return f'<div class="report-logo-text">{html_text(label)}</div>'


def report_kicker(template: dict[str, Any]) -> str:
    return {
        "mckinsey": "Executive report",
        "meta_research": "Research paper",
        "iclr": "Research paper",
        "neurips": "Research paper",
        "nature_review": "Review report",
        "ieee_acm": "Technical report",
    }.get(template["id"], "Research report")


def render_metadata_rows_html(metadata: dict[str, str]) -> str:
    rows = []
    for label, key in (("Organization", "organization"), ("Date", "date"), ("Website", "website")):
        value = metadata.get(key, "")
        if not value:
            continue
        if key == "website" and value.startswith(("http://", "https://")):
            value_html = f'<a href="{html_attr(value)}">{html_text(value)}</a>'
        else:
            value_html = html_text(value)
        rows.append(f"<div><dt>{html_text(label)}</dt><dd>{value_html}</dd></div>")
    if not rows:
        return ""
    return '<dl class="report-metadata">' + "".join(rows) + "</dl>"


def render_opening_summary_html(structure: dict[str, Any], template: dict[str, Any]) -> str:
    items = structure.get("summary_items") or []
    if not items:
        return ""
    if template["id"] == "meta_research":
        rows = ['<section class="report-opening meta-paper-abstract">']
        for item in items:
            summary = str(item.get("opening_summary") or item.get("summary") or "").strip()
            if summary:
                rows.append(f"<p>{html_text(summary)}</p>")
        rows.append("</section>")
        return "\n".join(rows)
    heading = "Abstract" if template["id"] in {"iclr", "neurips", "ieee_acm"} else "Opening Summary"
    rows = [
        '<section class="report-opening">',
        f"<h2>{html_text(heading)}</h2>",
        "<ul>",
    ]
    for item in items:
        rows.append(f"<li><strong>{html_text(item.get('title', 'Section'))}</strong>: {html_text(item.get('summary', ''))}</li>")
    rows.extend(["</ul>", "</section>"])
    return "\n".join(rows)


def render_rich_toc(structure: dict[str, Any]) -> str:
    groups = structure.get("groups") or []
    if not groups:
        return ""
    rows = ['<nav class="report-toc">', "<h2>Table of Contents</h2>", "<ol>"]
    for group in groups:
        chapter_id = f"chapter-{group.get('group_id') or slugify(group.get('title'), fallback='chapter')}"
        rows.append(f'<li><a href="#{html_attr(chapter_id)}">{html_text(group.get("title", "Chapter"))}</a>')
        sections = group.get("sections") or []
        if sections:
            rows.append("<ul>")
            for section in sections:
                section_id = f"section-{slugify(section.get('section_id') or section.get('title'), fallback='section')}"
                rows.append(f'<li><a href="#{html_attr(section_id)}">{html_text(section.get("title", "Section"))}</a></li>')
            rows.append("</ul>")
        rows.append("</li>")
    rows.extend(["</ol>", "</nav>"])
    return "\n".join(rows)


def render_rich_body_markdown(structure: dict[str, Any]) -> str:
    parts: list[str] = []
    groups = structure.get("groups") or []
    if groups:
        for group in groups:
            chapter_id = f"chapter-{group.get('group_id') or slugify(group.get('title'), fallback='chapter')}"
            parts.extend([f'::: {{class="report-chapter"}}', "", f"## {group['title']} {{#{chapter_id} .report-chapter-title}}", ""])
            for section in group.get("sections", []):
                section_id = f"section-{slugify(section.get('section_id') or section.get('title'), fallback='section')}"
                parts.extend([f'::: {{class="report-section"}}', "", f"### {section['title']} {{#{section_id} .report-section-title}}", ""])
                if section.get("body"):
                    parts.extend([section["body"].strip(), ""])
                for visual in section.get("visuals", []):
                    parts.extend([render_visual_html(visual), ""])
                parts.extend([":::", ""])
            parts.extend([":::", ""])
    else:
        parts.extend([structure.get("fallback_markdown", ""), ""])
    if structure.get("unassigned_visuals"):
        parts.extend(['::: {class="report-chapter"}', "", "## Visual Evidence {#visual-evidence .report-chapter-title}", ""])
        for visual in structure["unassigned_visuals"]:
            parts.extend([render_visual_html(visual), ""])
        parts.extend([":::", ""])
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()


def render_visual_html(visual: dict[str, str]) -> str:
    source = visual.get("source_id", "")
    source_suffix = f" Source: S{html_text(source)}." if source else ""
    return (
        '<figure class="report-exhibit">'
        f'<img src="{html_attr(visual.get("image_ref", ""))}" alt="{html_attr(visual.get("alt") or visual.get("caption") or "Visual evidence")}">'
        f'<figcaption><strong>Exhibit.</strong> {html_text(visual.get("caption", "Visual evidence"))}.{source_suffix}</figcaption>'
        "</figure>"
    )


def render_rich_references(references: list[dict[str, Any]]) -> str:
    if not references:
        return ""
    rows = ['<section class="report-references reference-list">', "<h2>References</h2>", "<ol>"]
    for reference in references:
        rows.append(render_reference_html_item(reference))
    rows.extend(["</ol>", "</section>"])
    return "\n".join(rows)


def render_reference_html_item(reference: dict[str, Any]) -> str:
    source = reference.get("source") or {}
    source_id = str(reference.get("source_id") or "").strip()
    title = clean_reference_text(source.get("title") or f"Source S{source_id}")
    publisher = clean_reference_text(source.get("publisher") or "")
    url = clean_reference_url(source.get("url") or "")
    source_type = clean_reference_text(source.get("source_type") or "")
    title_html = f'<a href="{html_attr(url)}">{html_text(title)}</a>' if url else html_text(title)
    details = [title_html]
    if publisher and publisher != "unknown":
        details.append(html_text(publisher))
    if source_type and source_type != "unknown":
        details.append(html_text(source_type))
    if source_id:
        details.append(f"Source S{html_text(source_id)}")
    return f'<li id="{html_attr(reference["anchor_id"])}">' + ". ".join(details).rstrip(".") + ".</li>"


def markdown_to_html_fragment(markdown: str) -> str:
    if not markdown.strip():
        return ""
    ensure_pandoc()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        md_path = tmp_path / "fragment.md"
        md_path.write_text(markdown, encoding="utf-8")
        command = ["pandoc", str(md_path), "-f", PANDOC_FROM, "-t", "html"]
        result = subprocess.run(command, cwd=str(tmp_path), text=True, capture_output=True, timeout=180)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Pandoc failed.").strip()
            raise RuntimeError(detail[:2000])
        return result.stdout


def html_text(value: Any) -> str:
    return html_lib.escape(str(value or ""), quote=False)


def html_attr(value: Any) -> str:
    return html_lib.escape(str(value or ""), quote=True)


def css_url(value: str) -> str:
    return str(value or "").replace('"', "%22").replace("\\", "\\\\")


def render_preview_html(markdown: str, template: dict[str, Any]) -> str:
    return render_html(markdown, template)


def render_html(markdown: str, template: dict[str, Any]) -> str:
    ensure_pandoc()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        md_path = tmp_path / "report.md"
        out_path = tmp_path / "report.html"
        md_path.write_text(markdown, encoding="utf-8")
        command = ["pandoc", str(md_path), "-f", PANDOC_FROM, "-t", "html", "--standalone", "--metadata", f"title={template['name']}", "-o", str(out_path)]
        run_pandoc(command, tmp_path)
        html = out_path.read_text(encoding="utf-8", errors="replace")
    return inject_template_html(html, template)


def inject_template_html(html: str, template: dict[str, Any]) -> str:
    style = f"<style>\n{full_template_css(template)}\n</style>"
    if "</head>" in html:
        html = html.replace("</head>", f"{style}\n</head>", 1)
    else:
        html = f"{style}\n{html}"
    body_class = f'template-{template["id"]}'
    return html.replace("<body>", f'<body class="{body_class}">', 1)


def pandoc_command(markdown_path: Path, output_path: Path, fmt: str, template: dict[str, Any]) -> list[str]:
    if fmt not in {"pdf", "docx", "latex"}:
        raise ValueError(f"Unsupported Pandoc export format: {fmt}")
    target = "latex" if fmt == "latex" else fmt
    command = [
        "pandoc",
        str(markdown_path),
        "-f",
        PANDOC_FROM,
        "-t",
        target,
        "--standalone",
        "--toc",
        "--number-sections",
        "--metadata",
        f"title={template['name']}",
        "-o",
        str(output_path),
    ]
    if fmt == "pdf":
        command.extend(["--pdf-engine=xelatex", "-V", f"geometry:{template['geometry']}", "-V", f"fontsize={template['fontsize']}"])
    return command


def run_pandoc(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, timeout=180)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Pandoc failed.").strip()
        raise RuntimeError(detail[:2000])


def render_pdf_from_template_html(html: str, output_path: Path, cwd: Path) -> list[str]:
    renderer = html_pdf_renderer_path()
    if not renderer:
        raise RuntimeError("WeasyPrint is required for PDF export from rich report templates.")
    html_path = output_path.with_suffix(".render.html")
    atomic_write_text(html_path, pdf_safe_html(html))
    command = [renderer, str(html_path), str(output_path)]
    result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, timeout=240)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "WeasyPrint failed.").strip()
        raise RuntimeError(detail[:2000])
    try:
        html_path.unlink()
    except OSError:
        pass
    return command


def pdf_safe_html(html: str) -> str:
    cleaned = html
    cleaned = re.sub(r"\s*text-justify\s*:\s*[^;{}]+;?", "", cleaned)
    cleaned = re.sub(r"\s*backdrop-filter\s*:\s*[^;{}]+;?", "", cleaned)
    cleaned = re.sub(r"\s*box-shadow\s*:\s*[^;{}]+;?", "", cleaned)
    cleaned = re.sub(r"\s*column-count\s*:\s*[^;{}]+;?", "", cleaned)
    cleaned = re.sub(r"\s*column-gap\s*:\s*[^;{}]+;?", "", cleaned)
    cleaned = re.sub(r"\s*column-span\s*:\s*[^;{}]+;?", "", cleaned)
    cleaned = re.sub(r"\s*break-inside\s*:\s*[^;{}]+;?", "", cleaned)
    cleaned = cleaned.replace("font-weight: 760", "font-weight: 700")
    cleaned = re.sub(r"@media\s*\(max-width:\s*760px\)\s*\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "", cleaned, flags=re.S)
    override = """
<style>
.template-mckinsey .report-cover { min-height: auto; }
.template-mckinsey .report-body, .template-ieee_acm .report-body { max-width: 980px; }
.template-mckinsey .report-toc { display: block; }
.template-mckinsey .report-chapter-title { font-size: 30px; line-height: 1.04; }
.report-cover, .report-toc, .report-body, .report-references { overflow-wrap: anywhere; }
</style>
"""
    if "</head>" in cleaned:
        return cleaned.replace("</head>", f"{override}\n</head>", 1)
    return override + cleaned


def render_pdf_preview_pages(pdf_path: Path, page_dir: Path) -> tuple[list[Path], list[str]]:
    renderer = shutil.which("pdftoppm")
    if not renderer:
        return [], ["PDF page renderer not found; using embedded PDF preview fallback."]
    if page_dir.exists():
        shutil.rmtree(page_dir)
    page_dir.mkdir(parents=True, exist_ok=True)
    command = [renderer, "-png", "-r", "96", str(pdf_path), str(page_dir / "page")]
    result = subprocess.run(command, text=True, capture_output=True, timeout=300)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "PDF page rendering failed.").strip()
        return [], [detail[:1000]]
    pages = sorted(page_dir.glob("page-*.png"), key=preview_page_sort_key)
    return pages, []


def preview_page_sort_key(path: Path) -> int:
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0


def render_pdf_preview(markdown: str, html: str, template: dict[str, Any], output_path: Path, cwd: Path) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if uses_original_latex_pdf(template):
        if not shutil.which("xelatex"):
            raise RuntimeError("XeLaTeX is required for Latex - original PDF preview.")
        markdown_path = output_path.with_suffix(".preview.md")
        atomic_write_text(markdown_path, markdown)
        command = pandoc_command(markdown_path, output_path, "pdf", template)
        run_pandoc(command, cwd)
        return command
    return render_pdf_from_template_html(html, output_path, cwd)


def html_pdf_renderer_path() -> str:
    # pip installs the weasyprint command next to this Python, which isn't on PATH unless its venv is activated.
    for search_path in (None, sysconfig.get_path("scripts"), str(REPORTING_VENV / "bin")):
        path = shutil.which("weasyprint", path=search_path)
        if path:
            return path
    return ""


def uses_original_latex_pdf(template: dict[str, Any]) -> bool:
    return template.get("id") == LATEX_ORIGINAL_TEMPLATE_ID


def validate_template(template_id: str) -> dict[str, Any]:
    template = REPORT_TEMPLATES.get(str(template_id or DEFAULT_TEMPLATE_ID))
    if not template:
        raise ValueError(f"Unknown report template: {template_id}")
    return template


def public_template(template: dict[str, Any]) -> dict[str, Any]:
    return {
        key: template[key]
        for key in (
            "id",
            "name",
            "description",
            "layout_id",
            "cover_treatment",
            "opening_block",
            "body_flow",
            "figure_treatment",
            "table_treatment",
            "citation_style",
            "accent",
            "font_stack",
            "heading_font_stack",
            "body_font_stack",
            "summary_block",
            "toc_depth",
            "chapter_numbering",
            "max_width",
        )
        if key in template
    }


def validate_formats(formats: list[str]) -> list[str]:
    if not isinstance(formats, list) or not formats:
        raise ValueError("Select at least one export format.")
    seen = []
    for value in formats:
        fmt = str(value).strip().lower()
        if fmt not in REPORT_FORMATS:
            raise ValueError(f"Unsupported export format: {fmt}")
        if fmt not in seen:
            seen.append(fmt)
    return seen


def validate_output_dir(value: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Output directory is required.")
    if not raw.startswith("~") and not Path(raw).is_absolute():
        raise ValueError("Output directory must be absolute or start with ~.")
    path = Path(raw).expanduser().resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("Output path exists and is not a directory.")
    return path


def dependency_status() -> dict[str, Any]:
    pandoc = shutil.which("pandoc")
    xelatex = shutil.which("xelatex")
    weasyprint = html_pdf_renderer_path()
    pdftoppm = shutil.which("pdftoppm")
    return {
        "pandoc": {"available": bool(pandoc), "path": pandoc or ""},
        "xelatex": {"available": bool(xelatex), "path": xelatex or ""},
        "weasyprint": {"available": bool(weasyprint), "path": weasyprint},
        "pdftoppm": {"available": bool(pdftoppm), "path": pdftoppm or ""},
        "fonts": {
            "meta_fonts_available": any(font_family_available(font) for font in META_FONT_CANDIDATES),
            "bundled_inter_available": bool(ensure_inter_font_asset()),
            "bundled_inter_path": str(INTER_FONT_PATH) if INTER_FONT_PATH.exists() else "",
        },
    }


def ensure_pandoc() -> None:
    if not shutil.which("pandoc"):
        raise RuntimeError("Pandoc is required for report preview and export.")


def ensure_export_dependencies(formats: list[str], template: dict[str, Any]) -> None:
    ensure_pandoc()
    if "pdf" not in formats:
        return
    if uses_original_latex_pdf(template):
        if not shutil.which("xelatex"):
            raise RuntimeError("XeLaTeX is required for Latex - original PDF export.")
    elif not html_pdf_renderer_path():
        raise RuntimeError("WeasyPrint is required for PDF export from selected report templates.")


def readiness_warnings(corpus: Path) -> list[str]:
    warnings = []
    if not publish.publish_paper_path(corpus).exists():
        warnings.append("Compiled paper not found.")
    if not publish.publish_source_index_path(corpus).exists():
        warnings.append("Source index not found.")
    state = publish.read_json_object(publish.publish_state_path(corpus))
    if state and state.get("state") != "completed":
        warnings.append("Compile state is not completed.")
    return warnings


def clean_caption(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.rstrip(".")[:260] or "Visual evidence"


def normalize_label(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def dedupe_text(values: list[str]) -> list[str]:
    seen = set()
    out = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out
