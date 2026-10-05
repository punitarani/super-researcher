from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from superresearcher import agent_log, codex, config, llm
from superresearcher.agent_log import AgentLog, redact
from superresearcher.llm import LLMClient, LLMError
from test_server_topics import topic_server


class RedactTests(unittest.TestCase):
    def test_configured_key_values_are_removed(self) -> None:
        text = "call with exa-0123456789abcdef and again exa-0123456789abcdef"
        self.assertEqual(redact(text, ["exa-0123456789abcdef"]), "call with [redacted] and again [redacted]")

    def test_common_credential_shapes_are_removed(self) -> None:
        samples = [
            "sk-proj-abcdefghijklmnopqrstuvwx",
            "AIzaSyA1234567890abcdefghijklmnopqrstuv",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "xoxb-1234567890-abcdefghij",
            "AKIAIOSFODNN7EXAMPLE",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
            "Authorization: Bearer abcdefghijklmnop0123456789",
        ]
        for sample in samples:
            with self.subTest(sample=sample[:12]):
                self.assertNotIn(sample.split()[-1], redact(f"before {sample} after"))

    def test_key_value_pairs_keep_their_name(self) -> None:
        self.assertEqual(redact('api_key="abcd1234efgh5678"'), 'api_key="[redacted]"')
        self.assertEqual(redact("password: hunter2hunter2"), "password: [redacted]")

    def test_quoted_keys_in_json_and_dicts_are_redacted(self) -> None:
        self.assertEqual(redact('{"password": "correcthorsebattery"}'), '{"password": "[redacted]"}')
        self.assertEqual(redact("{'api_key': '0123456789abcdef'}"), "{'api_key': '[redacted]'}")

    def test_ordinary_text_is_left_alone(self) -> None:
        text = "Keep the token budget under 512 and the secret sauce is evidence. A key finding: recall rose."
        self.assertEqual(redact(text), text)


class AgentLogTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        self.settings = self.run_dir / "app-settings.json"
        patcher = patch.object(config, "APP_SETTINGS_FILE", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, **keys: str) -> LLMClient:
        return LLMClient(keys, agent="codex", log=AgentLog(self.run_dir, keys.values()))

    def entries(self) -> list[dict]:
        index = self.run_dir / "logs" / "agent-log.jsonl"
        return [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()] if index.exists() else []

    def body(self, call: int) -> dict:
        calls = self.run_dir / "logs" / "agent-calls"
        return {part: (calls / f"{call:04d}-{part}.txt").read_text(encoding="utf-8") for part in ("prompt", "reply")}

    def test_an_answered_prompt_is_logged_with_its_step_and_reply(self) -> None:
        with patch.object(llm.codex, "complete", return_value='{"plan": 1}'):
            self.assertEqual(self.client().json_call("Plan the research", {}, step="Protocol: classify the topic"), {"plan": 1})

        [entry] = self.entries()
        self.assertEqual((entry["call"], entry["step"], entry["agent"], entry["outcome"], entry["sent"]), (1, "Protocol: classify the topic", "codex", "answered", True))
        self.assertIn("duration_ms", entry)
        self.assertEqual(self.body(1), {"prompt": "Plan the research", "reply": '{"plan": 1}'})

    def test_a_reply_that_isnt_json_falls_back_and_keeps_the_reply(self) -> None:
        with patch.object(llm.codex, "complete", return_value="Sorry, I can't help with that."):
            self.assertEqual(self.client().json_call("Plan", {"default": True}, step="Search heuristics: level 1"), {"default": True})

        [entry] = self.entries()
        self.assertEqual(entry["outcome"], "fell_back")
        self.assertTrue(entry["reason"])
        self.assertEqual(self.body(1)["reply"], "Sorry, I can't help with that.")

    def test_prompts_blocked_before_sending_are_logged_as_not_sent(self) -> None:
        client = self.client()
        client.unavailable = "Codex isn't signed in."
        client.json_call("Plan", {}, step="Protocol: rubric")
        with self.assertRaises(LLMError):
            client.text_call("Write", step="Compile: section 1")

        first, second = self.entries()
        self.assertEqual((first["outcome"], first["sent"], first["reason"]), ("fell_back", False, "Codex isn't signed in."))
        self.assertEqual((second["outcome"], second["sent"]), ("failed", False))

    def test_credentials_never_reach_the_log(self) -> None:
        reply = "Done. Debug: Bearer abcdefghijklmnop0123456789"
        with patch.object(llm.codex, "complete", return_value=reply):
            self.client(EXA_API_KEY="exa-secret-value-123").text_call("Search with exa-secret-value-123", step="Topic discovery: outline")

        written = (self.run_dir / "logs").rglob("*")
        everything = "".join(path.read_text(encoding="utf-8") for path in written if path.is_file())
        self.assertNotIn("exa-secret-value-123", everything)
        self.assertNotIn("abcdefghijklmnop0123456789", everything)
        self.assertIn("[redacted]", everything)

    def test_turning_the_log_off_writes_nothing(self) -> None:
        config.save_app_settings({"agent_log": False})
        with patch.object(llm.codex, "complete", return_value="{}"):
            self.client().json_call("Plan", {})
        self.assertFalse((self.run_dir / "logs").exists())

    def test_huge_prompts_are_clipped(self) -> None:
        with patch.object(llm.codex, "complete", return_value="ok"):
            self.client().text_call("x" * (agent_log.MAX_BODY_CHARS + 50))
        self.assertLessEqual(len(self.body(1)["prompt"]), agent_log.MAX_BODY_CHARS + 100)
        self.assertEqual(self.entries()[0]["prompt_chars"], agent_log.MAX_BODY_CHARS + 50)

    def test_a_log_that_cant_be_written_never_breaks_the_call(self) -> None:
        (self.run_dir / "logs").write_text("not a folder", encoding="utf-8")
        with patch.object(llm.codex, "complete", return_value="fine"):
            self.assertEqual(self.client().text_call("Write"), "fine")

    def test_text_that_cant_be_encoded_never_breaks_the_call(self) -> None:
        # A lone surrogate (e.g. from a "\\ud83d" JSON escape in scraped text) can't be written as UTF-8.
        with patch.object(llm.codex, "complete", return_value='{"ok": true}'):
            self.assertEqual(self.client().json_call("Plan \ud83d", {}), {"ok": True})

    def test_a_text_prompt_with_a_fallback_is_logged_as_using_defaults(self) -> None:
        with patch.object(llm.codex, "complete", side_effect=codex.CodexError("Codex didn't answer within 10 minutes.")):
            reply = self.client().text_call("Summarize", step="Compile: running summary after A", fallback="built-in summary")
        self.assertEqual(reply, "built-in summary")
        [entry] = self.entries()
        self.assertEqual((entry["outcome"], entry["sent"]), ("fell_back", True))

    def test_calls_are_numbered_without_rescanning_the_folder(self) -> None:
        client = self.client()
        with patch.object(llm.codex, "complete", return_value="ok"):
            client.text_call("one")
            with patch.object(Path, "glob", side_effect=AssertionError("rescanned")):
                client.text_call("two")
        self.assertEqual([entry["call"] for entry in self.entries()], [1, 2])

    def test_two_names_for_the_same_run_folder_share_one_numbering(self) -> None:
        alias = self.run_dir.parent / f"{self.run_dir.name}-alias"
        alias.symlink_to(self.run_dir)
        self.addCleanup(alias.unlink)
        run, job = self.client(), LLMClient({}, agent="codex", log=AgentLog(alias))
        with patch.object(llm.codex, "complete", return_value="ok"):
            run.text_call("from the run")
            job.text_call("from a job")
            run.text_call("from the run again")
        self.assertEqual([entry["call"] for entry in self.entries()], [1, 2, 3])

    def test_a_gemini_prompt_without_a_key_is_not_sent(self) -> None:
        client = LLMClient({}, agent="gemini", log=AgentLog(self.run_dir))
        client.json_call("Plan", {})
        self.assertFalse(self.entries()[0]["sent"])

    def test_client_for_logs_into_the_run_folder_and_redacts_configured_keys(self) -> None:
        with patch.object(llm, "load_api_keys", return_value={"SERPER_API_KEY": "serper-secret-999"}), patch.object(llm, "selected_agent", return_value="codex"):
            client = llm.client_for(self.run_dir)
        with patch.object(llm.codex, "complete", return_value="ok"):
            client.text_call("key serper-secret-999")
        self.assertEqual(self.body(1)["prompt"], "key [redacted]")


class SettingsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings = Path(tmp.name) / "app-settings.json"
        for patcher in (patch.object(config, "APP_SETTINGS_FILE", self.settings), patch.dict(os.environ, {"SUPERRESEARCHER_AGENT": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, base: str, method: str = "GET", body: bytes | None = None, content_type: str = "application/json") -> tuple[int, dict]:
        req = urllib.request.Request(f"{base}/api/settings", data=body, method=method, headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_the_agent_log_is_on_by_default_and_can_be_turned_off(self) -> None:
        with topic_server() as base:
            status, payload = self.request(base)
            self.assertEqual((status, payload["agent_log"]), (200, True))
            self.assertIn("logs/agent-log.jsonl", payload["agent_log_location"])

            status, payload = self.request(base, "PUT", b'{"agent_log": false}')
            self.assertEqual((status, payload["agent_log"]), (200, False))
            self.assertFalse(json.loads(self.settings.read_text())["agent_log"])

            self.assertEqual(self.request(base, "PUT", b'{"agent_log": "no"}')[0], 400)
            self.assertEqual(self.request(base, "PUT", b'{"agent_log": true}', content_type="text/plain")[0], 403)
        self.assertFalse(json.loads(self.settings.read_text())["agent_log"])


if __name__ == "__main__":
    unittest.main()
