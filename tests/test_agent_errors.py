from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from superresearcher import codex, config, llm, publish, runner
from superresearcher.codex import CodexStatus
from superresearcher.llm import LLMClient, LLMError
from test_server_topics import topic_server


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def gemini_reply(text: str) -> FakeResponse:
    return FakeResponse(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}).encode())


def gemini_rejection(code: int, message: str) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": {"code": code, "message": message, "status": "INVALID_ARGUMENT"}}).encode())
    return urllib.error.HTTPError("https://generativelanguage.googleapis.com/", code, "Bad Request", {}, body)


class GeminiTests(unittest.TestCase):
    def test_uses_a_current_model_and_keeps_the_key_out_of_the_url(self) -> None:
        sent: list[urllib.request.Request] = []
        with patch.object(llm.urllib.request, "urlopen", side_effect=lambda req, timeout: sent.append(req) or gemini_reply('{"ok": true}')):
            self.assertEqual(llm.gemini_call({"GEMINI_API_KEY": "secret-key"}, "hi", json_mode=True), '{"ok": true}')

        request = sent[0]
        self.assertIn("/models/gemini-flash-latest:generateContent", request.full_url)
        self.assertNotIn("secret-key", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "secret-key")

    def test_a_rejected_key_says_why_and_stops_further_prompts(self) -> None:
        client = LLMClient({"GEMINI_API_KEY": "wrong"}, agent="gemini")
        rejection = gemini_rejection(400, "API key not valid. Please pass a valid API key.")
        with patch.object(llm.urllib.request, "urlopen", side_effect=rejection) as urlopen:
            with self.assertRaises(LLMError) as raised:
                client.text_call("hi")
            self.assertIn("API key not valid", str(raised.exception))
            with self.assertRaises(LLMError):
                client.text_call("again")
        self.assertEqual(urlopen.call_count, 1)  # the second prompt failed fast


class FallbackNoticeTests(unittest.TestCase):
    def test_json_call_reports_when_it_uses_the_fallback(self) -> None:
        client = LLMClient({}, agent="codex")
        notices: list[str] = []
        client.on_fallback = notices.append
        with patch.object(llm.codex, "complete", side_effect=codex.CodexError("You've hit your usage limit.", fatal=True)):
            self.assertEqual(client.json_call("plan", {"default": True}), {"default": True})
        self.assertEqual(notices, ["You've hit your usage limit."])


class RunStartTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def start(self, **patches) -> runner.ResearchRun:
        run = runner.ResearchRun({"topic": "Agent errors", "storage_root": str(self.root), "depth": "low", "breadth": "low"})
        with patch.multiple(runner, **patches):
            run._run()
        return run

    def test_unreadable_api_keys_fail_the_run_instead_of_leaving_it_queued(self) -> None:
        run = self.start(load_api_keys=lambda: (_ for _ in ()).throw(OSError("Permission denied: api_keys.txt")))
        status = run.snapshot()
        self.assertEqual(status["state"], "failed")
        self.assertIn("Permission denied", status["error"])

    def test_a_transient_agent_check_failure_doesnt_pin_the_run_to_defaults(self) -> None:
        seen = {}

        def stop_after_check(payload, client):
            seen["unavailable"] = client.unavailable
            raise RuntimeError("stop the test run here")

        transient = {"id": "codex", "label": "Codex", "state": "error", "ready": False, "message": "Couldn't run Codex: timed out"}
        self.start(load_api_keys=lambda: {}, agent_status=lambda *a, **k: transient, build_protocol=stop_after_check)
        self.assertIsNone(seen["unavailable"])

    def test_steps_that_fall_back_to_defaults_say_so(self) -> None:
        ready = {"id": "codex", "label": "Codex", "state": "ready", "ready": True, "message": "Signed in"}

        def stop_after_protocol(*args, **kwargs):
            raise RuntimeError("stop the test run here")

        with patch.object(llm.codex, "complete", side_effect=codex.CodexError("You've hit your usage limit.", fatal=True)):
            run = self.start(load_api_keys=lambda: {}, agent_status=lambda *a, **k: ready, generate_heuristics=stop_after_protocol)
        messages = [event["message"] for event in run.snapshot()["events"]]
        notices = [m for m in messages if "usage limit" in m]
        self.assertEqual(len(notices), 1, messages)  # once, though both protocol prompts fell back
        self.assertIn("built-in defaults", notices[0])


class AgentRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patcher in (
            patch.object(config, "APP_SETTINGS_FILE", Path(tmp.name) / "app-settings.json"),
            patch.dict(os.environ, {"SUPERRESEARCHER_AGENT": ""}),
            patch.object(llm, "codex_bin", return_value=Path("codex")),
            patch.object(codex, "status", return_value=CodexStatus("signed_out", codex.SIGN_IN_HELP)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, base: str, path: str, method: str, body: bytes) -> tuple[int, dict]:
        req = urllib.request.Request(f"{base}{path}", data=body, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_publish_plan_needs_a_ready_agent(self) -> None:
        with patch.object(publish, "create_publish_plan") as create, topic_server() as base:
            status, payload = self.request(base, "/api/publish/plan", "POST", b'{"corpus_id": "demo_Corpus"}')
        self.assertEqual((status, payload["error"]), (400, codex.SIGN_IN_HELP))
        create.assert_not_called()

    def test_a_malformed_agent_choice_is_rejected(self) -> None:
        with topic_server() as base:
            status, payload = self.request(base, "/api/agents", "PUT", b'{"selected": ["codex"]}')
        self.assertEqual(status, 400)
        self.assertIn("Unknown agent", payload["error"])


if __name__ == "__main__":
    unittest.main()
