from __future__ import annotations

import http.client
import unittest
import urllib.parse

from test_server_topics import topic_server


def fetch(base: str, path: str) -> tuple[int, str, bytes]:
    # http.client sends the path as-is, so ".." segments reach the server like they would from curl --path-as-is.
    url = urllib.parse.urlsplit(base)
    conn = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.getheader("Content-Type", ""), response.read()
    finally:
        conn.close()


class StaticFileTests(unittest.TestCase):
    def test_static_files_are_served(self) -> None:
        with topic_server() as base:
            status, content_type, _ = fetch(base, "/static/styles.css")
        self.assertEqual((status, content_type), (200, "text/css"))

    def test_static_paths_cannot_escape_the_web_folder(self) -> None:
        with topic_server() as base:
            for path in ("/static/../server.py", "/static/../../pyproject.toml", "/static/atlas/../../config.py", "/static/"):
                with self.subTest(path=path):
                    status, _, body = fetch(base, path)
                    self.assertEqual(status, 404)
                    self.assertNotIn(b"import", body)

    def test_atlas_workers_are_served_from_the_root_assets_path(self) -> None:
        # embedding-atlas hard-codes its worker URL as /assets/<file>, relative to the page origin.
        with topic_server() as base:
            status, content_type, body = fetch(base, "/assets/clustering.worker-D1Mz2-wD.js")
            self.assertEqual(status, 200)
            self.assertIn("javascript", content_type)
            self.assertTrue(body)

            status, _, _ = fetch(base, "/assets/../atlas.js")
            self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
