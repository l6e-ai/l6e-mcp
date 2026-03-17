"""User configuration for cloud sync: API key, endpoint, and opt-in flag.

Priority: env vars > ~/.l6e/config.toml > defaults.
"""
from __future__ import annotations

import logging
import os
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "https://api.l6e.ai"
_CONFIG_DIR = Path.home() / ".l6e"
_CONFIG_PATH = _CONFIG_DIR / "config.toml"

_TRUTHY = frozenset({"1", "true", "yes"})


def _load_toml() -> dict[str, Any]:
    path = Path(os.environ.get("L6E_CONFIG_PATH", str(_CONFIG_PATH)))
    if not path.is_file():
        return {}
    try:
        _check_permissions(path)
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        logger.debug("config_toml_read_failed", exc_info=True)
        return {}


def _check_permissions(path: Path) -> None:
    """Warn once if the config file is world-readable."""
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            print(
                f"l6e: warning: {path} is readable by other users. "
                "Run `chmod 600 ~/.l6e/config.toml` to restrict access.",
                file=sys.stderr,
            )
    except OSError:
        pass


def get_api_key() -> str | None:
    env = os.environ.get("L6E_API_KEY")
    if env:
        return env.strip()
    return _load_toml().get("api_key") or None


def get_cloud_endpoint() -> str:
    env = os.environ.get("L6E_CLOUD_ENDPOINT")
    if env:
        return env.strip().rstrip("/")
    toml_val = _load_toml().get("cloud_endpoint")
    if toml_val:
        return str(toml_val).strip().rstrip("/")
    return _DEFAULT_ENDPOINT


def is_cloud_sync_enabled() -> bool:
    env = os.environ.get("L6E_CLOUD_SYNC", "").strip().lower()
    if env:
        return env in _TRUTHY
    return bool(_load_toml().get("cloud_sync", False))


def send_task_summaries() -> bool:
    """Whether to include task summaries in cloud-synced session reports.

    Summaries are always stored locally regardless of this setting.
    """
    env = os.environ.get("L6E_SEND_TASK_SUMMARIES", "").strip().lower()
    if env:
        return env in _TRUTHY
    return bool(_load_toml().get("send_task_summaries", True))
