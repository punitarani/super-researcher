from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from superresearcher import publish


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def png_header(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"


class FakeLLM:
    def __init__(self) -> None:
        self.section_calls = 0

    def json_call(self, prompt: str, fallback: object, step: str = "") -> object:
        return {
            "sections": [
                {"section_id": "bad", "title": "Invented", "source_topic_id": "missing", "rationale": "bad"},
                {"section_id": "ok", "title": "Safety First", "source_topic_id": "topic-2", "rationale": "lead with risk"},
            ]
        }

    def text_call(self, prompt: str, step: str = "") -> str:
        if "Compact the running paper summary" in prompt:
            return "Prior sections covered safety and market evidence."
        self.section_calls += 1
        return f"## Generated Section {self.section_calls}\n\nEvidence-backed text [S0001:Cchunk-1]."


def make_publish_corpus(tmp: str) -> Path:
    corpus = Path(tmp) / "demo_Corpus"
    write_json(corpus / "run.json", {"topic": "Demo research paper"})
    finalized = {
        "version": "compose-query-bundles-v1",
        "cache_signature": "sig",
        "corpus": {"id": corpus.name, "path": str(corpus)},
        "subtopics": [
            {
                "topic_id": "topic-1",
                "label": "Market Evidence",
                "path": ["Market", "Market Evidence"],
                "parent_label": "Market",
                "enabled": True,
                "tags": [{"id": "t1", "label": "market demand", "normalized": "market demand", "selected": True}],
            },
            {
                "topic_id": "topic-2",
                "label": "Safety Evidence",
                "path": ["Risk", "Safety Evidence"],
                "parent_label": "Risk",
                "enabled": True,
                "tags": [{"id": "t2", "label": "safety certification", "normalized": "safety certification", "selected": True}],
            },
        ],
        "summary": {"enabled_subtopic_count": 2, "selected_tag_count": 2},
    }
    write_json(corpus / "atlas" / "compose" / "finalized_terms.json", finalized)
    chunks = [
        {
            "id": "chunk-1",
            "text": "Market demand evidence and adoption data are strong.",
            "metadata": {"source_id": "0001", "title": "Market Study", "publisher": "one", "section_path": "Market > Demand", "url": "https://one.test"},
        },
        {
            "id": "chunk-2",
            "text": "Safety certification requirements and operational risk evidence.",
            "metadata": {"source_id": "0002", "title": "Safety Study", "publisher": "two", "section_path": "Safety > Certification", "url": "https://two.test"},
        },
        {
            "id": "chunk-3",
            "text": "Safety certification rejected text should not be used.",
            "metadata": {"source_id": "0003", "title": "Rejected", "publisher": "three", "section_path": "Safety", "url": "https://three.test"},
        },
    ]
    write_jsonl(corpus / "atlas" / "chunks.jsonl", chunks)
    write_jsonl(corpus / "atlas" / "selections.jsonl", [{"chunk_id": "chunk-3", "status": "reject"}])
    return corpus


def make_visual_corpus(tmp: str) -> Path:
    corpus = Path(tmp) / "visual_Corpus"
    asset_dir = corpus / "assets" / "0001"
    asset_dir.mkdir(parents=True, exist_ok=True)
    useful_bytes = png_header(240, 160) + (b"useful" * 8000)
    (asset_dir / "useful.png").write_bytes(useful_bytes)
    (asset_dir / "duplicate.png").write_bytes(useful_bytes)
    (asset_dir / "small-file.png").write_bytes(png_header(260, 160) + (b"small" * 1000))
    (asset_dir / "short-side.png").write_bytes(png_header(149, 400) + (b"side" * 12000))
    (asset_dir / "small-square.png").write_bytes(png_header(235, 240) + (b"square" * 9000))
    (asset_dir / "ocr.png").write_bytes(png_header(320, 180) + (b"ocr" * 14000))
    (asset_dir / "table.png").write_bytes(png_header(320, 180) + (b"table" * 12000))
    (asset_dir / "logo.png").write_bytes(png_header(32, 32) + b"logo")
    markdown = corpus / "markdown" / "0001-source.md"
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(
        "\n".join(
            [
                "# Source",
                "## Market demand",
                "Figure 1. Market demand chart for adoption.",
                "![Useful figure](../assets/0001/useful.png)",
                "![Duplicate figure](../assets/0001/duplicate.png)",
                "![Small file](../assets/0001/small-file.png)",
                "![Short side](../assets/0001/short-side.png)",
                "![Small square](../assets/0001/small-square.png)",
                "![Missing diagram](../assets/0001/missing.png)",
                "![OCR figure](../assets/0001/ocr.png)",
                "**----- Start of picture text -----**<br>",
                "Market adoption 2024 2025 2026<br>**----- End of picture text -----**<br>",
                "![Extracted table](../assets/0001/table.png)",
                "| Region | Share |",
                "| --- | --- |",
                "| US | 45% |",
                "![Remote chart](https://example.test/chart.png)",
                "![Logo](../assets/0001/logo.png)",
            ]
        ),
        encoding="utf-8",
    )
    write_jsonl(
        corpus / "ingested_sources.jsonl",
        [
            {
                "title": "Market Visual Source",
                "publisher": "visual.test",
                "url": "https://visual.test/source",
                "source_type": "pdf",
                "local_path": str(corpus / "originals" / "0001-source.pdf"),
                "markdown_path": str(markdown),
            }
        ],
    )
    write_jsonl(
        corpus / "atlas" / "chunks.jsonl",
        [
            {
                "id": "source_0001__chunk_0001",
                "text": markdown.read_text(encoding="utf-8"),
                "metadata": {
                    "source_id": "0001",
                    "title": "Market Visual Source",
                    "publisher": "visual.test",
                    "url": "https://visual.test/source",
                    "markdown_path": str(markdown),
                    "section_path": "Source > Market demand",
                },
            }
        ],
    )
    write_jsonl(corpus / "atlas" / "selections.jsonl", [])
    write_json(
        publish.publish_plan_path(corpus),
        {
            "version": publish.PUBLISH_VERSION,
            "sections": [
                {
                    "section_id": "section-001",
                    "title": "Market Demand",
                    "source_topic_id": "topic-1",
                    "source_path": ["Market", "Demand"],
                }
            ],
        },
    )
    return corpus


class PublishTests(unittest.TestCase):
    def test_toc_validation_drops_hallucinated_and_appends_missing(self) -> None:
        sections = [
            {"topic_id": "topic-1", "label": "Market Evidence", "path": ["Market"]},
            {"topic_id": "topic-2", "label": "Safety Evidence", "path": ["Safety"]},
        ]
        generated = {
            "sections": [
                {"title": "Invented", "source_topic_id": "fake"},
                {"title": "Safety First", "source_topic_id": "topic-2"},
            ]
        }

        validated = publish.validate_toc_sections(generated, sections)

        self.assertEqual([row["source_topic_id"] for row in validated], ["topic-2", "topic-1"])
        self.assertEqual(validated[0]["title"], "Safety First")

    def test_rank_chunks_excludes_rejected_and_matches_selected_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_publish_corpus(tmp)
            chunks, selections = publish.load_publish_units(corpus)
            finalized = publish.finalized_compose_payload(corpus)

            ranked = publish.rank_chunks_for_section(finalized["subtopics"][1], chunks, selections)

            self.assertTrue(ranked)
            self.assertEqual(ranked[0]["id"], "chunk-2")
            self.assertNotIn("chunk-3", [row["id"] for row in ranked])

    def test_compile_resumes_from_existing_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_publish_corpus(tmp)
            llm = FakeLLM()
            publish.create_publish_plan(str(corpus), llm=llm)
            plan = publish.read_json_object(publish.publish_plan_path(corpus))
            first = plan["sections"][0]
            first_path = publish.section_file_path(corpus, 1, first)
            first_path.parent.mkdir(parents=True, exist_ok=True)
            first_path.write_text("## Existing\n\nAlready done.\n", encoding="utf-8")
            write_json(
                publish.publish_state_path(corpus),
                {
                    "state": "running",
                    "completed_sections": [{"section_id": first["section_id"], "title": first["title"], "path": str(first_path), "chunk_count": 1}],
                    "continuity_summary": "Existing section summary.",
                },
            )

            result = publish.compile_publish_paper(str(corpus), llm=llm)

            completed = result["state"]["completed_sections"]
            self.assertEqual(len(completed), 2)
            self.assertEqual(llm.section_calls, 1)
            self.assertTrue(publish.publish_paper_path(corpus).exists())

    def test_compile_progress_ends_without_a_current_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_publish_corpus(tmp)
            counts: dict[str, object] = {}  # merged like PublishJob.event does

            publish.compile_publish_paper(str(corpus), llm=FakeLLM(), progress=lambda stage, progress, **c: counts.update(c))

            self.assertEqual(counts["completed_sections"], counts["section_count"])
            self.assertIsNone(counts["current_section"])

    def test_visual_candidates_exclude_remote_missing_and_dedupe_local_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_visual_corpus(tmp)

            candidates = publish.extract_visual_candidates(corpus)

            links = {row["original_link"] for row in candidates}
            self.assertIn("../assets/0001/useful.png", links)
            self.assertIn("../assets/0001/ocr.png", links)
            self.assertIn("../assets/0001/missing.png", links)
            self.assertNotIn("https://example.test/chart.png", links)
            self.assertNotIn("../assets/0001/duplicate.png", links)
            self.assertNotIn("../assets/0001/small-file.png", links)
            self.assertNotIn("../assets/0001/short-side.png", links)
            self.assertNotIn("../assets/0001/small-square.png", links)
            self.assertNotIn("../assets/0001/table.png", links)
            self.assertNotIn("../assets/0001/logo.png", links)
            ocr = next(row for row in candidates if row["original_link"].endswith("ocr.png"))
            self.assertEqual(ocr["extraction_status"], "text_extracted")
            missing = next(row for row in candidates if row["original_link"].endswith("missing.png"))
            self.assertTrue(missing["missing"])
            useful = next(row for row in candidates if row["original_link"].endswith("useful.png"))
            self.assertEqual(useful["width"], 240)
            self.assertEqual(useful["height"], 160)

    def test_visual_selection_generates_stable_placeholder_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_visual_corpus(tmp)
            built = publish.build_visual_candidates(str(corpus))
            candidate_id = built["candidates"]["items"][0]["candidate_id"]

            saved = publish.save_visual_selections(
                str(corpus),
                {"selections": {candidate_id: {"status": "add", "section_id": "section-001"}}},
            )
            first_placeholders = saved["plan"]["payload"]["placeholders"]
            saved_again = publish.save_visual_selections(str(corpus), {"selections": saved["selections"]["payload"]["selections"]})
            second_placeholders = saved_again["plan"]["payload"]["placeholders"]

            self.assertEqual(first_placeholders, second_placeholders)
            self.assertEqual(first_placeholders[0]["placeholder_id"], "fig_001_01")
            self.assertEqual(first_placeholders[0]["visual_id"], candidate_id)


if __name__ == "__main__":
    unittest.main()
