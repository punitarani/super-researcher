from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from superresearcher import config


class LoadApiKeysTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env = self.root / ".env"
        self.legacy = self.root / "api_keys.txt"
        for patcher in (patch.object(config, "API_KEYS_FILE", self.env), patch.object(config, "LEGACY_KEYS_FILE", self.legacy)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_reads_dotenv_syntax(self) -> None:
        self.env.write_text(
            "# Search providers\n"
            "EXA_API_KEY=exa-123\n"
            "export SERPER_API_KEY=serper-456\n"
            'GEMINI_API_KEY="gem # not a comment"\n'
            "CODEX_MODEL=gpt-6.1-sol  # optional override\n"
            "SERP_API_KEY=\n"
            "\n",
            encoding="utf-8",
        )

        self.assertEqual(
            config.load_api_keys(),
            {"EXA_API_KEY": "exa-123", "SERPER_API_KEY": "serper-456", "GEMINI_API_KEY": "gem # not a comment", "CODEX_MODEL": "gpt-6.1-sol"},
        )

    def test_a_leftover_api_keys_txt_still_works_and_env_wins(self) -> None:
        self.legacy.write_text("EXA_API_KEY=old-exa\nGEMINI_API_KEY: old-gemini\n", encoding="utf-8")
        self.env.write_text("EXA_API_KEY=new-exa\n", encoding="utf-8")

        self.assertEqual(config.load_api_keys(), {"EXA_API_KEY": "new-exa", "GEMINI_API_KEY": "old-gemini"})

    def test_no_files_means_no_keys(self) -> None:
        self.assertEqual(config.load_api_keys(), {})

    def test_an_explicit_file_is_read_on_its_own(self) -> None:
        # SUPERRESEARCHER_API_KEYS points at one file; the e2e tests rely on it to run without the user's keys.
        self.legacy.write_text("EXA_API_KEY=old-exa\n", encoding="utf-8")
        other = self.root / "no-keys.env"
        other.write_text("# nothing\n", encoding="utf-8")

        self.assertEqual(config.load_api_keys(other), {})


class DefaultKeysFileTests(unittest.TestCase):
    def test_keys_live_in_the_repo_dotenv(self) -> None:
        self.assertEqual(config.ENV_FILE, config.ROOT / ".env")


if __name__ == "__main__":
    unittest.main()
