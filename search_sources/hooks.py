"""Hook callbacks: collect the pages a turn touched, then hand Desktop a citation record with the reply.

Why `transform_llm_output` and not the tool hooks alone: only there is the final reply text known (needed to
tell cited pages from merely retrieved ones) and only there can the reply itself be extended. The tool hooks
fire per call; the turn id they share with the transform is the join key.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import store
from .extract import extract
from .record import append_directive, build_record, strip_directives

logger = logging.getLogger(__name__)

# `agent.platform` of a session opened by the Desktop app (`source: 'desktop'` on session.create). It is the
# one value that proves the reply is going to a transcript that can draw the directive; Telegram and every
# other chat surface carry their own platform name and are therefore never touched.
DESKTOP_PLATFORMS = frozenset({"desktop"})
BUCKET_TTL = 3600.0
MAX_BUCKETS = 256
MAX_RAW_PER_TURN = 400


def _turn_key(turn_id: str, session_id: str) -> str:
    return turn_id or f"session:{session_id}"


class Collector:
    """Raw pages per turn. A subagent's pages belong to the turn that spawned it, so children are mapped to
    their parent's turn key for as long as they run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, list[dict]]] = {}
        self._children: dict[str, str] = {}

    def key_for(self, turn_id: str, session_id: str) -> str:
        with self._lock:
            return self._children.get(session_id) or _turn_key(turn_id, session_id)

    def is_child(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._children

    def add(self, key: str, sources: list[dict]) -> None:
        now = time.monotonic()
        with self._lock:
            self._evict(now)
            _, bucket = self._buckets.setdefault(key, (now, []))
            bucket.extend(sources[: max(0, MAX_RAW_PER_TURN - len(bucket))])

    def pop(self, key: str) -> list[dict]:
        with self._lock:
            return self._buckets.pop(key, (0.0, []))[1]

    def link_child(self, child_session_id: str, parent_key: str) -> None:
        with self._lock:
            self._children[child_session_id] = parent_key

    def unlink_child(self, child_session_id: str) -> None:
        with self._lock:
            self._children.pop(child_session_id, None)

    def _evict(self, now: float) -> None:
        stale = [k for k, (born, _) in self._buckets.items() if now - born > BUCKET_TTL]
        for key in stale:
            del self._buckets[key]
        overflow = len(self._buckets) - MAX_BUCKETS + 1
        for key in sorted(self._buckets, key=lambda k: self._buckets[k][0])[: max(0, overflow)]:
            del self._buckets[key]


class Hooks:
    def __init__(self, collector: Collector | None = None) -> None:
        self.collector = collector or Collector()

    # post_tool_call ----------------------------------------------------------------------------------
    def on_post_tool_call(self, tool_name: str = "", args: Any = None, result: Any = None, session_id: str = "",
                          turn_id: str = "", status: str | None = None, **_: Any) -> None:
        if status not in (None, "", "ok"):
            return
        try:
            sources = extract(tool_name, args, result)
        except Exception:
            logger.debug("search-sources could not read %s output", tool_name, exc_info=True)
            return
        if sources:
            self.collector.add(self.collector.key_for(turn_id or "", session_id or ""), sources)

    # subagent_start / subagent_stop ------------------------------------------------------------------
    def on_subagent_start(self, parent_session_id: str = "", parent_turn_id: str = "", child_session_id: str = "",
                          **_: Any) -> None:
        if child_session_id:
            parent_key = self.collector.key_for(parent_turn_id or "", parent_session_id or "")
            self.collector.link_child(child_session_id, parent_key)

    def on_subagent_stop(self, child_session_id: str = "", **_: Any) -> None:
        if child_session_id:
            self.collector.unlink_child(child_session_id)

    # transform_llm_output ----------------------------------------------------------------------------
    def on_transform_llm_output(self, response_text: str = "", session_id: str = "", platform: str = "",
                                turn_id: str = "", **_: Any) -> str | None:
        """The reply plus a `::search-sources` paragraph, or None to leave it alone."""
        if self.collector.is_child(session_id or ""):
            return None  # a subagent's summary is tool output for its parent, not a reply
        sources = self.collector.pop(_turn_key(turn_id or "", session_id or ""))
        if not isinstance(response_text, str):
            return None
        # A directive the model copied from its own history is never trusted: its id would point at another
        # turn's pages, and on any surface but Desktop it would show up as raw text.
        reply = strip_directives(response_text)
        record = self._record_for(reply, sources, session_id or "", platform, turn_id or "")
        if record is None:
            return reply if reply != response_text else None
        _broadcast(record)
        return append_directive(reply, record)

    @staticmethod
    def _record_for(reply: str, sources: list[dict], session_id: str, platform: str, turn_id: str) -> dict | None:
        if not sources or not reply.strip():
            return None
        if str(platform or "").strip().lower() not in DESKTOP_PLATFORMS or not store.desktop_alive():
            return None
        record = build_record(session_id, turn_id, sources, reply)
        if record is None:
            return None
        try:
            store.save_record(record)
        except OSError:
            logger.warning("search-sources could not store record %s", record["id"], exc_info=True)
            return None
        return record


def _broadcast(record: dict) -> None:
    """Push the full record to every connected Desktop window; the page list then never has to ride in the
    reply text. Best effort — Desktop falls back to `GET /record/<id>`."""
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event
        broadcast_plugin_event(store.PLUGIN_ID, "turn.recorded", {"record": record})
    except Exception:
        logger.debug("search-sources event broadcast failed", exc_info=True)
