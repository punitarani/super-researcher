from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from superresearcher import topic_discovery


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def chunk(
    chunk_id: str,
    text: str,
    source_id: str = "s1",
    title: str = "Source",
    publisher: str = "Publisher",
    section: str = "Section",
    section_paths: list[str] | None = None,
) -> dict[str, object]:
    metadata = {
        "source_id": source_id,
        "title": title,
        "publisher": publisher,
        "url": f"https://example.com/{source_id}",
        "section_path": section,
        "chunk_type": "text",
        "word_count": len(text.split()),
        "warning_flags": [],
    }
    if section_paths is not None:
        metadata["section_paths"] = section_paths
    return {"id": chunk_id, "text": text, "metadata": metadata}


class FakeLLM:
    def __init__(self, response: str = "- Market\n  - Demand", fail: bool = False) -> None:
        self.response = response
        self.fail = fail
        self.prompts: list[str] = []

    def text_call(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("fake llm failed")
        return self.response


class TopicDiscoveryTests(unittest.TestCase):
    def test_load_topic_units_excludes_rejected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "test_Corpus"
            atlas_dir = corpus / "atlas"
            write_jsonl(
                atlas_dir / "chunks.jsonl",
                [
                    chunk("keep", "market demand adoption"),
                    chunk("key", "certification safety evidence"),
                    chunk("maybe", "operational constraints"),
                    chunk("unmarked", "public acceptance"),
                    chunk("reject", "broken extraction noise"),
                ],
            )
            write_jsonl(
                atlas_dir / "selections.jsonl",
                [
                    {"chunk_id": "keep", "status": "keep"},
                    {"chunk_id": "key", "status": "key_evidence"},
                    {"chunk_id": "maybe", "status": "maybe"},
                    {"chunk_id": "reject", "status": "reject"},
                ],
            )

            units, counts = topic_discovery.load_topic_units(corpus)

        self.assertEqual([unit["id"] for unit in units], ["keep", "key", "maybe", "unmarked"])
        self.assertEqual(counts["excluded_reject"], 1)
        self.assertEqual(counts["included_keep"], 1)
        self.assertEqual(counts["included_key_evidence"], 1)
        self.assertEqual(counts["included_maybe"], 1)
        self.assertEqual(counts["included_unmarked"], 1)

    def test_toc_extraction_from_table_rows_and_dotted_leaders(self) -> None:
        units = [
            {
                "id": "c1",
                "text": "|1.2 Market Demand ................................ 11|\n2. Certification Pathway ........ 24",
                "metadata": {
                    "source_id": "s1",
                    "title": "Report A",
                    "publisher": "Publisher",
                    "section_path": "Report A > Table of Contents",
                },
                "selection_status": "unmarked",
            }
        ]

        entries = topic_discovery.extract_toc_entries(units)
        labels = [entry["label"] for entry in entries]

        self.assertIn("Market Demand", labels)
        self.assertIn("Certification Pathway", labels)
        self.assertFalse(topic_discovery.is_meaningful_label("Conclusion"))
        self.assertFalse(topic_discovery.is_meaningful_label("Acknowledgments"))
        self.assertTrue(all(entry["origin"] == "toc" for entry in entries))

    def test_heading_extraction_from_section_path_and_section_paths(self) -> None:
        units = [
            {
                "id": "c1",
                "text": "charging and vertiport text",
                "metadata": {
                    "source_id": "s1",
                    "title": "Report A",
                    "publisher": "Publisher",
                    "section_path": "Report A > Infrastructure > Charging Operations",
                    "section_paths": ["Report A > Infrastructure > Vertiport Siting"],
                },
                "selection_status": "unmarked",
            }
        ]

        headings = topic_discovery.extract_heading_candidates(units)
        labels = [entry["label"] for entry in headings]

        self.assertIn("Infrastructure", labels)
        self.assertIn("Charging Operations", labels)
        self.assertIn("Vertiport Siting", labels)
        self.assertTrue(all(entry["origin"] == "heading" for entry in headings))

    def test_candidates_are_combined_before_dedupe(self) -> None:
        toc = [{"label": "Certification Pathway", "normalized_label": "certification pathway"}]
        headings = [{"label": "Airspace Integration", "normalized_label": "airspace integration"}]

        combined = topic_discovery.combine_candidates(toc, headings)

        self.assertEqual(combined, toc + headings)

    def test_dedupe_merges_exact_and_fuzzy_duplicates_preserving_evidence(self) -> None:
        units = [
            {
                "id": "toc",
                "text": "1. Certification Pathway ........ 10",
                "metadata": {"source_id": "s1", "title": "A", "publisher": "P1", "section_path": "A > Table of Contents"},
                "selection_status": "keep",
            },
            {
                "id": "heading",
                "text": "certification",
                "metadata": {"source_id": "s2", "title": "B", "publisher": "P2", "section_path": "B > Certification Pathways"},
                "selection_status": "maybe",
            },
        ]
        candidates = topic_discovery.extract_toc_entries(units) + topic_discovery.extract_heading_candidates(units)

        deduped = topic_discovery.dedupe_candidates(candidates)
        merged = next(item for item in deduped if item["normalized_label"] == "certification pathway")

        self.assertEqual(merged["label"], "Certification Pathway")
        self.assertIn("Certification Pathways", merged["aliases"])
        self.assertEqual(merged["source_count"], 2)
        self.assertIn("toc", merged["chunk_ids"])
        self.assertIn("heading", merged["chunk_ids"])
        self.assertEqual(merged["curation_mix"]["keep"], 1)
        self.assertEqual(merged["curation_mix"]["maybe"], 1)

    def test_run_topic_discovery_writes_outputs_and_sends_deduped_candidates_to_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "shape_Corpus"
            atlas_dir = corpus / "atlas"
            write_jsonl(
                atlas_dir / "chunks.jsonl",
                [
                    chunk("toc", "|1. Market Demand ...... 1|", "s1", "Report", "P", "Report > Table of Contents"),
                    chunk("body", "raw body text should not be sent", "s2", "Body", "P", "Body > Market Demand"),
                ],
            )
            write_jsonl(atlas_dir / "selections.jsonl", [{"chunk_id": "toc", "status": "keep"}])
            (corpus / "atlas" / "topics").mkdir(parents=True)
            (corpus / "atlas" / "topics" / "topic_tree.json").write_text("stale", encoding="utf-8")
            llm = FakeLLM("- Market\n  - Market Demand")

            result = topic_discovery.run_topic_discovery(corpus=str(corpus), llm=llm)

            out = corpus / "atlas" / "topics"
            self.assertEqual(result["summary"]["llm_status"], "success")
            self.assertTrue((out / "toc_entries.json").exists())
            self.assertTrue((out / "toc_entries.md").exists())
            self.assertTrue((out / "topic_candidates.json").exists())
            self.assertTrue((out / "topic_candidates.md").exists())
            self.assertTrue((out / "deduped_subtopics.json").exists())
            self.assertTrue((out / "deduped_subtopics.md").exists())
            self.assertTrue((out / "topic_tree.md").exists())
            self.assertTrue((out / "topic_discovery_summary.md").exists())
            self.assertTrue((out / "llm_prompt.md").exists())
            self.assertFalse((out / "topic_tree.json").exists())
            self.assertIn("- Market", (out / "topic_tree.md").read_text(encoding="utf-8"))
            self.assertIn("Market Demand", llm.prompts[0])
            self.assertNotIn("raw body text should not be sent", llm.prompts[0])

    def test_llm_failure_writes_mined_artifacts_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "failure_Corpus"
            atlas_dir = corpus / "atlas"
            write_jsonl(atlas_dir / "chunks.jsonl", [chunk("toc", "1. Safety Case ...... 4", "s1", "Report", "P", "Report > Table of Contents")])
            write_jsonl(atlas_dir / "selections.jsonl", [])

            with self.assertRaises(RuntimeError):
                topic_discovery.run_topic_discovery(corpus=str(corpus), llm=FakeLLM(fail=True))

            out = corpus / "atlas" / "topics"
            self.assertTrue((out / "toc_entries.json").exists())
            self.assertTrue((out / "deduped_subtopics.json").exists())
            self.assertTrue((out / "llm_prompt.md").exists())
            self.assertTrue((out / "topic_discovery_summary.md").exists())
            self.assertFalse((out / "topic_tree.md").exists())
            self.assertIn("LLM status: failed", (out / "topic_discovery_summary.md").read_text(encoding="utf-8"))

    def test_flat_pdf_text_fails_with_next_steps_before_asking_the_agent(self) -> None:
        # Every chunk sits under ingest's own "Extracted PDF Text" label, so there's nothing to build topics from.
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "flat_Corpus"
            write_jsonl(
                corpus / "atlas" / "chunks.jsonl",
                [chunk(f"c{i}", "plain text", f"s{i}", f"Paper {i}", "P", "Extracted PDF Text") for i in range(5)],
            )
            write_jsonl(corpus / "atlas" / "selections.jsonl", [])
            llm = FakeLLM()

            with self.assertRaises(RuntimeError) as raised:
                topic_discovery.run_topic_discovery(corpus=str(corpus), llm=llm)

            self.assertEqual(llm.prompts, [])  # no agent call wasted on an empty outline
            self.assertIn("Post-process", str(raised.exception))
            deduped = json.loads((corpus / "atlas" / "topics" / "deduped_subtopics.json").read_text(encoding="utf-8"))
            self.assertNotIn("Extracted PDF Text", [row["label"] for row in deduped])

    def test_parse_topic_tree_markdown_defaults_selected_and_assigns_ids(self) -> None:
        tree = topic_discovery.parse_topic_tree_markdown(
            """
# Topic Tree

- Market Adoption
  - Demand Forecast
    - Enterprise Buyers
- Safety & Regulation
  * Certification Pathways
"""
        )

        self.assertEqual([node["id"] for node in tree], ["topic-1", "topic-2"])
        self.assertEqual(tree[0]["label"], "Market Adoption")
        self.assertEqual(tree[0]["children"][0]["id"], "topic-1-1")
        self.assertEqual(tree[0]["children"][0]["children"][0]["id"], "topic-1-1-1")
        self.assertTrue(tree[0]["selected"])
        self.assertTrue(tree[1]["children"][0]["selected"])

    def test_render_curated_topic_markdown_includes_only_selected_nodes(self) -> None:
        markdown = topic_discovery.render_curated_topic_markdown(
            [
                {
                    "label": "Market Adoption",
                    "selected": True,
                    "children": [
                        {"label": "Buyer Demand", "selected": True, "children": []},
                        {"label": "Dropped Signal", "selected": False, "children": []},
                    ],
                },
                {
                    "label": "Unselected Parent",
                    "selected": False,
                    "children": [{"label": "Selected Child", "selected": True, "children": []}],
                },
            ]
        )

        self.assertEqual(markdown, "- Market Adoption\n  - Buyer Demand\n")

    def test_save_curated_topic_tree_writes_separate_files_and_preserves_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = Path(tmp) / "demo_Corpus"
            topics_dir = corpus / "atlas" / "topics"
            topics_dir.mkdir(parents=True)
            raw = "- First Topic\n  - Keep This\n- Second Topic\n"
            raw_path = topics_dir / "topic_tree.md"
            raw_path.write_text(raw, encoding="utf-8")

            payload = {
                "topics": [
                    {"label": "Second Topic", "selected": True, "children": []},
                    {
                        "label": "First Topic",
                        "selected": True,
                        "children": [
                            {"label": "Drop This", "selected": False, "children": []},
                            {"label": "Keep This", "selected": True, "children": []},
                        ],
                    },
                ]
            }
            result = topic_discovery.save_curated_topic_tree(str(corpus), payload)

            self.assertEqual(raw_path.read_text(encoding="utf-8"), raw)
            curated_json = topics_dir / "curated_topic_tree.json"
            curated_md = topics_dir / "curated_topic_tree.md"
            self.assertTrue(curated_json.exists())
            self.assertTrue(curated_md.exists())
            self.assertEqual(curated_md.read_text(encoding="utf-8"), "- Second Topic\n- First Topic\n  - Keep This\n")
            saved = json.loads(curated_json.read_text(encoding="utf-8"))
            self.assertEqual(saved["corpus_id"], "demo_Corpus")
            self.assertEqual(saved["source_topic_tree_md_path"], str(raw_path.resolve()))
            self.assertEqual(saved["topics"][0]["id"], "topic-1")
            self.assertTrue(result["exists"])
            self.assertIn("Keep This", result["markdown"])


if __name__ == "__main__":
    unittest.main()
