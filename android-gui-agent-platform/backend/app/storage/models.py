import uuid
import json
from datetime import datetime
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.storage.db import Base


def _uuid():
    return str(uuid.uuid4())


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(String, primary_key=True, default=_uuid)
    title = Column(String, nullable=True)
    device_id = Column(String, nullable=True)
    status = Column(String, default="idle")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    turns = relationship("Turn", back_populates="conversation", order_by="Turn.created_at")
    messages = relationship("Message", back_populates="conversation", order_by="Message.created_at")


class Turn(Base):
    __tablename__ = "turns"

    id = Column(String, primary_key=True, default=_uuid)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    user_message_id = Column(String, nullable=True)
    status = Column(String, default="pending")
    max_steps = Column(Integer, default=20)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    conversation = relationship("Conversation", back_populates="turns")
    steps = relationship("TurnStep", back_populates="turn", order_by="TurnStep.step_index")


class Message(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True, default=_uuid)
    conversation_id = Column(String, ForeignKey("conversations.id"), nullable=False)
    turn_id = Column(String, ForeignKey("turns.id"), nullable=True)
    role = Column(String, nullable=False)
    kind = Column(String, default="text")
    content = Column(Text, nullable=False, default="")
    extra = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    conversation = relationship("Conversation", back_populates="messages")

    def set_extra(self, data: dict):
        self.extra = json.dumps(data, ensure_ascii=False)

    def get_extra(self) -> dict:
        if self.extra:
            return json.loads(self.extra)
        return {}


class TurnStep(Base):
    __tablename__ = "turn_steps"

    id = Column(String, primary_key=True, default=_uuid)
    turn_id = Column(String, ForeignKey("turns.id"), nullable=False)
    step_index = Column(Integer, nullable=False)
    action = Column(String, nullable=True)
    parameters = Column(Text, nullable=True)
    status = Column(String, default="completed")
    screenshot_path = Column(String, nullable=True)
    raw_output = Column(Text, nullable=True)
    risk_level = Column(String, default="safe")
    created_at = Column(DateTime, default=datetime.utcnow)

    turn = relationship("Turn", back_populates="steps")

    def set_parameters(self, params: dict):
        self.parameters = json.dumps(params, ensure_ascii=False)

    def get_parameters(self) -> dict:
        if self.parameters:
            return json.loads(self.parameters)
        return {}
