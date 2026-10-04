from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from superresearcher import codex, config, llm, topic_discovery
from superresearcher.codex import CodexError, CodexStatus
from test_server_topics import topic_server

FAKE_CODEX = """#!{python}
import json, os, sys

args = sys.argv[1:]
with open(os.environ["FAKE_CODEX_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({{
        "args": args,
        "stdin": sys.stdin.read() if args[:1] == ["exec"] else "",
        "cwd_files": os.listdir("."),
        "api_key_env": sorted(k for k in ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY") if k in os.environ),
    }}) + "\\n")

login = os.environ.get("FAKE_CODEX_LOGIN", "chatgpt")
if args == ["--version"]:
    print("codex-cli " + os.environ.get("FAKE_CODEX_VERSION", "0.160.0"))
elif args == ["features", "list"]:
    print("apps                stable      true\\nshell_tool          stable      true\\nweb_search_cached   deprecated  false")
elif args == ["login", "status"]:
    lines = {{"chatgpt": "Logged in using ChatGPT", "api_key": "Logged in using an API key - sk-proj-***abcde"}}
    print(lines.get(login, "Not logged in"), file=sys.stderr)
    sys.exit(0 if login in lines else 1)
elif args[:1] == ["exec"]:
    outcome = os.environ.get("FAKE_CODEX_EXEC", "ok")
    if outcome == "ok":
        with open(args[args.index("--output-last-message") + 1], "w", encoding="utf-8") as reply:
            reply.write('```json\\n{{"answer": 42}}\\n```')
        print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 10, "output_tokens": 5}}}}))
        sys.exit(0)
    failures = {{
        "usage_limit": "You\\u2019ve hit your usage limit. Try again at 3:45 PM.",
        "expired": "unexpected status 401 Unauthorized: Missing bearer or basic authentication in header",
        "offline": "stream disconnected before completion: error sending request",
    }}
    print(json.dumps({{"type": "error", "message": "Reconnecting... 2/5 (stream disconnected)"}}))
    print(json.dumps({{"type": "turn.failed", "error": {{"message": failures[outcome]}}}}))
    sys.exit(1)
else:
    sys.exit(2)
"""


@unittest.skipIf(os.name == "nt", "the fake codex binary is a POSIX script")
class FakeCodexTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.binary = self.tmp / "codex"
        self.binary.write_text(FAKE_CODEX.format(python=sys.executable), encoding="utf-8")
        self.binary.chmod(0o755)
        self.log = self.tmp / "calls.jsonl"
        self.codex_home = self.tmp / "codex-home"
        self.codex_home.mkdir()
        env = {
            "FAKE_CODEX_LOG": str(self.log),
            "CODEX_HOME": str(self.codex_home),
            "CODEX_API_KEY": "sk-must-not-reach-codex",
            "OPENAI_API_KEY": "sk-must-not-reach-codex",
        }
        for patcher in (patch.dict(os.environ, env), patch.object(codex, "codex_bin", return_value=self.binary)):
            patcher.start()
            self.addCleanup(patcher.stop)
        codex.invalidate_status()
        self.addCleanup(codex.invalidate_status)

    def set_fake(self, **values: str) -> None:
        os.environ.update({f"FAKE_CODEX_{key.upper()}": value for key, value in values.items()})
        codex.invalidate_status()

    def calls(self, command: str) -> list[dict]:
        if not self.log.exists():
            return []
        rows = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row["args"][:1] == [command]]


class CodexStatusTests(FakeCodexTestCase):
    def test_status_reflects_how_codex_is_signed_in(self) -> None:
        for login, expected in (("chatgpt", "ready"), ("api_key", "wrong_auth"), ("signed_out", "signed_out")):
            with self.subTest(login=login):
                self.set_fake(login=login)
                self.assertEqual(codex.status().state, expected)

    def test_status_explains_missing_or_outdated_codex(self) -> None:
        with patch.object(codex, "codex_bin", return_value=None):
            missing = codex.status(refresh=True)
        self.assertEqual(missing.state, "not_installed")
        self.assertIn("npm install -g @openai/codex", missing.message)

        self.set_fake(version="0.100.0")
        outdated = codex.status()
        self.assertEqual(outdated.state, "outdated")
        self.assertIn("0.122.0", outdated.message)


