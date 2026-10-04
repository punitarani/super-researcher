from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from superresearcher import query_bundles


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def make_compose_corpus(tmp: str) -> Path:
    corpus = Path(tmp) / "compose_Corpus"
    atlas = corpus / "atlas"
    rows = [
        {
            "id": "c1",
            "text": "Smart charging coordination, UAM charging demand, peak load, and power grid flow shape airport electrification.",
            "metadata": {"source_id": "s1", "title": "Grid Integration Report", "publisher": "grid.example", "section_path": "Infrastructure > Charging coordination, UAM charging demand, and power grid flow"},
        },
        {
            "id": "c2",
            "text": "Vertiport siting, TOLA access, and passenger transfer requirements shape facility planning.",
            "metadata": {"source_id": "s2", "title": "Vertiport Access Study", "publisher": "airport.example", "section_path": "Infrastructure > Vertiport and TOLA availability, access, and siting"},
        },
        {
            "id": "c3",
            "text": "Rejected safety text should not drive compose query terms.",
            "metadata": {"source_id": "s3", "title": "Rejected Study", "publisher": "reject.example", "section_path": "Safety > Certification"},
        },
    ]
    write_jsonl(atlas / "chunks.jsonl", rows)
    write_jsonl(atlas / "selections.jsonl", [{"chunk_id": "c1", "status": "keep"}, {"chunk_id": "c2", "status": "maybe"}, {"chunk_id": "c3", "status": "reject"}])
    write_json(atlas / "manifest.json", {"signature": "sig-1", "chunk_count": 3})
    write_json(
        atlas / "topics" / "curated_topic_tree.json",
        {
            "topics": [
                {
                    "id": "topic-1",
                    "label": "Infrastructure",
                    "selected": True,
                    "children": [
                        {"id": "topic-1-1", "label": "Charging coordination, UAM charging demand, and power grid flow", "selected": True, "children": []},
                        {"id": "topic-1-2", "label": "Vertiport and TOLA availability, access, and siting", "selected": True, "children": []},
                    ],
                }
            ]
        },
    )
    write_json(
        atlas / "topics" / "deduped_subtopics.json",
        [
            {"label": "Charging coordination", "aliases": ["UAM charging demand", "Power grid flow"], "chunk_ids": ["c1"], "section_paths": ["Infrastructure > Charging coordination, UAM charging demand, and power grid flow"], "source_count": 1},
            {"label": "Vertiport siting", "aliases": ["TOLA access"], "chunk_ids": ["c2"], "section_paths": ["Infrastructure > Vertiport and TOLA availability, access, and siting"], "source_count": 1},
        ],
    )
    write_json(atlas / "topics" / "toc_entries.json", [])
    return corpus


class ComposeQueryBundleTests(unittest.TestCase):
    def test_selected_leaf_subtopics_excludes_grouping_parents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_compose_corpus(tmp)

            leaves = query_bundles.selected_leaf_subtopics(corpus)

            labels = [item["label"] for item in leaves]
            self.assertEqual(len(labels), 2)
            self.assertNotIn("Infrastructure", labels)
            self.assertIn("Charging coordination, UAM charging demand, and power grid flow", labels)

    def test_context_excludes_rejected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_compose_corpus(tmp)

            context = query_bundles.load_query_bundle_context(corpus)

            self.assertEqual(context["filter_counts"]["excluded_reject"], 1)
            self.assertEqual(context["filter_counts"]["included_keep"], 1)
            self.assertEqual(context["filter_counts"]["included_maybe"], 1)

    def test_build_compose_query_bundles_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_compose_corpus(tmp)

            first = query_bundles.build_compose_query_bundles(str(corpus), force=True)
            second = query_bundles.build_compose_query_bundles(str(corpus), force=False)

            self.assertEqual(first["summary"]["subtopic_count"], 2)
            self.assertTrue(second["cached"])
            self.assertTrue((corpus / "atlas" / "compose" / "query_bundles.json").exists())
            self.assertTrue(all(item["tags"] for item in first["subtopics"]))

    def test_build_progress_ends_with_every_subtopic_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_compose_corpus(tmp)
            counts: dict[str, object] = {}  # merged like ComposeBuildJob.event does

            query_bundles.build_compose_query_bundles(str(corpus), force=True, progress=lambda stage, progress, **c: counts.update(c))

            self.assertEqual(counts["completed_subtopics"], 2)
            self.assertIsNone(counts["current_subtopic"])

    def test_finalize_writes_selected_terms_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            corpus = make_compose_corpus(tmp)
            generated = query_bundles.build_compose_query_bundles(str(corpus), force=True)
            generated["subtopics"][0]["tags"][0]["selected"] = False
            generated["subtopics"][1]["enabled"] = False

            status = query_bundles.save_finalized_compose_terms(str(corpus), {"subtopics": generated["subtopics"]})
            finalized = status["finalized"]["payload"]

            self.assertEqual(finalized["summary"]["enabled_subtopic_count"], 1)
            self.assertEqual(len(finalized["subtopics"]), 1)
            self.assertNotEqual(finalized["subtopics"][0]["tags"][0]["label"], generated["subtopics"][0]["tags"][0]["label"])
            self.assertTrue((corpus / "atlas" / "compose" / "finalized_terms.md").exists())


if __name__ == "__main__":
    unittest.main()
