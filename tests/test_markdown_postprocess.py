from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from superresearcher import postprocess


class MarkdownReadabilityPostprocessTests(unittest.TestCase):
    def test_skips_rows_without_a_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            for paths in ({}, {"markdown_path": None}, {"markdown_path": ""}, {"markdown_path": "."}, {"markdown_path": str(corpus)}):
                with self.subTest(paths=paths):
                    row = {"source_type": "pdf", "fetch_status": "failed", **paths}

                    result = postprocess.postprocess_markdown_row(corpus, row)

                    self.assertEqual(result["row"], row)
                    self.assertEqual(result["scanned"], 0)
                    self.assertEqual(result["failed"], 0)

    def test_reflows_markdown_without_an_original_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            md = corpus / "paper.md"
            body = "\n".join(["A", "d", "v", "a", "n", "c", "e", "d"] * 30)
            for paths in ({}, {"local_path": None}, {"local_path": ""}, {"local_path": "."}, {"local_path": str(corpus)}):
                with self.subTest(paths=paths):
                    md.write_text(body, encoding="utf-8")
                    row = {"source_type": "pdf", "markdown_path": str(md), **paths}

                    result = postprocess.postprocess_markdown_row(corpus, row)

                    self.assertEqual(result["reflowed"], 1)
                    self.assertIn("Advanced", md.read_text(encoding="utf-8"))

    def test_corpus_processing_continues_after_a_failed_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            md = corpus / "paper.md"
            md.write_text("## Introduction\n\nReadable report text. " * 20, encoding="utf-8")
            rows = [
                {"source_type": "pdf", "fetch_status": "failed"},
                {"source_type": "pdf", "markdown_path": str(md)},
            ]
            postprocess.write_jsonl(corpus / "ingested_sources.jsonl", rows)

            summary = postprocess.postprocess_markdown_readability(corpus)

            self.assertEqual(summary["scanned"], 1)
            self.assertEqual(summary["pdf_scanned"], 1)
            self.assertEqual(summary["unchanged"], 1)
            self.assertEqual(summary["failed"], 0)
            self.assertEqual(postprocess.read_jsonl(corpus / "ingested_sources.jsonl"), rows)

    def test_detects_fragmented_pdf_markdown(self) -> None:
        body = "\n".join(["U", "A", "M", "Vis", "io", "n", "C", "o", "n", "c", "e", "p", "t"] * 20)
        metrics = postprocess.readability_metrics(body)

        self.assertTrue(metrics["damaged"])
        self.assertTrue(metrics["newline_damaged"])

    def test_detects_binary_fallback_gibberish(self) -> None:
        body = ("mó\n\x10 öò\x01z\x02\x01^>\x00 \x04 \x86C\x01\x10o\n" * 100) + "AAM Market Assessment Report"
        metrics = postprocess.readability_metrics(body)

        self.assertTrue(metrics["damaged"])
        self.assertTrue(metrics["binary_corrupt"])

    def test_detects_printable_raw_pdf_markdown_without_controls(self) -> None:
        body = "%PDF-1.7\n" + "\n".join(
            f"{index} 0 obj\n<</Filter/FlateDecode/Length 20>>stream\nabc\nendstream\nendobj"
            for index in range(1, 14)
        )
        metrics = postprocess.readability_metrics(body)

        self.assertEqual(metrics["nul_count"], 0)
        self.assertEqual(metrics["control_count"], 0)
        self.assertFalse(metrics["binary_corrupt"])
        self.assertTrue(metrics["damaged"])
        self.assertTrue(metrics["raw_pdf_corrupt"])

    def test_shorter_clean_candidate_can_replace_raw_pdf_markdown(self) -> None:
        old_body = "%PDF-1.7\n" + "\n".join(
            f"{index} 0 obj\n<</Filter/FlateDecode/Length 20>>stream\n{'ABCDEF' * 80}\nendstream\nendobj"
            for index in range(1, 18)
        )
        candidate = ("Clean extracted report text with readable sentences and normal Markdown structure. " * 80).strip()

        self.assertTrue(postprocess.candidate_is_better(old_body, candidate))

    def test_reflows_fragmented_text_without_touching_tables(self) -> None:
        body = "\n".join(
            ["U", "A", "M", "Vis", "io", "n", "", "| Metric | Value |", "| --- | --- |", "| Cost | 10 |"]
        )

        cleaned = postprocess.cleanup_newline_damage(body)

        self.assertIn("UAM Vision", cleaned)
        self.assertIn("| Metric | Value |", cleaned)

    def test_row_reflow_overwrites_sidecar_and_marks_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp)
            md = corpus / "markdown" / "0001-example.md"
            md.parent.mkdir()
            md.write_text(
                "# Example\n\n- Source type: pdf\n\n---\n\n" + "\n".join(["A", "d", "v", "a", "n", "c", "e", "d"] * 30),
                encoding="utf-8",
            )
            row = {
                "source_type": "pdf",
                "local_path": str(corpus / "originals" / "0001-example.pdf"),
                "markdown_path": str(md),
                "conversion_notes": ["pdf_fallback_text_only"],
            }

            result = postprocess.postprocess_markdown_row(corpus, row)

        self.assertEqual(result["reflowed"], 1)
        self.assertIn("postprocess_newline_reflowed", result["row"]["conversion_notes"])


    def flat_pdf_row(self, corpus: Path) -> dict:
        # What ingest writes with pdftotext: readable text, but no headings of the document's own.
        original = corpus / "originals" / "0001-paper.pdf"
        original.parent.mkdir()
        original.write_bytes(b"%PDF-1.7\n%fake\n")
        md = corpus / "markdown" / "0001-paper.md"
        md.parent.mkdir()
        prose = "Retrieval augmented generation systems are evaluated with faithfulness and relevance metrics. " * 40
        md.write_text("# Paper\n\n- Source type: academic\n\n---\n## Extracted PDF Text\n\n" + prose, encoding="utf-8")
        return {"source_type": "academic", "url": "https://arxiv.org/pdf/2401.00001", "local_path": str(original), "markdown_path": str(md)}

    def test_flat_pdf_text_is_rebuilt_with_the_documents_headings(self) -> None:
        structured = "# Paper\n\n## Abstract\n\n" + "Faithfulness and relevance metrics. " * 40 + "\n\n## 1 Introduction\n\n" + "RAG evaluation. " * 60
        with tempfile.TemporaryDirectory() as tmp, patch.object(postprocess, "pdf_via_pymupdf4llm", return_value=structured):
            corpus = Path(tmp)
            row = self.flat_pdf_row(corpus)
            result = postprocess.postprocess_markdown_row(corpus, row)
            rewritten = Path(row["markdown_path"]).read_text(encoding="utf-8")

        self.assertEqual((result["flagged"], result["reconverted"]), (1, 1))
        self.assertIn("## 1 Introduction", rewritten)
        self.assertTrue(rewritten.startswith("# Paper\n\n- Source type: academic"))  # source header kept

    def test_flat_pdf_text_is_left_alone_when_no_converter_adds_headings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(postprocess, "pdf_via_pymupdf4llm", side_effect=ImportError("pymupdf4llm")), patch.object(
            postprocess, "pdf_via_pymupdf", return_value="Plain text only. " * 80
        ):
            corpus = Path(tmp)
            row = self.flat_pdf_row(corpus)
            before = Path(row["markdown_path"]).read_text(encoding="utf-8")
            result = postprocess.postprocess_markdown_row(corpus, row)
            after = Path(row["markdown_path"]).read_text(encoding="utf-8")

        self.assertEqual((result["reconverted"], result["failed"], result["unchanged"]), (0, 0, 1))
        self.assertEqual(before, after)

if __name__ == "__main__":
    unittest.main()
