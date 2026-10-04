from __future__ import annotations

import ipaddress
import json
import mimetypes
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .config import DEFAULT_STORAGE_ROOT, DEPTH_RESULTS, FINAL_SOURCE_DEFAULT, FINAL_SOURCE_MAX, load_api_keys, load_app_settings, redact_keys, save_app_settings
from .runner import RUNS, ResearchRun, get_run, list_runs
from . import atlas, codex, llm, postprocess, publish, query_bundles, reporting, topic_discovery


def _web_dir() -> Path:
    # UI assets live inside the package, so both pip installs and git
    # checkouts serve the same files.
    return Path(__file__).resolve().parent / "web"


WEB = _web_dir()
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "[::1]"}


def web_file(root: Path, relative: str) -> Path | None:
    # Resolve first so "..", symlinks and absolute paths can't reach outside root.
    base = root.resolve()
    path = (base / relative).resolve()
    return path if base in path.parents else None


class Handler(BaseHTTPRequestHandler):
    server_version = f"SuperResearcher/{__version__}"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/":
            return self.serve_file(WEB / "index.html")
        if path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if path.startswith("/static/"):
            return self.serve_file(web_file(WEB, path.removeprefix("/static/")))
        if path.startswith("/assets/"):
            # embedding-atlas loads its workers from /assets/<file> at the page origin.
            return self.serve_file(web_file(WEB / "atlas" / "assets", path.removeprefix("/assets/")))
        if path == "/api/config":
            keys = load_api_keys()
            return self.send_json(
                {
                    "depth_options": DEPTH_RESULTS,
                    "default_storage_root": str(DEFAULT_STORAGE_ROOT),
                    "default_final_source_count": FINAL_SOURCE_DEFAULT,
                    "max_final_source_count": FINAL_SOURCE_MAX,
                    "configured_api_keys": redact_keys(keys),
                }
            )
        if path == "/api/agents":
            refresh = urllib.parse.parse_qs(parsed.query).get("refresh") == ["1"]
            return self.send_json(llm.agents_payload(refresh=refresh))
        if path == "/api/runs":
            return self.send_json(list_runs())
        if path.startswith("/api/runs/"):
            run = get_run(urllib.parse.unquote(path.removeprefix("/api/runs/")))
            if not run:
                return self.send_json({"error": "Run not found"}, status=404)
            return self.send_json(run)
        if path == "/api/corpora":
            corpora = atlas.list_corpora()
            return self.send_json({"corpora": corpora, "latest_corpus_id": atlas.latest_corpus_id()})
        if path == "/api/atlas/dependencies":
            return self.send_json(atlas.dependency_status())
        if path == "/api/postprocess/dependencies":
            return self.send_json(postprocess.markdown_readability_dependency_status())
        if path.startswith("/api/atlas/jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/api/atlas/jobs/"))
            job = atlas.get_job(job_id)
            if not job:
                return self.send_json({"error": "Atlas job not found"}, status=404)
            return self.send_json(job.snapshot())
        if path.startswith("/api/postprocess/jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/api/postprocess/jobs/"))
            job = postprocess.get_markdown_postprocess_job(job_id)
            if not job:
                return self.send_json({"error": "Post-processing job not found"}, status=404)
            return self.send_json(job.snapshot())
        if path.startswith("/api/topics/jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/api/topics/jobs/"))
            job = topic_discovery.get_topic_discovery_job(job_id)
            if not job:
                return self.send_json({"error": "Topic discovery job not found"}, status=404)
            return self.send_json(job.snapshot())
        if path.startswith("/api/compose/jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/api/compose/jobs/"))
            job = query_bundles.get_compose_build_job(job_id)
            if not job:
                return self.send_json({"error": "Compose job not found"}, status=404)
            return self.send_json(job.snapshot())
        if path.startswith("/api/publish/jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/api/publish/jobs/"))
            job = publish.get_publish_job(job_id)
            if not job:
                return self.send_json({"error": "Publish job not found"}, status=404)
            return self.send_json(job.snapshot())
        atlas_route = parse_atlas_route(path)
        if atlas_route:
            corpus_id, resource, tail = atlas_route
            try:
                if resource == "points" and not tail:
                    return self.send_json({"points": atlas.get_points(corpus_id)})
                if resource == "chunks" and len(tail) == 1:
                    chunk_id = urllib.parse.unquote(tail[0])
                    return self.send_json(atlas.get_chunk(corpus_id, chunk_id))
                if resource == "selections" and not tail:
                    return self.send_json({"selections": atlas.get_selections(corpus_id)})
                if resource == "topics" and not tail:
                    return self.send_json(topic_discovery.get_topic_payload(corpus_id))
                if resource == "topics" and tail == ["curated"]:
                    return self.send_json(topic_discovery.get_curated_topic_payload(corpus_id))
                if resource == "compose" and not tail:
                    return self.send_json(query_bundles.get_compose_payload(corpus_id))
                if resource == "publish" and not tail:
                    return self.send_json(publish.get_publish_payload(corpus_id))
                if resource == "publish" and tail == ["report", "preview.pdf"]:
                    return self.serve_file(reporting.report_preview_pdf_path_for_corpus(corpus_id))
                if resource == "publish" and len(tail) == 3 and tail[:2] == ["report", "preview-pages"]:
                    return self.serve_file(reporting.report_preview_page_path_for_corpus(corpus_id, urllib.parse.unquote(tail[2])))
                if resource == "publish" and tail == ["report"]:
                    return self.send_json(reporting.get_report_payload(corpus_id))
                if resource == "publish" and tail == ["visuals"]:
                    return self.send_json(publish.get_visual_payload(corpus_id))
                if resource == "publish" and len(tail) == 3 and tail[:2] == ["visuals", "assets"]:
                    return self.serve_visual_asset(corpus_id, urllib.parse.unquote(tail[2]))
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        return self.send_json({"error": "Not found"}, status=404)

    def do_POST(self) -> None:
        if not self.is_local_json_request():
            return self.refuse_remote_change()
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/agents/codex/login":
            try:
                return self.send_json({"url": codex.start_login()}, status=202)
            except codex.CodexError as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/stop"):
            run_id = urllib.parse.unquote(parsed.path.removeprefix("/api/runs/").removesuffix("/stop"))
            run = RUNS.get(run_id)
            if not run:
                return self.send_json({"error": "Run not found, or it isn't running in this app session."}, status=404)
            if not run.stop():
                return self.send_json({"error": "This run has already finished."}, status=409)
            return self.send_json(run.snapshot(), status=202)
        if parsed.path == "/api/atlas/build":
            try:
                payload = self.read_json()
                corpus = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                job = atlas.start_atlas_job(str(corpus), force=bool(payload.get("force")))
                return self.send_json(job.snapshot(), status=202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/postprocess/markdown":
            try:
                payload = self.read_json()
                corpus_id = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus_id:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                corpus = atlas.resolve_corpus(str(corpus_id))
                job = postprocess.start_markdown_postprocess_job(corpus)
                return self.send_json(job.snapshot(), status=202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/topics/discover":
            try:
                payload = self.read_json()
                corpus_id = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus_id:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                job = topic_discovery.start_topic_discovery_job(str(corpus_id), force=bool(payload.get("force")))
                return self.send_json(job.snapshot(), status=202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/compose/build":
            try:
                payload = self.read_json()
                corpus_id = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus_id:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                job = query_bundles.start_compose_build_job(str(corpus_id), force=bool(payload.get("force")))
                return self.send_json(job.snapshot(), status=202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/publish/plan":
            try:
                payload = self.read_json()
                corpus_id = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus_id:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                result = publish.create_publish_plan(str(corpus_id), custom_prompts=payload.get("custom_prompts") or None)
                return self.send_json(result, status=201)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/publish/compile":
            try:
                payload = self.read_json()
                corpus_id = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus_id:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                job = publish.start_publish_compile_job(
                    str(corpus_id),
                    custom_prompts=payload.get("custom_prompts") or None,
                    force_plan=bool(payload.get("force_plan")),
                )
                return self.send_json(job.snapshot(), status=202)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/publish/visuals/build":
            try:
                payload = self.read_json()
                corpus_id = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus_id:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                result = publish.build_visual_candidates(str(corpus_id))
                return self.send_json(result, status=201)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/publish/report/preview":
            try:
                payload = self.read_json()
                corpus_id = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus_id:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                result = reporting.build_report_preview(
                    str(corpus_id),
                    template_id=str(payload.get("template_id") or reporting.DEFAULT_TEMPLATE_ID),
                    metadata=payload.get("metadata") or None,
                )
                return self.send_json(result, status=201)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path == "/api/publish/report/export":
            try:
                payload = self.read_json()
                corpus_id = payload.get("corpus_path") or payload.get("corpus_id")
                if not corpus_id:
                    return self.send_json({"error": "corpus_path or corpus_id is required"}, status=400)
                result = reporting.export_report(
                    str(corpus_id),
                    template_id=str(payload.get("template_id") or reporting.DEFAULT_TEMPLATE_ID),
                    output_dir=str(payload.get("output_dir") or ""),
                    formats=payload.get("formats") or [],
                    metadata=payload.get("metadata") or None,
                )
                return self.send_json(result, status=201)
            except Exception as exc:
                return self.send_json({"error": str(exc)}, status=400)
        if parsed.path != "/api/runs":
            return self.send_json({"error": "Not found"}, status=404)
        try:
            payload = self.read_json()
            validate_payload(payload)
            run = ResearchRun(payload)
            run.start()
            return self.send_json(run.snapshot(), status=201)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, status=400)

    def do_PUT(self) -> None:
        if not self.is_local_json_request():
            return self.refuse_remote_change()
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/api/agents":
            try:
                agent = self.read_json().get("selected")
            except (ValueError, AttributeError):
                agent = None
            if agent not in llm.AGENTS:
                return self.send_json({"error": f"Unknown agent: {agent}"}, status=400)
            override = llm.agent_override()
            if override and override != agent:
                return self.send_json({"error": f"SUPERRESEARCHER_AGENT={override} is set, so the app always uses {llm.AGENTS[override]}. Unset it and restart the app to choose here."}, status=409)
            try:
                save_app_settings({**load_app_settings(), "agent": agent})
            except OSError as exc:
                return self.send_json({"error": f"Couldn't save the agent choice: {exc}"}, status=500)
            return self.send_json(llm.agents_payload())
        atlas_route = parse_atlas_route(parsed.path)
        if not atlas_route:
            return self.send_json({"error": "Not found"}, status=404)
        corpus_id, resource, tail = atlas_route
        try:
            payload = self.read_json()
            if resource == "selections" and not tail:
                return self.send_json({"selections": atlas.update_selections(corpus_id, payload)})
            if resource == "topics" and tail == ["curated"]:
                return self.send_json(topic_discovery.save_curated_topic_tree(corpus_id, payload))
            if resource == "compose" and tail == ["finalized"]:
                return self.send_json(query_bundles.save_finalized_compose_terms(corpus_id, payload))
            if resource == "publish" and tail == ["visuals", "selections"]:
                return self.send_json(publish.save_visual_selections(corpus_id, payload))
            return self.send_json({"error": "Not found"}, status=404)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, status=400)

    def refuse_remote_change(self) -> None:
        # Starting runs and jobs spends the user's agent quota and writes files, so only this app may do it.
        return self.send_json({"error": "Changes can only be made from the app on this computer."}, status=403)

    def is_local_json_request(self) -> bool:
        # Loopback-only, addressed to this computer by name (so a page that points its own domain
        # at 127.0.0.1, a DNS rebinding attack, can't use it), and JSON-only so other websites
        # can't trigger it with a plain form post.
        is_loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
        host = re.sub(r":\d+$", "", self.headers.get("Host", "")).lower()
        return is_loopback and host in LOCAL_HOSTNAMES and self.headers.get("Content-Type", "").startswith("application/json")

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def serve_file(self, path: Path | None) -> None:
        if path is None or not path.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_visual_asset(self, corpus_id: str, candidate_id: str) -> None:
        path = publish.visual_asset_path(corpus_id, candidate_id)
        if not path:
            self.send_error(404)
            return
        return self.serve_file(path)

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def validate_payload(payload: dict[str, Any]) -> None:
    if not payload.get("topic", "").strip():
        raise ValueError("Research topic is required.")
    if payload.get("depth") not in {"low", "medium", "high", "extra_high", "ludicrous"}:
        raise ValueError("Invalid depth setting.")
    if payload.get("breadth") not in {"low", "medium", "high"}:
        raise ValueError("Invalid breadth setting.")
    final = int(payload.get("final_source_count") or FINAL_SOURCE_DEFAULT)
    if final < 1:
        raise ValueError("Final source count must be at least 1.")
    payload["final_source_count"] = min(final, FINAL_SOURCE_MAX)
    payload["storage_root"] = payload.get("storage_root") or str(DEFAULT_STORAGE_ROOT)


def parse_atlas_route(path: str) -> tuple[str, str, list[str]] | None:
    prefix = "/api/atlas/"
    if not path.startswith(prefix):
        return None
    rest = path.removeprefix(prefix)
    if not rest or rest.startswith("jobs/") or rest == "build" or rest == "dependencies":
        return None
    parts = rest.split("/")
    if len(parts) < 2:
        return None
    return urllib.parse.unquote(parts[0]), urllib.parse.unquote(parts[1]), parts[2:]


def run_server(host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"SuperResearcher running at http://{host}:{port}")
    server.serve_forever()
    return server
