from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from . import codex
from .config import codex_bin, load_api_keys, load_app_settings


class LLMError(RuntimeError):
    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal  # every later prompt would fail the same way (bad key, unknown model)


# Google's alias for its current Flash model, so the default doesn't go stale (gemini-2.0-flash was shut down).
GEMINI_DEFAULT_MODEL = "gemini-flash-latest"


def extract_json(text: str) -> Any:
    text = text.strip()
    if not text:
        raise ValueError("empty response")
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [p for p in [start_obj, start_arr] if p >= 0]
    if not starts:
        raise
    start = min(starts)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise
    return json.loads(text[start : end + 1])


AGENTS = {"codex": "Codex", "gemini": "Gemini"}


def agent_override() -> str | None:
    """The agent forced by SUPERRESEARCHER_AGENT, which takes precedence over the saved choice."""
    agent = os.environ.get("SUPERRESEARCHER_AGENT")
    return agent if agent in AGENTS else None


def selected_agent(keys: dict[str, str]) -> str:
    agent = agent_override() or load_app_settings().get("agent")
    if agent in AGENTS:
        return agent
    return "gemini" if codex_bin() is None and gemini_key(keys) else "codex"


def agent_status(agent: str, keys: dict[str, str], refresh: bool = False) -> dict[str, Any]:
    if agent == "codex":
        current = codex.status(refresh)
        state, message = current.state, current.message
    elif gemini_key(keys):
        state, message = "ready", "Using your Gemini API key from api_keys.txt. Usage is billed to that key."
    else:
        state, message = "missing_key", "Add GEMINI_API_KEY to api_keys.txt (see api_keys.example.txt), then click Re-check."
    return {"id": agent, "label": AGENTS[agent], "state": state, "ready": state == "ready", "message": message}


def agents_payload(refresh: bool = False) -> dict[str, Any]:
    keys = load_api_keys()
    return {"selected": selected_agent(keys), "agents": [agent_status(agent, keys, refresh) for agent in AGENTS]}


def require_ready_agent() -> None:
    """Fail fast, with setup steps, when the selected agent can't take prompts."""
    keys = load_api_keys()
    status = agent_status(selected_agent(keys), keys)
    if not status["ready"]:
        raise LLMError(status["message"])


class LLMClient:
    """Sends prompts to one agent. Never falls back to another provider."""

    def __init__(self, keys: dict[str, str], agent: str | None = None) -> None:
        self.keys = keys
        self.agent = agent or selected_agent(keys)
        # Set after a failure every later call would repeat (signed out, usage limit), so jobs fail fast.
        self.unavailable: str | None = None
        # Told why, whenever a JSON prompt fails and the caller's built-in default is used instead.
        self.on_fallback: Callable[[str], None] | None = None

    def json_call(self, prompt: str, fallback: Any) -> Any:
        try:
            return extract_json(self._complete(prompt, json_mode=True))
        except Exception as exc:
            if self.on_fallback:
                self.on_fallback(str(exc))
            return fallback

    def text_call(self, prompt: str) -> str:
        return strip_markdown_fence(self._complete(prompt, json_mode=False))

    def _complete(self, prompt: str, json_mode: bool) -> str:
        if self.unavailable:
            raise LLMError(self.unavailable)
        if self.agent == "gemini":
            try:
                return gemini_call(self.keys, prompt, json_mode)
            except LLMError as exc:
                if exc.fatal:
                    self.unavailable = str(exc)
                raise
        try:
            return codex.complete(prompt, model=self.keys.get("CODEX_MODEL"))
        except codex.CodexError as exc:
            if exc.fatal:
                self.unavailable = str(exc)
            raise LLMError(str(exc)) from exc


def gemini_key(keys: dict[str, str]) -> str | None:
    return keys.get("GEMINI_API_KEY") or keys.get("GOOGLE_API_KEY")


def gemini_call(keys: dict[str, str], prompt: str, json_mode: bool) -> str:
    key = gemini_key(keys)
    if not key:
        raise LLMError("Gemini key not configured")
    model = keys.get("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{urllib.parse.quote(model, safe='')}:generateContent"
    generation_config = {"temperature": 0.7, "responseMimeType": "application/json"} if json_mode else {"temperature": 0.2}
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": generation_config}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        # The key goes in a header, not the URL, so it can't end up in logs or error messages.
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = gemini_error_detail(exc)
        # A bad key or model fails every prompt the same way; other errors (e.g. one oversized prompt) may not.
        fatal = exc.code in (401, 403, 404) or "api key" in detail.lower()
        raise LLMError(f"Gemini rejected the request (HTTP {exc.code}): {detail}", fatal=fatal) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"Couldn't reach Gemini: {exc.reason}") from exc
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    text = "".join(part.get("text", "") for part in parts)
    if not text:
        raise LLMError("empty Gemini response")
    return text


def gemini_error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.loads(exc.read().decode("utf-8", errors="replace"))["error"]["message"])
    except Exception:
        return str(exc.reason)


def strip_markdown_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()
