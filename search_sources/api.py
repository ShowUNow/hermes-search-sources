"""FastAPI routes: record lookup for Desktop and the heartbeat that licenses the directive."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import store
from .record import is_record_id

router = APIRouter()


@router.get("/health")
async def health():
    return {"ok": True, "desktop_alive": store.desktop_alive()}


@router.get("/record/{record_id}")
async def record(record_id: str):
    if not is_record_id(record_id):
        raise HTTPException(status_code=404, detail="Unknown record")
    found = store.load_record(record_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Unknown record")
    return found


@router.get("/recent")
async def recent(limit: int = 30):
    return {"records": store.recent_records(limit)}


@router.post("/ping")
async def ping(body: dict | None = None):
    body = body or {}
    store.mark_desktop_seen(body.get("version", ""), body.get("instance", ""))
    return {"ok": True}


@router.post("/bye")
async def bye(body: dict | None = None):
    store.mark_desktop_gone((body or {}).get("instance", ""))
    return {"ok": True}
