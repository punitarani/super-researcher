from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from superresearcher import atlas, server
from test_server_topics import topic_server

# Every route that changes something. A plain-text POST is what any website can send
# cross-origin without a CORS preflight, so none of these may act on one.
MUTATIONS = [
    ("POST", "/api/runs"),
    ("POST", "/api/atlas/build"),
    ("POST", "/api/postprocess/markdown"),
    ("POST", "/api/topics/discover"),
    ("POST", "/api/compose/build"),
    ("POST", "/api/publish/plan"),
    ("POST", "/api/publish/compile"),
    ("POST", "/api/publish/visuals/build"),
    ("POST", "/api/publish/report/preview"),
    ("POST", "/api/publish/report/export"),
    ("POST", "/api/agents/codex/login"),
    ("PUT", "/api/agents"),
    ("PUT", "/api/atlas/demo_Corpus/selections"),
    ("PUT", "/api/atlas/demo_Corpus/topics/curated"),
]


def send(base: str, method: str, path: str, content_type: str) -> int:
    body = json.dumps({"topic": "x", "corpus_id": "demo_Corpus", "selected": "codex"}).encode()
    req = urllib.request.Request(f"{base}{path}", data=body, method=method, headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


class MutationGuardTests(unittest.TestCase):
    def test_plain_text_requests_cannot_change_anything(self) -> None:
        with patch.object(server, "ResearchRun") as research_run, patch.object(atlas, "start_atlas_job") as build, topic_server() as base:
            for method, path in MUTATIONS:
                with self.subTest(route=f"{method} {path}"):
                    self.assertEqual(send(base, method, path, "text/plain"), 403)
        research_run.assert_not_called()
        build.assert_not_called()

    def test_json_requests_from_this_computer_still_reach_the_route(self) -> None:
        with patch.object(server, "ResearchRun") as research_run, patch.object(server, "validate_payload"), topic_server() as base:
            research_run.return_value.snapshot.return_value = {"run_id": "x_Corpus", "state": "queued"}
            self.assertEqual(send(base, "POST", "/api/runs", "application/json"), 201)
        research_run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
