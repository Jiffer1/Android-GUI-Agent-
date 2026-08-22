"""Long-term memory management endpoints (AC-06).

``get_store`` is a module-level function so tests can monkeypatch it to
inject an isolated store.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.memory.store import FILE_PATHS, FILE_PREFERENCES, get_store

router = APIRouter()


class DeleteEntryRequest(BaseModel):
    file: str
    index: int


@router.get("/api/memory")
def get_memory():
    store = get_store()
    return {"preferences": store.preferences(), "paths": store.paths()}


@router.delete("/api/memory/entries")
def delete_entry(body: DeleteEntryRequest):
    if body.file not in (FILE_PREFERENCES, FILE_PATHS):
        raise HTTPException(status_code=400, detail=f"Unknown memory file: {body.file}")
    store = get_store()
    if not store.delete(body.file, body.index):
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "deleted"}


@router.delete("/api/memory")
def clear_memory():
    store = get_store()
    store.clear()
    return {"status": "cleared"}
