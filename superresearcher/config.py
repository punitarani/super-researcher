from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

_storage_env = os.environ.get("SUPERRESEARCHER_STORAGE_ROOT")
DEFAULT_STORAGE_ROOT = (
    Path(_storage_env).expanduser() if _storage_env else ROOT / "research_runs"
)
del _storage_env

ENV_FILE = ROOT / ".env"
_keys_env = os.environ.get("SUPERRESEARCHER_API_KEYS")
# SUPERRESEARCHER_API_KEYS names one keys file to use instead (the e2e tests point it at an empty one).
API_KEYS_FILE = Path(_keys_env).expanduser() if _keys_env else ENV_FILE
# Keys file from before .env; still read so older setups keep working, with .env winning.
LEGACY_KEYS_FILE: Path | None = None if _keys_env else ROOT / "api_keys.txt"
del _keys_env

APP_SETTINGS_FILE = DEFAULT_STORAGE_ROOT / "app-settings.json"

_LEGACY_CODEX_BIN = Path("/Applications/Codex.app/Contents/Resources/codex")


def codex_bin() -> Path | None:
    """Locate the Codex CLI binary.

    Honors the ``CODEX_BIN`` environment variable first, then falls back to
    ``PATH`` lookup, then the legacy macOS .app bundle location.
    Returns ``None`` when no usable binary is found.
    """
    env = os.environ.get("CODEX_BIN")
    if env:
        candidate = Path(env).expanduser()
        if candidate.exists():
            return candidate
    on_path = shutil.which("codex")
    if on_path:
        return Path(on_path)
    if _LEGACY_CODEX_BIN.exists():
        return _LEGACY_CODEX_BIN
    return None

DEPTH_RESULTS = {
    "low": 3,
    "medium": 7,
    "high": 10,
    "extra_high": 15,
    "ludicrous": 50,
}

BREADTH_LEVELS = {
    "low": 1,
    "medium": 3,
    "high": 5,
}

FINAL_SOURCE_DEFAULT = 120
FINAL_SOURCE_MAX = 500


def slugify(value: str, fallback: str = "research") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value[:72] or fallback


def ensure_storage_root(path: str | Path = DEFAULT_STORAGE_ROOT) -> Path:
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.W_OK):
        raise RuntimeError(f"Storage path is not writable: {root}")
    usage = shutil.disk_usage(root)
    if usage.free < 1024 * 1024 * 1024:
        raise RuntimeError(f"Storage path has less than 1 GB free: {root}")
    return root


def load_api_keys(path: Path | None = None) -> dict[str, str]:
    """API keys and settings from .env (or `path`). Values are never put into os.environ."""
    if path is not None:
        return read_keys_file(path)
    legacy = read_keys_file(LEGACY_KEYS_FILE) if LEGACY_KEYS_FILE else {}
    return {**legacy, **read_keys_file(API_KEYS_FILE)}


def read_keys_file(path: Path) -> dict[str, str]:
    """Parse KEY=value lines: comments, `export`, quotes and trailing ` # comments` as in .env files."""
    keys: dict[str, str] = {}
    if not path.exists():
        return keys
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:  # api_keys.txt also allowed KEY: value
            key, value = line.split(":", 1)
        else:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if key and value:
            keys[key] = value
    return keys


def redact_keys(keys: dict[str, str]) -> dict[str, str]:
    return {k: "<configured>" for k, v in keys.items() if v}


def load_app_settings() -> dict[str, Any]:
    try:
        settings = json.loads(APP_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def save_app_settings(settings: dict[str, Any]) -> None:
    APP_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(APP_SETTINGS_FILE, settings)


def atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def atomic_write_text(path: Path, payload: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
