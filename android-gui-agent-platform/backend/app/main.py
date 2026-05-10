from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.devices import router as devices_router
from app.api.tasks import router as tasks_router
from app.config.settings import settings
from app.storage.db import Base, engine
from app.ws.task_stream import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    Path(settings.ARTIFACTS_DIR).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Android GUI Agent Platform", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tasks_router)
app.include_router(devices_router)
app.include_router(ws_router)


@app.get("/health")
def health():
    return {"status": "ok"}
