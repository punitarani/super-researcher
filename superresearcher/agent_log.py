"""Per-run log of agent prompts and replies, kept in the run folder on this computer.

logs/agent-log.jsonl holds one small line per call (step, outcome, timing) so the timeline stays
cheap to read; logs/agent-calls/NNNN-prompt.txt and NNNN-reply.txt hold that call's text, so each
can be read a step at a time. Credentials are redacted before anything is written. Logging is on
unless app settings say "agent_log": false.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .config import atomic_write_text, load_app_settings

LOG_INDEX = Path("logs") / "agent-log.jsonl"
CALLS_DIR = Path("logs") / "agent-calls"
MAX_BODY_CHARS = 400_000  # per prompt or reply; longer ones are clipped

_lock = threading.Lock()
_next_call: dict[Path, int] = {}  # per calls folder, so numbering doesn't rescan it on every call

_TOKENS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),  # OpenAI-style keys
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),  # Google API keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),  # GitHub tokens
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key IDs
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWTs
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{16,}"),
]
# name = value, name: value, and quoted keys as in JSON or dicts ("password": "...").
_PAIR = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password|passwd)([\"']?\s*[:=]\s*)([\"']?)([^\s\"',;]{8,})")


def enabled() -> bool:
    return load_app_settings().get("agent_log", True) is not False


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove the given secret values and anything shaped like a credential."""
    for value in sorted({s for s in secrets if s and len(s) >= 8}, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    for pattern in _TOKENS:
        text = pattern.sub("[redacted]", text)
    return _PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}[redacted]", text)


class AgentLog:
    def __init__(self, run_dir: Path, secrets: Iterable[str] = ()) -> None:
        self.run_dir = Path(run_dir)
        self.secrets = [s for s in secrets if isinstance(s, str)]

    def record(
        self,
        *,
        step: str,
        agent: str,
        model: str,
        prompt: str,
        reply: str | None,
        outcome: str,
        reason: str | None,
        sent: bool,
        started_at: datetime,
        duration_ms: int,
    ) -> None:
        """Append one call. Never raises: a log that can't be written must not break the run."""
        if not enabled():
            return
        try:
            with _lock:
                calls = self.run_dir / CALLS_DIR
                calls.mkdir(parents=True, exist_ok=True)
                calls = calls.resolve()  # the run and its jobs may name the same folder differently
                number = _next_call.get(calls) or sum(1 for _ in calls.glob("*-prompt.txt")) + 1
                _next_call[calls] = number + 1
                atomic_write_text(calls / f"{number:04d}-prompt.txt", self._clean(prompt))
                atomic_write_text(calls / f"{number:04d}-reply.txt", self._clean(reply or ""))
                entry = {
                    "call": number,
                    "time": started_at.isoformat(timespec="seconds"),
                    "step": step,
                    "agent": agent,
                    "model": model,
                    "outcome": outcome,
                    "sent": sent,
                    "reason": redact(reason, self.secrets)[:2000] if reason else None,
                    "duration_ms": duration_ms,
                    "prompt_chars": len(prompt),
                    "reply_chars": len(reply or ""),
                }
                with (self.run_dir / LOG_INDEX).open("a", encoding="utf-8") as index:
                    index.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # disk errors, text that can't be encoded: losing a log line beats failing the run
            pass

    def _clean(self, text: str) -> str:
        # Redact before clipping, so a secret cut at the clip point can't survive half-redacted.
        text = redact(text, self.secrets)
        return text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS] + f"\n\n[clipped: {len(text) - MAX_BODY_CHARS} more characters]"
