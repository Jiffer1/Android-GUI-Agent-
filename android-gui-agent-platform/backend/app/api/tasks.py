import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.runtime.engine import get_engine
from app.storage.db import get_db
from app.storage.models import Task, TaskStep

router = APIRouter()


# ------------------------------------------------------------------
# Request / Response schemas
# ------------------------------------------------------------------

class CreateTaskRequest(BaseModel):
    instruction: str
    device_id: Optional[str] = None
    max_steps: int = 20


class TaskStepResponse(BaseModel):
    id: str
    task_id: str
    step_index: int
    action: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None
    status: str
    screenshot_path: Optional[str] = None
    raw_output: Optional[str] = None
    risk_level: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("parameters", mode="before")
    @classmethod
    def parse_parameters(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v


class TaskResponse(BaseModel):
    id: str
    instruction: str
    device_id: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime
    max_steps: int
    current_step: int
    steps: List[TaskStepResponse] = []

    model_config = {"from_attributes": True}


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/api/tasks", response_model=TaskResponse)
def create_task(body: CreateTaskRequest, db: Session = Depends(get_db)):
    task = Task(
        instruction=body.instruction,
        device_id=body.device_id,
        max_steps=body.max_steps,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/api/tasks", response_model=List[TaskResponse])
def list_tasks(db: Session = Depends(get_db)):
    return db.query(Task).order_by(Task.created_at.desc()).all()


@router.get("/api/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/api/tasks/{task_id}/start")
async def start_task(task_id: str, db: Session = Depends(get_db)):
    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.status not in ("pending", "failed", "stopped"):
        raise HTTPException(status_code=400, detail=f"Cannot start task in status: {task.status}")
    engine = get_engine()
    await engine.start_task(task_id)
    return {"status": "started"}


@router.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str):
    engine = get_engine()
    try:
        engine.pause_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "paused"}


@router.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str):
    engine = get_engine()
    try:
        engine.resume_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "resumed"}


@router.post("/api/tasks/{task_id}/stop")
def stop_task(task_id: str):
    engine = get_engine()
    try:
        engine.stop_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "stopped"}


@router.post("/api/tasks/{task_id}/confirm")
def confirm_action(task_id: str):
    engine = get_engine()
    try:
        engine.confirm_action(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "confirmed"}
