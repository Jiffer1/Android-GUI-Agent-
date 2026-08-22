import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.runtime.engine import get_engine
from app.storage.db import get_db
from app.storage.models import Conversation, Message, Turn, TurnStep

router = APIRouter()


# ------------------------------------------------------------------
# Request / Response schemas
# ------------------------------------------------------------------

class CreateConversationRequest(BaseModel):
    title: Optional[str] = None
    device_id: Optional[str] = None


class SendMessageRequest(BaseModel):
    text: str


class ReplyRequest(BaseModel):
    text: str


class ConfirmRequest(BaseModel):
    approved: bool


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    turn_id: Optional[str] = None
    role: str
    kind: str
    content: str
    extra: Optional[Dict[str, Any]] = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("extra", mode="before")
    @classmethod
    def parse_extra(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return v


class TurnStepResponse(BaseModel):
    id: str
    turn_id: str
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

    @field_validator("screenshot_path", mode="before")
    @classmethod
    def to_artifact_url(cls, v):
        # stored value is a path relative to ARTIFACTS_DIR (posix style)
        if not v:
            return None
        return v if v.startswith("/artifacts/") else f"/artifacts/{v}"


class TurnResponse(BaseModel):
    id: str
    conversation_id: str
    user_message_id: Optional[str] = None
    status: str
    max_steps: int
    error: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
    steps: List[TurnStepResponse] = []

    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    id: str
    title: Optional[str] = None
    device_id: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime
    last_message: Optional[str] = None

    model_config = {"from_attributes": True}


class ConversationDetailResponse(ConversationResponse):
    messages: List[MessageResponse] = []
    turns: List[TurnResponse] = []


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_conversation_or_404(db: Session, conversation_id: str) -> Conversation:
    conversation = db.query(Conversation).filter_by(id=conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _busy_code(error: str) -> str:
    text = str(error)
    if "Device" in text and "busy" in text:
        return "device_busy"
    if "active turn" in text:
        return "conversation_busy"
    return "busy"


# ------------------------------------------------------------------
# CRUD
# ------------------------------------------------------------------

@router.post("/api/conversations", response_model=ConversationResponse)
def create_conversation(body: CreateConversationRequest, db: Session = Depends(get_db)):
    title = (body.title or "").strip() or "新会话"
    conversation = Conversation(title=title, device_id=body.device_id, status="idle")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.get("/api/conversations", response_model=List[ConversationResponse])
def list_conversations(db: Session = Depends(get_db)):
    conversations = (db.query(Conversation)
                     .order_by(Conversation.updated_at.desc())
                     .all())
    result = []
    for c in conversations:
        item = ConversationResponse.model_validate(c)
        last = (db.query(Message)
                .filter_by(conversation_id=c.id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .first())
        item.last_message = last.content if last else None
        result.append(item)
    return result


@router.get("/api/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_conversation(conversation_id: str, db: Session = Depends(get_db)):
    conversation = _get_conversation_or_404(db, conversation_id)
    detail = ConversationDetailResponse.model_validate(conversation)
    detail.messages = [
        MessageResponse.model_validate(m)
        for m in (db.query(Message)
                  .filter_by(conversation_id=conversation_id)
                  .order_by(Message.created_at, Message.id)
                  .all())
    ]
    turns = (db.query(Turn)
             .filter_by(conversation_id=conversation_id)
             .order_by(Turn.created_at)
             .all())
    for t in turns:
        turn_resp = TurnResponse.model_validate(t)
        turn_resp.steps = [
            TurnStepResponse.model_validate(s)
            for s in (db.query(TurnStep)
                      .filter_by(turn_id=t.id)
                      .order_by(TurnStep.step_index)
                      .all())
        ]
        detail.turns.append(turn_resp)
    return detail


@router.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, db: Session = Depends(get_db)):
    _get_conversation_or_404(db, conversation_id)

    # Stop the running coroutine BEFORE deleting rows, so its finalizer
    # cannot write to rows we are about to remove.
    engine = get_engine()
    await engine.stop_and_wait(conversation_id)

    db.query(TurnStep).filter(
        TurnStep.turn_id.in_(db.query(Turn.id).filter_by(conversation_id=conversation_id))
    ).delete(synchronize_session=False)
    db.query(Turn).filter_by(conversation_id=conversation_id).delete(synchronize_session=False)
    db.query(Message).filter_by(conversation_id=conversation_id).delete(synchronize_session=False)
    db.query(Conversation).filter_by(id=conversation_id).delete(synchronize_session=False)
    db.commit()
    return {"status": "deleted"}


# ------------------------------------------------------------------
# Turn control
# ------------------------------------------------------------------

@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, body: SendMessageRequest,
                       db: Session = Depends(get_db)):
    _get_conversation_or_404(db, conversation_id)
    engine = get_engine()
    try:
        turn = await engine.start_turn(conversation_id, body.text)
    except ValueError as e:
        raise HTTPException(status_code=400,
                            detail={"code": _busy_code(e), "message": str(e)})
    return {"turn_id": turn.id, "status": turn.status}


@router.post("/api/conversations/{conversation_id}/reply")
async def reply_ask(conversation_id: str, body: ReplyRequest,
                    db: Session = Depends(get_db)):
    _get_conversation_or_404(db, conversation_id)
    engine = get_engine()
    try:
        await engine.reply_ask(conversation_id, body.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "replied"}


@router.post("/api/conversations/{conversation_id}/confirm")
def confirm_risk(conversation_id: str, body: ConfirmRequest,
                 db: Session = Depends(get_db)):
    _get_conversation_or_404(db, conversation_id)
    engine = get_engine()
    try:
        engine.confirm(conversation_id, body.approved)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "confirmed" if body.approved else "cancelled"}


@router.post("/api/conversations/{conversation_id}/stop")
def stop_turn(conversation_id: str, db: Session = Depends(get_db)):
    _get_conversation_or_404(db, conversation_id)
    engine = get_engine()
    try:
        engine.stop_turn(conversation_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "stopped"}
