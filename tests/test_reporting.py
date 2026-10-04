from __future__ import annotations

import json
import os
import shutil
import struct
import sysconfig
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from superresearcher import publish, reporting


def png_bytes(width: int = 240, height: int = 160) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    rows = b"".join(b"\x00" + (b"\xf4\xf8\xfb" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def make_report_corpus(tmp: str) -> Path:
    corpus = Path(tmp) / "demo_Corpus"
    section_path = publish.publish_sections_dir(corpus) / "001-market.md"
    second_section_path = publish.publish_sections_dir(corpus) / "002-grid.md"
    asset_path = corpus / "assets" / "figures" / "adoption.png"
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.write_bytes(png_bytes())
    write_json(corpus / "run.json", {"topic": "Demo report"})
    section_path.parent.mkdir(parents=True, exist_ok=True)
    section_path.write_text("## Market Evidence\n\nAdoption is accelerating [S0001:Cchunk-1].\n", encoding="utf-8")
    second_section_path.write_text("## Grid Evidence\n\nCharging infrastructure is a gating requirement [S0001:Cchunk-2].\n", encoding="utf-8")
    write_json(
        publish.publish_plan_path(corpus),
        {
            "version": publish.PUBLISH_VERSION,
            "sections": [
                {
                    "section_id": "section-001",
                    "title": "Market Evidence",
                    "source_topic_id": "topic-1",
                    "source_path": ["Market", "Evidence"],
                },
                {
                    "section_id": "section-002",
                    "title": "Grid Evidence",
                    "source_topic_id": "topic-2",
                    "source_path": ["Infrastructure", "Grid"],
                }
            ],
        },
    )
    write_json(
        publish.publish_state_path(corpus),
        {
            "version": publish.PUBLISH_VERSION,
            "state": "completed",
            "completed_sections": [
                {
                    "section_id": "section-001",
                    "title": "Market Evidence",
                    "path": str(section_path),
                    "chunk_count": 1,
                },
                {
                    "section_id": "section-002",
                    "title": "Grid Evidence",
                    "path": str(second_section_path),
                    "chunk_count": 1,
                }
            ],
        },
    )
    write_json(
        publish.publish_source_index_path(corpus),
        {
            "version": publish.PUBLISH_VERSION,
            "sources": {"0001": {"title": "Market Study", "publisher": "one", "url": "https://one.test"}},
            "chunks": {},
        },
    )
    publish.publish_paper_path(corpus).write_text(
        "# Demo report\n\n## Market Evidence\n\nAdoption is accelerating.\n\n## Grid Evidence\n\nCharging infrastructure is required.\n",
        encoding="utf-8",
    )
    write_jsonl(
        publish.visual_candidates_path(corpus),
        [
            {
                "candidate_id": "vis_adoption",
                "asset_path": "assets/figures/adoption.png",
                "local_path": str(asset_path),
                "is_remote": False,
                "missing": False,
                "file_size": 100000,
                "width": 240,
                "height": 160,
                "flags": [],
                "extraction_status": "no_extracted_text",
                "caption": "Adoption chart",
                "source_id": "0001",
            }
        ],
    )
    write_json(
        publish.visual_plan_path(corpus),
        {
            "version": publish.PUBLISH_VERSION,
            "placeholders": [
                {
                    "placeholder_id": "fig_001_01",
                    "section_id": "section-001",
                    "visual_id": "vis_adoption",
                    "caption": "Adoption chart",
                    "source_id": "0001",
                }
            ],
        },
    )
    return corpus


class ReportingTests(unittest.TestCase):
    def test_template_registry_has_expected_v1_templates(self) -> None:
        self.assertEqual(
            set(reporting.REPORT_TEMPLATES),
            {"mckinsey", "iclr", "neurips", "meta_research", "nature_review", "ieee_acm", "latex_original"},
        )
        self.assertEqual(reporting.REPORT_TEMPLATES["mckinsey"]["layout_id"], "consulting-report")
        self.assertEqual(reporting.REPORT_TEMPLATES["meta_research"]["opening_block"], "meta-paper-abstract")
        self.assertEqual(reporting.REPORT_TEMPLATES["latex_original"]["name"], "Latex - original")

    def test_metadata_defaults_and_invalid_assets_warn_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)

            defaults = reporting.default_report_metadata(corpus)
            metadata, warnings = reporting.normalize_report_metadata(
                corpus,
                {"title": "", "date": "", "logo_path": "relative/logo.png", "cover_image_path": str(Path(tmp) / "missing.png")},
                image_mode="preview",
                asset_dir=None,
            )

            self.assertEqual(defaults["title"], "Demo report")
            self.assertEqual(defaults["organization"], "Challenger Deep")
            self.assertEqual(metadata["title"], "Demo report")
            self.assertEqual(metadata["date"], defaults["date"])
            self.assertIn("Logo path must be absolute or start with ~; omitted.", warnings)
            self.assertTrue(any("Cover image file not found" in warning for warning in warnings))

    def test_assembly_inserts_selected_visual_and_source_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)

            result = reporting.assemble_report_markdown(corpus, image_mode="canonical")

            self.assertIn("![Adoption chart](assets/figures/adoption.png)", result["markdown"])
            self.assertIn("Figure: Adoption chart. Source: S0001.", result["markdown"])
            self.assertIn("## References", result["markdown"])

    def test_local_chunk_citations_become_reused_numbered_reference_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)

            result = reporting.assemble_report_markdown(corpus, image_mode="canonical")
            body, references = result["markdown"].split("## References", 1)

            self.assertNotIn("[S0001:Cchunk-1]", result["markdown"])
            self.assertNotIn("[S0001:Cchunk-2]", result["markdown"])
            self.assertEqual(body.count("[^1^](#ref-1)"), 2)
            self.assertNotIn("[^ref-1]", result["markdown"])
            self.assertEqual(references.count("[]{#ref-1} [Market Study](<https://one.test>). one. Source S0001."), 1)
            self.assertIn('::: {class="reference-list"}', references)

    def test_citations_group_once_per_paragraph_and_fix_punctuation_spacing(self) -> None:
        markdown = "One finding [S0001:Cchunk-1] . Another sentence [S0001:Cchunk-2]. Third point [S0002:Cchunk-3] ."
        source_index = {
            "sources": {
                "0001": {"title": "Market Study", "publisher": "one", "url": "https://one.test"},
                "0002": {"title": "Policy Study", "publisher": "two", "url": "https://two.test"},
            }
        }

        rendered, references = reporting.replace_local_citations_with_references(markdown, source_index)

        self.assertEqual(rendered.count("[^1^](#ref-1)"), 1)
        self.assertEqual(rendered.count("[^2^](#ref-2)"), 1)
        self.assertIn("One finding. Another sentence. Third point.", rendered)
        self.assertNotIn("finding .", rendered)
        self.assertNotIn("point .", rendered)
        self.assertEqual([reference["source_id"] for reference in references], ["0001", "0002"])

    def test_source_path_groups_flat_plan_into_topic_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)
            plan = publish.read_json_object(publish.publish_plan_path(corpus))
            state = publish.read_json_object(publish.publish_state_path(corpus))

            groups = reporting.group_sections_by_source_path(plan, state)

            self.assertEqual([group["title"] for group in groups], ["Market", "Infrastructure"])
            self.assertEqual(groups[0]["sections"][0]["title"], "Market Evidence")

    def test_rendered_markdown_uses_nested_toc_and_body_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)

            result = reporting.assemble_report_markdown(corpus, image_mode="canonical")

            self.assertIn("1. Market\n   - Market Evidence", result["markdown"])
            self.assertIn("## Market", result["markdown"])
            self.assertIn("### Market Evidence", result["markdown"])
            self.assertNotIn("1. Market Evidence\n2. Grid Evidence", result["markdown"])

    def test_meta_opening_summary_is_not_ellipsized(self) -> None:
        sentence = (
            "Certification readiness depends on legal, regulatory, operational, infrastructure, safety, public acceptance, "
            "airspace integration, energy planning, vertiport siting, emergency response, workforce training, insurance, "
            "and interagency coordination across local, state, and federal agencies. "
        )
        markdown = "## Executive Summary\n\n" + sentence * 5

        clipped = reporting.extract_section_summary(markdown)
        full = reporting.extract_section_summary(markdown, sentence_limit=3, max_chars=None)

        self.assertTrue(clipped.endswith("..."))
        self.assertFalse(full.endswith("..."))
        self.assertGreater(len(full), len(clipped))

    def test_summary_extraction_removes_space_before_period_after_citation(self) -> None:
        summary = reporting.extract_section_summary("## Summary\n\nCities need planning [S0001:Cchunk-1] . Agencies need sequencing [S0002:Cchunk-2] .")

        self.assertIn("planning.", summary)
        self.assertIn("sequencing.", summary)
        self.assertNotIn("planning .", summary)
        self.assertNotIn("sequencing .", summary)

    def test_reference_text_removes_space_before_period(self) -> None:
        self.assertEqual(reporting.clean_reference_text("Market Outlook 2026 ."), "Market Outlook 2026.")

    def test_meta_template_has_font_fallbacks_and_gray_summary_box(self) -> None:
        template = reporting.REPORT_TEMPLATES["meta_research"]

        self.assertIn("Optimistic Display", template["heading_font_stack"])
        self.assertIn("Optimistic Text", template["body_font_stack"])
        self.assertIn("Challenger Inter", template["body_font_stack"])
        self.assertEqual(template["summary_block"], "meta-paper-abstract")
        self.assertEqual(template["cover_treatment"], "gray-paper-front-matter")
        self.assertIn(".meta-paper-abstract", template["css"])
        self.assertIn("#f1f3f5", template["css"])
        self.assertIn("text-align: justify", template["css"])

        assets = reporting.prepare_template_assets(template, image_mode="preview", asset_dir=None)
        self.assertIn("@font-face", assets["font_css"])
        self.assertIn("data:", assets["font_css"])

    def test_non_meta_templates_use_tighter_margins_and_smaller_type(self) -> None:
        for template_id in ("mckinsey", "iclr", "neurips", "nature_review"):
            template = reporting.REPORT_TEMPLATES[template_id]
            self.assertEqual(template["geometry"], "margin=0.35in")
            self.assertIn("@page { size: A4; margin: 0.35in 0.32in 0.38in; }", template["css"])

        ieee = reporting.REPORT_TEMPLATES["ieee_acm"]
        self.assertEqual(ieee["geometry"], "margin=0.32in")
        self.assertIn("h1 + p, .report-kicker { color: #111; text-transform: none;", ieee["css"])
        self.assertIn("h2 { font-size: 13.5px; color: #111; text-transform: none;", ieee["css"])
        self.assertIn("a { color: #111; }", ieee["css"])
        self.assertIn(".template-ieee_acm .report-kicker { color: #111; text-transform: none;", reporting.RICH_REPORT_CSS)
        self.assertIn(".template-ieee_acm .report-chapter-title { column-span: all; font-size: 13.5px; text-transform: none; color: #111; }", reporting.RICH_REPORT_CSS)

        self.assertEqual(reporting.REPORT_TEMPLATES["meta_research"]["geometry"], "margin=0.45in")
        self.assertEqual(reporting.REPORT_TEMPLATES["latex_original"]["geometry"], "margin=0.9in")

    def test_output_dir_must_be_absolute_or_home_relative(self) -> None:
        with self.assertRaises(ValueError):
            reporting.validate_output_dir("relative/path")

    def test_pandoc_pdf_command_uses_xelatex(self) -> None:
        command = reporting.pandoc_command(Path("/tmp/report.md"), Path("/tmp/report.pdf"), "pdf", reporting.REPORT_TEMPLATES["latex_original"])

        self.assertIn("--pdf-engine=xelatex", command)
        self.assertIn("/tmp/report.pdf", command)

    def test_pdf_safe_html_removes_weasyprint_hostile_css(self) -> None:
        html = """
<html><head><style>
p { text-justify: inter-word; font-weight: 760; box-shadow: 0 1px 3px #000; }
.x { backdrop-filter: blur(4px); column-count: 2; column-span: all; break-inside: avoid; }
@media (max-width: 760px) { .x { column-count: 1; } }
</style></head><body></body></html>
"""
        cleaned = reporting.pdf_safe_html(html)

        self.assertNotIn("text-justify", cleaned)
        self.assertNotIn("box-shadow", cleaned)
        self.assertNotIn("backdrop-filter", cleaned)
        self.assertNotIn("column-count", cleaned)
        self.assertNotIn("column-span", cleaned)
        self.assertNotIn("break-inside", cleaned)
        self.assertNotIn("@media (max-width", cleaned)
        self.assertIn("font-weight: 700", cleaned)

    @unittest.skipUnless(shutil.which("pandoc") and shutil.which("xelatex"), "pandoc and xelatex are required")
    def test_latex_original_preview_uses_pandoc_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)

            result = reporting.build_report_preview(str(corpus), "latex_original")

            self.assertEqual(result["preview_mode"], "pdf")
            self.assertIn("/publish/report/preview.pdf", result["preview_url"])
            self.assertTrue(Path(result["pdf_preview_path"]).exists())
            self.assertEqual(Path(result["pdf_preview_path"]).read_bytes()[:4], b"%PDF")
            if shutil.which("pdftoppm"):
                self.assertTrue(result["preview_page_urls"])
            self.assertIn('class="template-latex_original"', result["html"])
            self.assertNotIn('<article class="rich-report', result["html"])

    @unittest.skipUnless(shutil.which("pandoc") and reporting.html_pdf_renderer_path(), "pandoc and WeasyPrint are required")
    def test_preview_builds_pdf_from_rich_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)

            result = reporting.build_report_preview(
                str(corpus),
                "meta_research",
                metadata={"authors": "Research Team", "organization": "Challenger Deep", "website": "https://example.test"},
            )

            self.assertIn("<html", result["html"])
            self.assertEqual(result["preview_mode"], "pdf")
            self.assertIn("/publish/report/preview.pdf", result["preview_url"])
            self.assertTrue(Path(result["pdf_preview_path"]).exists())
            self.assertEqual(Path(result["pdf_preview_path"]).read_bytes()[:4], b"%PDF")
            if shutil.which("pdftoppm"):
                self.assertTrue(result["preview_page_urls"])
            self.assertIn("Demo report", result["html"])
            self.assertIn("template-meta_research", result["html"])
            self.assertIn("report-opening", result["html"])
            self.assertIn("meta-paper-abstract", result["html"])
            self.assertIn("challenger-wordmark", result["html"])
            self.assertIn("🤿", result["html"])
            self.assertIn("Challenger Deep", result["html"])
            self.assertNotIn("meta-wordmark", result["html"])
            self.assertNotIn("challenger-mark", result["html"])
            self.assertIn("Research Team", result["html"])
            self.assertIn("Optimistic Display", result["html"])
            self.assertIn("Optimistic Text", result["html"])
            self.assertIn("Challenger Inter", result["html"])
            self.assertIn("padding-top: 26px", result["html"])
            self.assertIn("@page { size: A4; margin: 0.45in 0.38in 0.48in; }", result["html"])
            self.assertIn(".template-meta_research .report-body p, .template-meta_research .report-body li, .template-meta_research .report-references li { font-size: 12.5px", result["html"])
            self.assertIn("font-size: 21px", result["html"])
            self.assertIn("clamp(16px, 2.5vw, 24px)", result["html"])
            self.assertIn("font-size: 10px", result["html"])
            self.assertIn(".template-meta_research .meta-paper-abstract p { font-size: 10px", result["html"])
            self.assertNotIn("[S0001:Cchunk-1]", result["html"])
            self.assertIn("https://one.test", result["html"])
            self.assertIn("<sup", result["html"])
            self.assertIn("reference-list", result["html"])
            self.assertTrue(reporting.report_md_path(corpus).exists())

    @unittest.skipUnless(shutil.which("pandoc"), "pandoc is not installed")
    def test_rich_template_html_structures_are_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)
            cover = Path(tmp) / "cover.png"
            cover.write_bytes(png_bytes())
            structure = reporting.build_report_structure(corpus, image_mode="preview", metadata={"cover_image_path": str(cover)})

            meta_html = reporting.render_rich_report_html(structure, reporting.REPORT_TEMPLATES["meta_research"])
            mckinsey_html = reporting.render_rich_report_html(structure, reporting.REPORT_TEMPLATES["mckinsey"])
            iclr_html = reporting.render_rich_report_html(structure, reporting.REPORT_TEMPLATES["iclr"])
            neurips_html = reporting.render_rich_report_html(structure, reporting.REPORT_TEMPLATES["neurips"])

            self.assertIn("template-meta_research", meta_html)
            self.assertIn("#f1f3f5", meta_html)
            self.assertIn("meta-paper-abstract", meta_html)
            self.assertIn("template-mckinsey", mckinsey_html)
            self.assertIn("report-exhibit", mckinsey_html)
            self.assertIn("--cover-image", mckinsey_html)
            self.assertIn("template-iclr", iclr_html)
            self.assertIn("Abstract", iclr_html)
            self.assertIn("template-neurips", neurips_html)
            self.assertIn("indented-abstract", reporting.REPORT_TEMPLATES["neurips"]["opening_block"])

    @unittest.skipUnless(shutil.which("pandoc") and reporting.html_pdf_renderer_path(), "pandoc and WeasyPrint are required")
    def test_export_smoke_for_rich_template_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)
            output_dir = Path(tmp) / "exports"

            manifest = reporting.export_report(
                str(corpus),
                "mckinsey",
                str(output_dir),
                ["pdf", "html", "docx", "latex"],
                metadata={"authors": "Research Team"},
            )

            formats = {row["format"] for row in manifest["outputs"]}
            self.assertTrue({"markdown", "pdf", "html", "docx", "latex"}.issubset(formats))
            self.assertEqual(manifest["metadata"]["authors"], "Research Team")
            self.assertTrue(any(command and "weasyprint" in Path(command[0]).name for command in manifest["commands"]))
            self.assertFalse(any("--pdf-engine=xelatex" in command for command in manifest["commands"] if isinstance(command, list)))
            for row in manifest["outputs"]:
                self.assertTrue(Path(row["path"]).exists())
            self.assertTrue(reporting.report_manifest_path(corpus).exists())

    @unittest.skipUnless(shutil.which("pandoc") and shutil.which("xelatex"), "pandoc and xelatex are required")
    def test_latex_original_pdf_keeps_xelatex_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_report_corpus(tmp)
            output_dir = Path(tmp) / "exports"

            manifest = reporting.export_report(str(corpus), "latex_original", str(output_dir), ["pdf"])

            self.assertTrue(any("--pdf-engine=xelatex" in command for command in manifest["commands"] if isinstance(command, list)))
            self.assertTrue(any(row["format"] == "pdf" and Path(row["path"]).exists() for row in manifest["outputs"]))


class PdfRendererLookupTests(unittest.TestCase):
    def test_finds_weasyprint_next_to_the_running_python_when_not_on_path(self) -> None:
        # e.g. the app started as .venv/bin/python run_app.py without activating the venv
        with tempfile.TemporaryDirectory() as scripts:
            command = Path(scripts) / "weasyprint"
            command.write_text("#!/bin/sh\n", encoding="utf-8")
            command.chmod(0o755)
            with patch.dict(os.environ, {"PATH": ""}), patch.object(sysconfig, "get_path", return_value=scripts):
                self.assertEqual(reporting.html_pdf_renderer_path(), str(command))


if __name__ == "__main__":
    unittest.main()
