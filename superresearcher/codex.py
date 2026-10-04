"""Codex agent: runs prompts on the user's ChatGPT plan through their own Codex CLI.

Codex owns sign-in (`codex login`) and token storage. This module only runs
Codex commands; it never reads, stores, or passes credentials.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import codex_bin

MIN_VERSION = (0, 122, 0)  # first release with `codex exec --ignore-user-config`
STATUS_TTL_SECONDS = 30
EXEC_TIMEOUT_SECONDS = 600

# Any of these in Codex's environment would bill an API account instead of the ChatGPT plan.
API_KEY_ENV = ("CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENAI_API_KEY")
# Tools and connectors a plain text prompt doesn't need, plus endless network retries.
# Codex rejects unknown feature names, so only those the installed version lists are disabled.
DISABLED_FEATURES = ("shell_tool", "unified_exec", "apps", "plugins", "unbounded_connection_retries")

INSTALL_HELP = (
    "Codex isn't installed. Install it with `npm install -g @openai/codex` or `brew install --cask codex`, "
    "then click Re-check. If it's installed somewhere unusual, set CODEX_BIN to its path and restart the app."
)
SIGN_IN_HELP = (
    "Codex isn't signed in. Click Sign in with ChatGPT, or run `codex login` in a terminal "
    "(`codex login --device-auth` on a machine without a browser)."
)
WRONG_AUTH_HELP = (
    "Codex is signed in with an API key or another method, so usage wouldn't come from your ChatGPT plan. "
    "Click Sign in with ChatGPT to switch (this replaces the current Codex sign-in), or run `codex login`."
)
EXPIRED_HELP = "Your ChatGPT sign-in for Codex has expired or was revoked. Click Sign in with ChatGPT or run `codex login`."

_AUTH_ERRORS = ("401", "unauthorized", "sign in again", "not logged in", "login is required")
_PLAN_ERRORS = ("usage limit", "out of credits", "spend cap", "upgrade to plus")
_NETWORK_ERRORS = ("connection failed", "stream disconnected", "error sending request", "timed out", "routing discovery")
_STDERR_NOISE = re.compile(r"^(WARNING|Reading additional input|\d{4}-\d\d-\d\dT)")


class CodexError(RuntimeError):
    def __init__(self, message: str, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal  # retrying won't help until the user acts (sign in, wait for limits)


@dataclass(frozen=True)
class CodexStatus:
    state: str  # ready | not_installed | outdated | signed_out | wrong_auth | error
    message: str

    @property
    def ready(self) -> bool:
        return self.state == "ready"


_status_cache: tuple[float, CodexStatus] | None = None
_login_process: subprocess.Popen[str] | None = None
_login_lock = threading.Lock()


def status(refresh: bool = False) -> CodexStatus:
    global _status_cache
    cached = _status_cache
    if not refresh and cached and time.monotonic() - cached[0] < STATUS_TTL_SECONDS:
        return cached[1]
    current = _check_status()
    _status_cache = (time.monotonic(), current)
    return current


def invalidate_status() -> None:
    global _status_cache
    _status_cache = None


def complete(prompt: str, model: str | None = None) -> str:
    """Run one prompt with `codex exec` in an isolated, read-only, tool-free session."""
    current = status()
    if not current.ready:
        raise CodexError(current.message, fatal=True)
    found = codex_bin()
    if found is None:
        invalidate_status()
        raise CodexError(INSTALL_HELP, fatal=True)
    binary = str(found)
    with tempfile.TemporaryDirectory(prefix="superresearcher-codex-") as tmp:
        workdir = Path(tmp, "work")
        workdir.mkdir()
        reply = Path(tmp, "reply.md")
        cmd = [
            binary, "exec", "--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--skip-git-repo-check", "--sandbox", "read-only", "--cd", str(workdir), "--color", "never",
            "--output-last-message", str(reply),
            "-c", 'web_search="disabled"', "-c", 'model_reasoning_effort="high"',
            *_credential_store_override(),
        ]
        available = _features(binary, Path(binary).stat().st_mtime)
        for feature in DISABLED_FEATURES:
            if feature in available:
                cmd += ["--disable", feature]
        if model:
            cmd += ["--model", model]
        cmd.append("-")  # prompt on stdin
        try:
            result = _run(cmd, prompt, timeout=EXEC_TIMEOUT_SECONDS, cwd=workdir)
        except subprocess.TimeoutExpired as exc:
            raise CodexError(f"Codex didn't answer within {EXEC_TIMEOUT_SECONDS // 60} minutes.") from exc
        except OSError as exc:
            raise CodexError(f"Couldn't run Codex at {binary}: {exc}") from exc
        if result.returncode == 0 and reply.exists():
            return reply.read_text(encoding="utf-8")
        raise _classify_failure(result)


def start_login(timeout: float = 15) -> str:
    """Start `codex login` and return its sign-in URL.

    Codex opens the browser, receives the OAuth callback on its own localhost
    server, and stores the tokens itself; the app only sees the public URL.
    """
    global _login_process
    binary = codex_bin()
    if binary is None:
        raise CodexError(INSTALL_HELP)
    with _login_lock:
        if _login_process and _login_process.poll() is None:
            _login_process.terminate()
        process = subprocess.Popen(
            [str(binary), "login"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_codex_env(),
        )
        _login_process = process
    first_url: queue.Queue[str] = queue.Queue()
    threading.Thread(target=_watch_login, args=(process, first_url), daemon=True).start()
    try:
        found = first_url.get(timeout=timeout)
    except queue.Empty:
        raise CodexError("Codex didn't start the sign-in. Run `codex login` in a terminal instead.") from None
    if not found.startswith("https://"):
        raise CodexError(f"Codex couldn't start the sign-in: {found or 'no output'}")
    return found


def _watch_login(process: subprocess.Popen[str], first_url: queue.Queue[str]) -> None:
    output = []
    for line in process.stdout or ():
        output.append(line)
        match = re.search(r"https://\S+", line)
        if match:
            first_url.put(match.group(0))
    process.wait()
    invalidate_status()
    first_url.put(_first_meaningful_line("".join(output)))  # only read if no URL came first


def _check_status() -> CodexStatus:
    binary = codex_bin()
    if binary is None:
        return CodexStatus("not_installed", INSTALL_HELP)
    try:
        version = _parse_version(_run([str(binary), "--version"], timeout=20).stdout)
        if version and version < MIN_VERSION:
            needed = ".".join(map(str, MIN_VERSION))
            return CodexStatus(
                "outdated",
                f"Codex {'.'.join(map(str, version))} is too old (this app needs {needed} or newer). "
                "Update it with `npm install -g @openai/codex@latest` or `brew upgrade --cask codex`, then click Re-check.",
            )
        login = _run([str(binary), "login", "status"], timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return CodexStatus("error", f"Couldn't run Codex at {binary}: {exc}")
    output = login.stdout + login.stderr  # Codex prints the status to stderr
    if "Logged in using ChatGPT" in output:
        return CodexStatus("ready", "Signed in with ChatGPT. Prompts run on your ChatGPT plan.")
    if login.returncode == 0:
        return CodexStatus("wrong_auth", WRONG_AUTH_HELP)
    if "Not logged in" in output:
        return CodexStatus("signed_out", SIGN_IN_HELP)
    return CodexStatus("error", f"Couldn't check Codex sign-in: {_first_meaningful_line(login.stderr)}")


def _classify_failure(result: subprocess.CompletedProcess[str]) -> CodexError:
    message = (
        _failure_from_events(result.stdout)
        or _first_meaningful_line(result.stderr)
        or f"Codex exited with code {result.returncode}."
    )
    lowered = message.lower().replace("’", "'")
    if any(token in lowered for token in _AUTH_ERRORS):
        invalidate_status()
        return CodexError(EXPIRED_HELP, fatal=True)
    if any(token in lowered for token in _PLAN_ERRORS):
        return CodexError(f"Codex: {message}", fatal=True)  # Codex says when the limit resets
    if any(token in lowered for token in _NETWORK_ERRORS):
        return CodexError(f"Codex couldn't reach OpenAI. Check your internet connection and try again. ({message})")
    return CodexError(f"Codex: {message}")


def _failure_from_events(jsonl: str) -> str:
    """Return the failure message from `codex exec --json` output, ignoring retry notices."""
    message = ""
    for line in jsonl.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn.failed":
            return str((event.get("error") or {}).get("message", ""))
        if event.get("type") == "error" and not str(event.get("message", "")).startswith("Reconnecting"):
            message = str(event.get("message", ""))
    return message


def _credential_store_override() -> list[str]:
    """Carry over `cli_auth_credentials_store`, which `--ignore-user-config` would drop.

    Without it, users who keep their Codex sign-in in the OS keyring would look
    signed in to `codex login status` but signed out to `codex exec`.
    """
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        text = (home / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return []
    top_level = re.split(r"^\s*\[", text, maxsplit=1, flags=re.MULTILINE)[0]
    match = re.search(r"""^\s*cli_auth_credentials_store\s*=\s*["'](\w+)["']""", top_level, re.MULTILINE)
    return ["-c", f'cli_auth_credentials_store="{match.group(1)}"'] if match else []


@lru_cache(maxsize=8)
def _features(binary: str, mtime: float) -> frozenset[str]:
    """Feature names this Codex build knows (cached per binary version via its mtime)."""
    try:
        listing = _run([binary, "features", "list"], timeout=20)
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    if listing.returncode != 0:
        return frozenset()
    return frozenset(line.split()[0] for line in listing.stdout.splitlines() if line.strip())


def _run(cmd: list[str], stdin: str = "", timeout: float = 20, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
        env=_codex_env(),
        check=False,
    )


def _codex_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key not in API_KEY_ENV}


def _parse_version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(part) for part in match.groups()) if match else None


def _first_meaningful_line(text: str) -> str:
    lines = (line.strip() for line in text.splitlines())
    return next((line for line in lines if line and not _STDERR_NOISE.match(line)), "")