class CodexExecTests(FakeCodexTestCase):
    def test_complete_runs_an_isolated_session_on_the_chatgpt_plan(self) -> None:
        reply = codex.complete("Summarize the corpus")

        self.assertIn('"answer": 42', reply)
        call = self.calls("exec")[-1]
        args = call["args"]
        for flag in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn('web_search="disabled"', args)
        disabled = [args[i + 1] for i, arg in enumerate(args) if arg == "--disable"]
        self.assertEqual(disabled, ["shell_tool", "apps"])  # only features this Codex build knows
        self.assertNotIn("--model", args)
        self.assertFalse(any("forced_login_method" in arg for arg in args))  # it can delete the user's login
        self.assertEqual(call["api_key_env"], [])
        self.assertEqual(call["cwd_files"], [])
        self.assertEqual(call["stdin"], "Summarize the corpus")

    def test_model_override_and_keyring_storage_are_forwarded(self) -> None:
        (self.codex_home / "config.toml").write_text(
            'cli_auth_credentials_store = "keyring"\n[profiles.work]\ncli_auth_credentials_store = "file"\n',
            encoding="utf-8",
        )
        codex.complete("hi", model="gpt-6.1-sol")

        args = self.calls("exec")[-1]["args"]
        self.assertIn('cli_auth_credentials_store="keyring"', args)
        self.assertEqual(args[args.index("--model") + 1], "gpt-6.1-sol")

    def test_failures_become_actionable_messages(self) -> None:
        self.set_fake(exec="usage_limit")
        with self.assertRaises(CodexError) as limit:
            codex.complete("hi")
        self.assertTrue(limit.exception.fatal)
        self.assertIn("Try again at 3:45 PM", str(limit.exception))

        self.set_fake(exec="expired")
        with self.assertRaises(CodexError) as expired:
            codex.complete("hi")
        self.assertTrue(expired.exception.fatal)
        self.assertIn("codex login", str(expired.exception))
        self.assertIsNone(codex._status_cache)  # next status check asks Codex again

        self.set_fake(exec="offline")
        with self.assertRaises(CodexError) as offline:
            codex.complete("hi")
        self.assertFalse(offline.exception.fatal)
        self.assertIn("internet connection", str(offline.exception))

    def test_complete_refuses_to_run_without_a_chatgpt_sign_in(self) -> None:
        self.set_fake(login="api_key")
        with self.assertRaises(CodexError) as raised:
            codex.complete("hi")

        self.assertTrue(raised.exception.fatal)
        self.assertEqual(str(raised.exception), codex.WRONG_AUTH_HELP)
        self.assertEqual(self.calls("exec"), [])


class AgentSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings_file = Path(tmp.name) / "app-settings.json"
        for patcher in (
            patch.object(config, "APP_SETTINGS_FILE", self.settings_file),
            patch.dict(os.environ, {"SUPERRESEARCHER_AGENT": ""}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_codex_failures_never_fall_back_to_gemini(self) -> None:
        client = llm.LLMClient({"GEMINI_API_KEY": "configured"}, agent="codex")
        failure = CodexError("Codex: You've hit your usage limit.", fatal=True)
        with patch.object(llm.codex, "complete", side_effect=failure) as complete, patch.object(llm, "gemini_call") as gemini:
            self.assertEqual(client.json_call("plan", {"fallback": True}), {"fallback": True})
            with self.assertRaisesRegex(llm.LLMError, "usage limit"):
                client.text_call("write")

        complete.assert_called_once()  # later calls skip Codex instead of failing slowly again
        gemini.assert_not_called()

    def test_selected_agent_is_saved_and_defaults_sensibly(self) -> None:
        with patch.object(llm, "codex_bin", return_value=None):
            self.assertEqual(llm.selected_agent({"GEMINI_API_KEY": "configured"}), "gemini")
            self.assertEqual(llm.selected_agent({}), "codex")  # so the UI shows how to install Codex
            config.save_app_settings({"agent": "codex"})
            self.assertEqual(llm.selected_agent({"GEMINI_API_KEY": "configured"}), "codex")


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings_file = Path(tmp.name) / "app-settings.json"
        signed_out = CodexStatus("signed_out", codex.SIGN_IN_HELP)
        for patcher in (
            patch.object(config, "APP_SETTINGS_FILE", self.settings_file),
            patch.dict(os.environ, {"SUPERRESEARCHER_AGENT": ""}),
            patch.object(llm, "codex_bin", return_value=Path("codex")),
            patch.object(codex, "status", return_value=signed_out),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, base: str, path: str, method: str = "GET", body: bytes | None = None, content_type: str = "application/json") -> tuple[int, dict]:
        req = urllib.request.Request(f"{base}{path}", data=body, method=method, headers={"Content-Type": content_type})
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_agent_routes_report_status_and_save_the_choice(self) -> None:
        with topic_server() as base:
            status, payload = self.request(base, "/api/agents")
            self.assertEqual(status, 200)
            self.assertEqual(payload["selected"], "codex")
            self.assertEqual([agent["id"] for agent in payload["agents"]], ["codex", "gemini"])
            self.assertEqual(payload["agents"][0]["state"], "signed_out")

            status, payload = self.request(base, "/api/agents", "PUT", b'{"selected": "gemini"}')
            self.assertEqual((status, payload["selected"]), (200, "gemini"))
            self.assertEqual(json.loads(self.settings_file.read_text())["agent"], "gemini")

            status, _ = self.request(base, "/api/agents", "PUT", b'{"selected": "other"}')
            self.assertEqual(status, 400)

    def test_sign_in_route_only_accepts_json_requests(self) -> None:
        with patch.object(codex, "start_login") as start_login, topic_server() as base:
            status, _ = self.request(base, "/api/agents/codex/login", "POST", b"{}", content_type="text/plain")

        self.assertEqual(status, 403)
        start_login.assert_not_called()

    def test_llm_jobs_fail_fast_with_setup_steps(self) -> None:
        with patch.object(topic_discovery, "resolve_topic_corpus", return_value=Path("demo_Corpus")), topic_server() as base:
            status, payload = self.request(base, "/api/topics/discover", "POST", b'{"corpus_id": "demo_Corpus"}')

        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], codex.SIGN_IN_HELP)


if __name__ == "__main__":
    unittest.main()
