"""Disk side of the plugin: turn records and the Desktop "I am listening" heartbeat.

The agent hooks and `dashboard/plugin_api.py` are imported as separate modules (and, with plugin isolation,
can live in separate processes), so everything they share goes through files under
`<hermes home>/plugin-data/search-sources/`. Nothing here keeps module-level state.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from .record import is_record_id

logger = logging.getLogger(__name__)

PLUGIN_ID = "search-sources"
# Desktop pings on load, on every turn start and every few minutes; two missed intervals means it is gone.
HEARTBEAT_TTL = 900.0
KEEP_RECORDS = 200
PRUNE_ABOVE = 300


def data_dir() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home()) / "plugin-data" / PLUGIN_ID


def _records_dir() -> Path:
    return data_dir() / "records"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _prune() -> None:
    entries = sorted(
        (e for e in os.scandir(_records_dir()) if e.name.endswith(".json")), key=lambda e: e.stat().st_mtime
    )
    if len(entries) <= PRUNE_ABOVE:
        return
    for entry in entries[: len(entries) - KEEP_RECORDS]:
        Path(entry.path).unlink(missing_ok=True)


def save_record(record: dict) -> None:
    _write_json(_records_dir() / f"{record['id']}.json", record)
    try:
        _prune()
    except OSError:
        logger.debug("search-sources record pruning failed", exc_info=True)


def load_record(record_id: str) -> dict | None:
    if not is_record_id(record_id):
        return None
    value = _read_json(_records_dir() / f"{record_id}.json")
    return value if isinstance(value, dict) else None


def recent_records(limit: int = 30) -> list[dict]:
    """Summaries (no page lists) of the newest records."""
    try:
        entries = sorted(
            (e for e in os.scandir(_records_dir()) if e.name.endswith(".json")),
            key=lambda e: e.stat().st_mtime, reverse=True,
        )
    except OSError:
        return []
    out: list[dict] = []
    for entry in entries[: max(1, min(limit, 100))]:
        record = _read_json(Path(entry.path))
        if isinstance(record, dict) and is_record_id(record.get("id")):
            out.append({k: record.get(k) for k in ("id", "label", "total", "cited", "created", "session_id")})
    return out


# --- Desktop heartbeat -------------------------------------------------------------------------------


def _heartbeat_path() -> Path:
    return data_dir() / "desktop-seen.json"


def mark_desktop_seen(version: str = "", instance: str = "") -> None:
    _write_json(_heartbeat_path(), {"ts": time.time(), "version": str(version)[:32], "instance": str(instance)[:64]})


def mark_desktop_gone(instance: str = "") -> None:
    """Retire the heartbeat. A client that names itself only retires its own: a plugin hot-reload disposes the
    old instance after (or before) the new one has pinged, and must not erase the newcomer's check-in."""
    value = _read_json(_heartbeat_path())
    stored = value.get("instance") if isinstance(value, dict) else ""
    if instance and stored and stored != str(instance)[:64]:
        return
    _heartbeat_path().unlink(missing_ok=True)


def desktop_alive(now: float | None = None) -> bool:
    """Whether a Desktop client that can draw `::search-sources` has checked in recently. Without it the
    directive would reach the transcript as raw text, so the hook stays silent."""
    value = _read_json(_heartbeat_path())
    ts = value.get("ts") if isinstance(value, dict) else None
    if not isinstance(ts, (int, float)):
        return False
    return 0 <= (now if now is not None else time.time()) - ts <= HEARTBEAT_TTL
