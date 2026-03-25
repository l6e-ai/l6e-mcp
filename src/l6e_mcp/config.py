"""User configuration for cloud sync: API key, endpoint, and opt-in flag.

Priority: env vars > ~/.l6e/config.toml > defaults.
"""
from __future__ import annotations

import logging
import os
import stat
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_ENDPOINT = "https://api.l6e.ai"
_CONFIG_DIR = Path.home() / ".l6e"
_CONFIG_PATH = _CONFIG_DIR / "config.toml"

_TRUTHY = frozenset({"1", "true", "yes"})

_toml_cache: dict[str, Any] | None = None
_toml_cache_time: float = 0.0
_toml_cache_lock = threading.Lock()
_TOML_TTL = 30.0


def _load_toml() -> dict[str, Any]:
    global _toml_cache, _toml_cache_time  # noqa: PLW0603

    now = time.monotonic()
    if _toml_cache is not None and (now - _toml_cache_time) < _TOML_TTL:
        return _toml_cache

    with _toml_cache_lock:
        if _toml_cache is not None and (time.monotonic() - _toml_cache_time) < _TOML_TTL:
            return _toml_cache

        path = Path(os.environ.get("L6E_CONFIG_PATH", str(_CONFIG_PATH)))
        if not path.is_file():
            result: dict[str, Any] = {}
        else:
            try:
                _check_permissions(path)
                with open(path, "rb") as f:
                    result = tomllib.load(f)
            except Exception:
                logger.debug("config_toml_read_failed", exc_info=True)
                result = {}

        _toml_cache = result
        _toml_cache_time = time.monotonic()
        return result


def _reset_toml_cache() -> None:
    """Clear the TOML cache. Used by tests for isolation."""
    global _toml_cache, _toml_cache_time  # noqa: PLW0603
    with _toml_cache_lock:
        _toml_cache = None
        _toml_cache_time = 0.0


def _check_permissions(path: Path) -> None:
    """Warn if the config file is world-readable."""
    try:
        mode = path.stat().st_mode
        if mode & (stat.S_IRGRP | stat.S_IROTH):
            logger.warning(
                "Config file %s is readable by other users. "
                "Run `chmod 600 ~/.l6e/config.toml` to restrict access.",
                path,
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


def get_manual_calibration_factors() -> dict[str, float]:
    """Return manually configured per-model calibration factors.

    Precedence: L6E_CALIBRATION_FACTORS env var > TOML [calibration] section.
    Env var format: "model1:factor1,model2:factor2"
    """
    env = os.environ.get("L6E_CALIBRATION_FACTORS", "").strip()
    if env:
        factors: dict[str, float] = {}
        for entry in env.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.rsplit(":", 1)
            if len(parts) != 2:
                logger.warning("calibration_factors_malformed_entry", extra={"entry": entry})
                continue
            model, value = parts[0].strip(), parts[1].strip()
            try:
                factors[model] = float(value)
            except ValueError:
                logger.warning(
                    "calibration_factors_invalid_value",
                    extra={"model": model, "value": value},
                )
        return factors

    toml_section = _load_toml().get("calibration")
    if isinstance(toml_section, dict):
        factors = {}
        for model, value in toml_section.items():
            try:
                factors[model] = float(value)
            except (ValueError, TypeError):
                logger.warning(
                    "calibration_factors_invalid_toml_value",
                    extra={"model": model, "value": value},
                )
        return factors

    return {}


_CONFIG_TEMPLATE = """\
# l6e configuration
# Env vars take precedence over values in this file.

# api_key = "sk-l6e-..."
# cloud_endpoint = "https://api.l6e.ai"
# cloud_sync = false
# send_task_summaries = true

# Per-model calibration factors (manual override).
# These are used when cloud sync is off or as a fallback.
# Server-side factors from billing import always take precedence.
# [calibration]
# claude-4-opus = 72.0
# claude-4-sonnet = 45.0
# claude-3.5-haiku = 12.0
"""


def ensure_config_template() -> None:
    """Create ~/.l6e/config.toml with commented-out defaults if it doesn't exist."""
    if _CONFIG_PATH.is_file():
        return
    if not _CONFIG_DIR.is_dir():
        return
    try:
        _CONFIG_PATH.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        _CONFIG_PATH.chmod(0o600)
        logger.debug("config_template_created", extra={"path": str(_CONFIG_PATH)})
    except OSError:
        logger.debug("config_template_create_failed", exc_info=True)
