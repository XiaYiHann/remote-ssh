"""Utility helpers for parsing, env loading, and response unwrapping."""

from __future__ import annotations

import os
import shlex
from typing import Any, Dict, List, Optional

from remote_ssh_cli.config import JsonDict, SzuAutomationError


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_lower(value: Any) -> str:
    return _norm(value).lower()


def load_bashrc_exports(path: Optional[str] = None) -> Dict[str, str]:
    """Parse export lines from ~/.bashrc as a fallback credential source."""
    path = path or os.path.expanduser("~/.bashrc")
    values: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return values

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("export "):
            continue
        try:
            assignments = shlex.split(stripped[len("export ") :])
        except ValueError:
            continue
        for assignment in assignments:
            if "=" not in assignment:
                continue
            key, value = assignment.split("=", 1)
            values[key] = value
    return values


def env_default(name: str) -> Optional[str]:
    """Read env var, falling back to ~/.bashrc exports."""
    value = os.getenv(name)
    if value:
        return value
    return load_bashrc_exports().get(name)


def default_key_path() -> str:
    """Return a sensible default SSH private key path."""
    configured = env_default("SZU_AICLOUD_KEY_PATH")
    if configured:
        return configured
    if os.path.exists(os.path.expanduser("~/.ssh/id_rsa")):
        return "~/.ssh/id_rsa"
    return "id_rsa"


def _ensure_list(value: Any, label: str) -> List[JsonDict]:
    """Coerce an API response to a list of dicts."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("list", "records", "rows", "items", "data"):
            nested = value.get(key)
            if isinstance(nested, list):
                return [item for item in nested if isinstance(item, dict)]
    raise SzuAutomationError(
        f"{label}: expected list-like response, got {type(value).__name__}"
    )


def unwrap_response(payload: Any) -> Any:
    """Match the web app's axios wrapper, but tolerate raw API responses."""
    current = payload
    seen = 0
    while isinstance(current, dict) and "data" in current and seen < 4:
        code = current.get("code")
        if code not in (None, 0, "0", 200, "200"):
            message = current.get("message") or current.get("msg") or current
            raise SzuAutomationError(f"API returned code={code}: {message}")
        current = current["data"]
        seen += 1
    return current


def image_label(image: Any) -> str:
    """Build a human-readable label from an image record."""
    name = _norm(
        image.get("name") or image.get("imageName") or image.get("repository")
    )
    tag = _norm(image.get("tag") or image.get("imageTag") or image.get("version"))
    if name and tag:
        return f"{name}:{tag}"
    return _norm(image.get("label") or image.get("path") or image.get("url") or name)


def _id(record: Any, label: str) -> Any:
    """Extract a required id field from a record."""
    value = record.get("id")
    if value is None or value == "":
        raise SzuAutomationError(f"{label} has no id: {record}")
    return value
