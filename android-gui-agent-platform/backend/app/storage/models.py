import uuid
import json
from datetime import datetime
from sqlalchemy import Column, String, Integer, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.storage.db import Base


def _uuid():
    return str(uuid.uuid4())


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=_uuid)
    instruction = Column(String, nullable=False)
    device_id = Column(String, nullable=True)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    max_steps = Column(Integer, default=20)
    current_step = Column(Integer, default=0)

    steps = relationship("TaskStep", back_populates="task", order_by="TaskStep.step_index")


class TaskStep(Base):
    __tablename__ = "task_steps"

    id = Column(String, primary_key=True, default=_uuid)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    step_index = Column(Integer, nullable=False)
    action = Column(String, nullable=True)
    parameters = Column(Text, nullable=True)
    status = Column(String, default="pending")
    screenshot_path = Column(String, nullable=True)
    raw_output = Column(Text, nullable=True)
    risk_level = Column(String, default="safe")
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("Task", back_populates="steps")

    def set_parameters(self, params: dict):
        self.parameters = json.dumps(params, ensure_ascii=False)

    def get_parameters(self) -> dict:
        if self.parameters:
            return json.loads(self.parameters)
        return {}
