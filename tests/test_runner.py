from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

from superresearcher import runner, search, server
from test_server_topics import request_json, topic_server

PAYLOAD = {"topic": "Stop test", "depth": "low", "breadth": "low", "final_source_count": 3}


class StopRunTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(runner.RUNS.clear)
        for patcher in (
            patch.object(runner, "ensure_storage_root", side_effect=lambda path: Path(path)),
            patch.object(runner, "load_api_keys", return_value={}),
            patch.object(runner, "LLMClient", return_value=MagicMock(agent="codex", unavailable=None)),
            patch.object(runner, "agent_status", return_value={"state": "ready", "ready": True, "label": "Codex", "message": ""}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_stop_takes_effect_after_the_current_step(self) -> None:
        entered, release = threading.Event(), threading.Event()

        def slow_protocol(payload, llm):
            entered.set()
            release.wait(5)
            return {"run_settings": {"final_source_count": 3}}

        with patch.object(runner, "build_protocol", side_effect=slow_protocol), patch.object(runner, "generate_heuristics") as heuristics:
            run = runner.ResearchRun({**PAYLOAD, "storage_root": str(self.root)})
            run.start()
            self.assertTrue(entered.wait(5))

            self.assertTrue(run.stop())
            during = run.snapshot()
            self.assertEqual(during["state"], "running")
            self.assertTrue(during["stop_requested"])

            release.set()
            run.thread.join(5)

        heuristics.assert_not_called()
        final = run.snapshot()
        self.assertEqual(final["state"], "stopped")
        self.assertEqual(final["milestone"], "Run stopped")
        self.assertIsNotNone(final["completed_at"])
        self.assertFalse(run.stop(), "a finished run can't be stopped again")
        saved = json.loads((run.dossier / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["state"], "stopped")

    def test_discovery_and_ingestion_check_for_stop_between_items(self) -> None:
        plan = {"batches": [{"query": f"q{i}"} for i in range(5)]}
        calls = []
        with patch.object(search, "search_batch", side_effect=lambda batch, keys, report: calls.append(batch) or []), patch.object(search.time, "sleep"):
            search.discover_candidates(plan, {}, should_stop=lambda: len(calls) >= 2)
        self.assertEqual(len(calls), 2)

        ingested = []
        with patch("superresearcher.ingest.ingest_one", side_effect=lambda row, *args: ingested.append(row) or row):
            from superresearcher.ingest import ingest_sources

            ingest_sources([{"url": "a"}, {"url": "b"}, {"url": "c"}], self.root, {}, should_stop=lambda: len(ingested) >= 1)
        self.assertEqual(len(ingested), 1)


    def test_a_stopped_discovery_is_not_reported_as_a_search_failure(self) -> None:
        plan = {"batches": [{"query": f"q{i}"} for i in range(5)]}
        calls = []

        def failing(batch, keys, report):
            calls.append(batch)
            report["errors"].setdefault("Exa", "Exa search failed: the API key was rejected (HTTP 401). Check EXA_API_KEY.")
            return []

        with patch.object(search, "search_batch", side_effect=failing), patch.object(search.time, "sleep"):
            self.assertEqual(search.discover_candidates(plan, {"EXA_API_KEY": "x"}, should_stop=lambda: len(calls) >= 1), [])

    def test_a_finished_run_never_shows_its_final_state_without_its_final_record(self) -> None:
        run = runner.ResearchRun({**PAYLOAD, "storage_root": str(self.root)})
        torn = []

        class CheckedLock:
            """Checks, whenever the lock is released, that a terminal state comes with its final milestone."""

            def __init__(self) -> None:
                self.inner = threading.Lock()

            def __enter__(self):
                self.inner.acquire()

            def __exit__(self, *exc):
                if run.status["state"] not in runner.ACTIVE_STATES and not run.status["milestone"].startswith("Run "):
                    torn.append((run.status["state"], run.status["milestone"]))
                self.inner.release()

        run._lock = CheckedLock()
        with patch.object(runner, "build_protocol", side_effect=RuntimeError("protocol failed")):
            run._run()
        self.assertEqual(run.snapshot()["state"], "failed")
        self.assertEqual(torn, [])

class RunHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.addCleanup(runner.RUNS.clear)

    def save(self, folder_name: str, **status) -> None:
        folder = self.root / folder_name
        folder.mkdir()
        body = {"run_id": folder_name, "topic": folder_name, "state": "completed", "events": [{"message": "hi"}], "files": {"a": "b"}, **status}
        (folder / "run.json").write_text(json.dumps(body), encoding="utf-8")

    def test_lists_saved_runs_newest_first_and_marks_orphans_interrupted(self) -> None:
        self.save("20260101-000000-old_Corpus")
        self.save("20260301-000000-orphan_Corpus", state="running")
        self.save("20260201-000000-mismatch_Corpus", run_id="something-else_Corpus")
        self.save("20260215-000000-odd-state_Corpus", state=[])  # hand-edited or foreign run.json
        (self.root / "20260401-000000-broken_Corpus").mkdir()
        (self.root / "20260401-000000-broken_Corpus" / "run.json").write_text("{not json", encoding="utf-8")

        rows = runner.list_runs(self.root)

        self.assertEqual([row["run_id"] for row in rows], ["20260301-000000-orphan_Corpus", "20260101-000000-old_Corpus"])
        self.assertEqual(rows[0]["state"], "interrupted")
        self.assertNotIn("events", rows[0])
        self.assertNotIn("files", rows[0])

    def test_get_run_reads_saved_runs_and_rejects_paths(self) -> None:
        self.save("20260101-000000-old_Corpus")
        run = runner.get_run("20260101-000000-old_Corpus", self.root)
        self.assertEqual(run["events"], [{"message": "hi"}])
        self.assertIsNone(runner.get_run("missing_Corpus", self.root))
        self.assertIsNone(runner.get_run("../20260101-000000-old_Corpus", self.root))
        self.assertIsNone(runner.get_run("not-a-corpus", self.root))

    def test_live_runs_win_over_saved_status(self) -> None:
        self.save("20260101-000000-live_Corpus", state="running")
        live = MagicMock()
        live.snapshot.return_value = {"run_id": "20260101-000000-live_Corpus", "state": "running", "events": []}
        runner.RUNS["20260101-000000-live_Corpus"] = live
        self.assertEqual(runner.list_runs(self.root)[0]["state"], "running")
        self.assertEqual(runner.get_run("20260101-000000-live_Corpus", self.root)["state"], "running")


class RunApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(runner.RUNS.clear)

    def test_stop_route(self) -> None:
        run = MagicMock()
        run.snapshot.return_value = {"run_id": "x_Corpus", "state": "running", "stop_requested": True}
        runner.RUNS["x_Corpus"] = run
        with topic_server() as base:
            run.stop.return_value = True
            self.assertTrue(request_json(f"{base}/api/runs/x_Corpus/stop", "POST", {})["stop_requested"])

            run.stop.return_value = False
            with self.assertRaises(urllib.error.HTTPError) as finished:
                request_json(f"{base}/api/runs/x_Corpus/stop", "POST", {})
            self.assertEqual(finished.exception.code, 409)

            with self.assertRaises(urllib.error.HTTPError) as missing:
                request_json(f"{base}/api/runs/missing_Corpus/stop", "POST", {})
            self.assertEqual(missing.exception.code, 404)

            with self.assertRaises(urllib.error.HTTPError) as form_post:
                request_json(f"{base}/api/runs/x_Corpus/stop", "POST")
            self.assertEqual(form_post.exception.code, 403)

            # A page that points its own domain at 127.0.0.1 (DNS rebinding) sends its own Host.
            rebound = urllib.request.Request(
                f"{base}/api/runs/x_Corpus/stop",
                data=b"{}",
                headers={"Content-Type": "application/json", "Host": "evil.example:8765"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as rebinding:
                urllib.request.urlopen(rebound, timeout=5)
            self.assertEqual(rebinding.exception.code, 403)

    def test_list_and_get_routes_use_run_history(self) -> None:
        with patch.object(server, "list_runs", return_value=[{"run_id": "a_Corpus"}]), patch.object(
            server, "get_run", side_effect=lambda run_id: {"run_id": run_id} if run_id == "a_Corpus" else None
        ):
            with topic_server() as base:
                self.assertEqual(request_json(f"{base}/api/runs"), [{"run_id": "a_Corpus"}])
                self.assertEqual(request_json(f"{base}/api/runs/a_Corpus"), {"run_id": "a_Corpus"})
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    request_json(f"{base}/api/runs/b_Corpus")
                self.assertEqual(missing.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
