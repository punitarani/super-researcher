from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import patch

from superresearcher import search

BATCH = {
    "query": "rag evaluation",
    "search_mechanism": "web_search",
    "results_per_query": 3,
    "focus_area": "evaluation",
    "time_bucket": "current",
    "evidence_dimension": "methods",
    "expected_artifact_type": "paper",
}
PLAN = {"batches": [BATCH, BATCH, BATCH]}


def rejected(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.test", code, "Unauthorized", {}, None)


class DiscoverCandidatesTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(search.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fails_with_the_provider_and_key_when_every_search_is_rejected(self) -> None:
        messages: list[str] = []
        with patch.object(search, "search_exa", side_effect=rejected(401)):
            with self.assertRaises(RuntimeError) as caught:
                search.discover_candidates(PLAN, {"EXA_API_KEY": "wrong"}, progress=messages.append)

        self.assertIn("EXA_API_KEY", str(caught.exception))
        self.assertEqual(sum("Exa search failed" in m for m in messages), 1)  # reported once, not per search

    def test_reports_a_failing_provider_but_keeps_results_from_another(self) -> None:
        messages: list[str] = []
        found = [{"title": "Paper", "url": "https://arxiv.org/abs/2401.00001"}]
        with patch.object(search, "search_exa", side_effect=rejected(401)), patch.object(search, "search_serper", return_value=found):
            candidates = search.discover_candidates(PLAN, {"EXA_API_KEY": "wrong", "SERPER_API_KEY": "ok"}, progress=messages.append)

        self.assertEqual(len(candidates), 3)
        self.assertTrue(any("Exa search failed" in m and "EXA_API_KEY" in m for m in messages))

    def test_no_configured_provider_is_not_an_error(self) -> None:
        self.assertEqual(search.discover_candidates(PLAN, {}), [])


if __name__ == "__main__":
    unittest.main()
