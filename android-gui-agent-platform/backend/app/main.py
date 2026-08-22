from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.conversations import router as conversations_router
from app.api.devices import router as devices_router
from app.api.memory import router as memory_router
from app.config.settings import settings
from app.storage.db import SessionLocal, engine, init_db
from app.storage.models import Conversation, Turn
from app.ws.conversation_stream import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path("data").mkdir(exist_ok=True)
    init_db()
    Path(settings.ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
    _recover_interrupted_turns()
    yield


def _recover_interrupted_turns() -> None:
    """Turns left running/waiting by a previous process cannot resume
    (their in-memory sessions are gone) — mark them stopped."""
    db = SessionLocal()
    try:
        stale = (db.query(Turn)
                 .filter(Turn.status.in_(["pending", "running",
                                          "waiting_ask", "waiting_confirm"]))
                 .all())
        for turn in stale:
            turn.status = "stopped"
            turn.finished_at = turn.finished_at or datetime.utcnow()
        if stale:
            db.query(Conversation).filter(
                Conversation.id.in_([t.conversation_id for t in stale])
            ).update({"status": "idle"}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


app = FastAPI(title="Android GUI Agent Platform", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/artifacts",
          StaticFiles(directory=settings.ARTIFACTS_DIR, check_dir=False),
          name="artifacts")

app.include_router(conversations_router)
app.include_router(devices_router)
app.include_router(memory_router)
app.include_router(ws_router)


@app.get("/health")
def health():
    return {"status": "ok"}
