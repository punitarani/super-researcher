from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from superresearcher import server


class FakeJob:
    def __init__(self, job_id: str = "topics-demo") -> None:
        self.job_id = job_id

    def snapshot(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "corpus_id": "demo_Corpus",
            "state": "queued",
            "stage": "Queued",
            "progress": 0,
            "counts": {},
            "error": None,
        }


class FakeComposeJob(FakeJob):
    def __init__(self, job_id: str = "compose-demo") -> None:
        super().__init__(job_id)


class FakePublishJob(FakeJob):
    def __init__(self, job_id: str = "publish-demo") -> None:
        super().__init__(job_id)


@contextmanager
def topic_server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def request_json(url: str, method: str = "GET", payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def request_bytes(url: str) -> tuple[bytes, str]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read(), response.headers.get("Content-Type", "")


class TopicApiTests(unittest.TestCase):
    def test_topic_status_route(self) -> None:
        payload = {"corpus": {"id": "demo_Corpus"}, "raw": {"exists": True}, "topics": []}
        with patch.object(server.topic_discovery, "get_topic_payload", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/atlas/demo_Corpus/topics")

        mocked.assert_called_once_with("demo_Corpus")
        self.assertEqual(result, payload)

    def test_discover_job_creation_route(self) -> None:
        with patch.object(server.topic_discovery, "start_topic_discovery_job", return_value=FakeJob()) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/topics/discover", "POST", {"corpus_id": "demo_Corpus", "force": False})

        mocked.assert_called_once_with("demo_Corpus", force=False)
        self.assertEqual(result["job_id"], "topics-demo")
        self.assertEqual(result["state"], "queued")

    def test_curated_save_route(self) -> None:
        payload = {"topics": [{"label": "Market", "selected": True, "children": []}]}
        saved = {"exists": True, "tree": payload}
        with patch.object(server.topic_discovery, "save_curated_topic_tree", return_value=saved) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/atlas/demo_Corpus/topics/curated", "PUT", payload)

        mocked.assert_called_once_with("demo_Corpus", payload)
        self.assertEqual(result, saved)

    def test_topic_job_route(self) -> None:
        with patch.object(server.topic_discovery, "get_topic_discovery_job", return_value=FakeJob("topics-123")) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/topics/jobs/topics-123")

        mocked.assert_called_once_with("topics-123")
        self.assertEqual(result["job_id"], "topics-123")

    def test_missing_topic_job_returns_404(self) -> None:
        with patch.object(server.topic_discovery, "get_topic_discovery_job", return_value=None):
            with topic_server() as base:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    request_json(f"{base}/api/topics/jobs/missing")

        self.assertEqual(raised.exception.code, 404)

    def test_compose_status_route(self) -> None:
        payload = {"corpus": {"id": "demo_Corpus"}, "selected_subtopics": [], "active": None}
        with patch.object(server.query_bundles, "get_compose_payload", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/atlas/demo_Corpus/compose")

        mocked.assert_called_once_with("demo_Corpus")
        self.assertEqual(result, payload)

    def test_compose_build_job_creation_route(self) -> None:
        with patch.object(server.query_bundles, "start_compose_build_job", return_value=FakeComposeJob()) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/compose/build", "POST", {"corpus_id": "demo_Corpus", "force": True})

        mocked.assert_called_once_with("demo_Corpus", force=True)
        self.assertEqual(result["job_id"], "compose-demo")

    def test_compose_job_route(self) -> None:
        with patch.object(server.query_bundles, "get_compose_build_job", return_value=FakeComposeJob("compose-123")) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/compose/jobs/compose-123")

        mocked.assert_called_once_with("compose-123")
        self.assertEqual(result["job_id"], "compose-123")

    def test_compose_finalize_route(self) -> None:
        payload = {"subtopics": [{"topic_id": "topic-1", "enabled": True, "tags": []}]}
        saved = {"finalized": {"exists": True}}
        with patch.object(server.query_bundles, "save_finalized_compose_terms", return_value=saved) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/atlas/demo_Corpus/compose/finalized", "PUT", payload)

        mocked.assert_called_once_with("demo_Corpus", payload)
        self.assertEqual(result, saved)

    def test_publish_status_route(self) -> None:
        payload = {"corpus": {"id": "demo_Corpus"}, "ready": True}
        with patch.object(server.publish, "get_publish_payload", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/atlas/demo_Corpus/publish")

        mocked.assert_called_once_with("demo_Corpus")
        self.assertEqual(result, payload)

    def test_publish_plan_route(self) -> None:
        payload = {"plan": {"exists": True}}
        # Planning needs a ready agent; stub the check so the test doesn't depend on this machine's Codex sign-in.
        with patch.object(server.llm, "require_ready_agent"), patch.object(server.publish, "create_publish_plan", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/publish/plan", "POST", {"corpus_id": "demo_Corpus", "custom_prompts": {"topic-1": "focus"}})

        mocked.assert_called_once_with("demo_Corpus", custom_prompts={"topic-1": "focus"})
        self.assertEqual(result, payload)

    def test_publish_compile_job_creation_route(self) -> None:
        with patch.object(server.publish, "start_publish_compile_job", return_value=FakePublishJob()) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/publish/compile", "POST", {"corpus_id": "demo_Corpus", "custom_prompts": {}, "force_plan": True})

        mocked.assert_called_once_with("demo_Corpus", custom_prompts=None, force_plan=True)
        self.assertEqual(result["job_id"], "publish-demo")

    def test_publish_job_route(self) -> None:
        with patch.object(server.publish, "get_publish_job", return_value=FakePublishJob("publish-123")) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/publish/jobs/publish-123")

        mocked.assert_called_once_with("publish-123")
        self.assertEqual(result["job_id"], "publish-123")

    def test_publish_visual_status_route(self) -> None:
        payload = {"corpus": {"id": "demo_Corpus"}, "summary": {"candidate_count": 2}}
        with patch.object(server.publish, "get_visual_payload", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/atlas/demo_Corpus/publish/visuals")

        mocked.assert_called_once_with("demo_Corpus")
        self.assertEqual(result, payload)

    def test_publish_visual_build_route(self) -> None:
        payload = {"summary": {"candidate_count": 3}}
        with patch.object(server.publish, "build_visual_candidates", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/publish/visuals/build", "POST", {"corpus_id": "demo_Corpus"})

        mocked.assert_called_once_with("demo_Corpus")
        self.assertEqual(result, payload)

    def test_publish_visual_selection_save_route(self) -> None:
        payload = {"selections": {"vis-1": {"status": "add"}}}
        saved = {"summary": {"add_count": 1}}
        with patch.object(server.publish, "save_visual_selections", return_value=saved) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/atlas/demo_Corpus/publish/visuals/selections", "PUT", payload)

        mocked.assert_called_once_with("demo_Corpus", payload)
        self.assertEqual(result, saved)

    def test_report_status_route(self) -> None:
        payload = {"corpus": {"id": "demo_Corpus"}, "ready": True, "templates": []}
        with patch.object(server.reporting, "get_report_payload", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/atlas/demo_Corpus/publish/report")

        mocked.assert_called_once_with("demo_Corpus")
        self.assertEqual(result, payload)

    def test_report_preview_pdf_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preview_path = Path(tmp) / "report_preview.pdf"
            preview_path.write_bytes(b"%PDF-1.4\n%%EOF")

            with patch.object(server.reporting, "report_preview_pdf_path_for_corpus", return_value=preview_path) as mocked:
                with topic_server() as base:
                    data, content_type = request_bytes(f"{base}/api/atlas/demo_Corpus/publish/report/preview.pdf")

        mocked.assert_called_once_with("demo_Corpus")
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(content_type, "application/pdf")

    def test_report_preview_page_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            page_path = Path(tmp) / "page-01.png"
            page_path.write_bytes(b"\x89PNG\r\n\x1a\n")

            with patch.object(server.reporting, "report_preview_page_path_for_corpus", return_value=page_path) as mocked:
                with topic_server() as base:
                    data, content_type = request_bytes(f"{base}/api/atlas/demo_Corpus/publish/report/preview-pages/page-01.png")

        mocked.assert_called_once_with("demo_Corpus", "page-01.png")
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertEqual(content_type, "image/png")

    def test_report_preview_route(self) -> None:
        payload = {"html": "<html></html>", "warnings": []}
        metadata = {"title": "Demo"}
        with patch.object(server.reporting, "build_report_preview", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(
                    f"{base}/api/publish/report/preview",
                    "POST",
                    {"corpus_id": "demo_Corpus", "template_id": "mckinsey", "metadata": metadata},
                )

        mocked.assert_called_once_with("demo_Corpus", template_id="mckinsey", metadata=metadata)
        self.assertEqual(result, payload)

    def test_report_export_route(self) -> None:
        payload = {"outputs": [{"format": "pdf", "path": "/tmp/report.pdf"}]}
        metadata = {"title": "Demo"}
        request = {"corpus_id": "demo_Corpus", "template_id": "mckinsey", "output_dir": "/tmp/report", "formats": ["pdf"], "metadata": metadata}
        with patch.object(server.reporting, "export_report", return_value=payload) as mocked:
            with topic_server() as base:
                result = request_json(f"{base}/api/publish/report/export", "POST", request)

        mocked.assert_called_once_with("demo_Corpus", template_id="mckinsey", output_dir="/tmp/report", formats=["pdf"], metadata=metadata)
        self.assertEqual(result, payload)


if __name__ == "__main__":
    unittest.main()
