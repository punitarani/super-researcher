from __future__ import annotations

import unittest
from collections import Counter

from superresearcher.phase2 import select_sources


def candidates(publisher: str, count: int, top_score: int) -> list[dict]:
    return [{"url": f"https://{publisher}/{i}", "publisher": publisher, "score": top_score - i} for i in range(count)]


class SelectSourcesTests(unittest.TestCase):
    def test_fills_the_target_when_one_publisher_dominates(self) -> None:
        selected = select_sources(candidates("arxiv.org", 20, 100) + candidates("example.com", 2, 50), 15)

        self.assertEqual(len(selected), 15)
        self.assertEqual(Counter(s["publisher"] for s in selected)["example.com"], 2)

    def test_spreads_publishers_before_backfilling(self) -> None:
        pool = candidates("a.org", 10, 100) + candidates("b.org", 10, 80) + candidates("c.org", 10, 60) + candidates("d.org", 10, 40)

        selected = select_sources(pool, 16)

        self.assertEqual(len(selected), 16)
        # The first 75% of picks hold each publisher to its cap (3), so every publisher gets in.
        self.assertEqual(set(Counter(s["publisher"] for s in selected[:12]).values()), {3})

    def test_returns_everything_when_there_are_fewer_candidates_than_the_target(self) -> None:
        self.assertEqual(len(select_sources(candidates("arxiv.org", 5, 100), 15)), 5)


if __name__ == "__main__":
    unittest.main()
