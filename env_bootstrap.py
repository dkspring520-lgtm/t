"""Bootstrap local environment variables used by network requests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict


def _load_env_file(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return {}

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            env[key.upper()] = value

    return env


def apply_local_env(path: str | Path | None = None, overwrite: bool = False) -> Dict[str, str]:
    """
    Load proxy variables from ~/.codex/daili.env and inject them into os.environ.

    Returns a dict of variables that were actually applied.
    """
    env_path = Path(path) if path is not None else Path.home() / ".codex" / "daili.env"
    if not env_path.exists():
        return {}

    raw = _load_env_file(env_path)
    if not raw:
        return {}

    applied: Dict[str, str] = {}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        value = raw.get(key)
        if not value:
            continue

        candidates = (key, key.lower())
        for env_key in candidates:
            if overwrite or not os.environ.get(env_key):
                os.environ[env_key] = value
                applied[env_key] = value

    return applied
