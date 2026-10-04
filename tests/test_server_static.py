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


if __name__ == "__main__":
    unittest.main()
