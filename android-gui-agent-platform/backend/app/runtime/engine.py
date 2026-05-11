import asyncio
import base64
import io
import json
import logging
import os
from collections import deque
from typing import Deque, Dict, Optional

from PIL import Image

from app.agent import escalation as escalation_policy
from app.agent.gui_agent import MockGuiAgent
from app.agent.react_agent import ReactGuiAgent
from app.agent.router import build_agent
from app.agent.schemas import (
    AgentInput, AgentOutput, ACTION_COMPLETE,
    ROUTE_REACT, ROUTE_STANDARD,
)
from app.device.base import AdbNotFoundError, DeviceError
from app.device.registry import get_controller
from app.runtime.events import (
    WSEvent,
    TASK_STARTED, STEP_STARTED, STEP_COMPLETED,
    TASK_PAUSED, TASK_RESUMED, TASK_FINISHED, TASK_FAILED, TASK_STOPPED, RISK_DETECTED,
    TASK_ROUTED, ESCALATION_TRIGGERED,
)
from app.runtime.session import TaskSession
from app.safety.policy import assess_output
from app.storage.artifact_store import save_screenshot
from app.storage.db import SessionLocal
from app.storage.models import Task, TaskStep
from app.ws.connection_manager import manager

logger = logging.getLogger(__name__)

USE_MOCK_AGENT = os.environ.get("USE_MOCK_AGENT", "").lower() in {"1", "true", "yes"}


def _image_to_base64(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _images_are_similar(img1: Image.Image, img2: Image.Image, threshold: float = 0.02) -> bool:
    """Return True when two screenshots differ by less than threshold (screen has stabilized)."""
    size = (100, 100)
    a = list(img1.resize(size).convert("L").getdata())
    b = list(img2.resize(size).convert("L").getdata())
    diff = sum(abs(p - q) for p, q in zip(a, b))
    return diff / (255 * len(a)) < threshold


def _make_placeholder_image() -> Image.Image:
    img = Image.new("RGB", (400, 200), color=(30, 30, 30))
    return img


class RuntimeEngine:
    def __init__(self):
        self._sessions: Dict[str, TaskSession] = {}
        self._tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    async def start_task(self, task_id: str) -> None:
        if task_id in self._sessions:
            raise ValueError(f"Task {task_id} is already running")
        session = TaskSession(task_id)
        self._sessions[task_id] = session
        loop = asyncio.get_event_loop()
        t = loop.create_task(self._run_task(task_id, session))
        self._tasks[task_id] = t

    def pause_task(self, task_id: str) -> None:
        session = self._get_session(task_id)
        session.pause_event.clear()
        session.status = "paused"

    def resume_task(self, task_id: str) -> None:
        session = self._get_session(task_id)
        session.pause_event.set()
        session.status = "running"

    def stop_task(self, task_id: str) -> None:
        session = self._get_session(task_id)
        session.stop_requested = True
        session.pause_event.set()
        session.confirm_event.set()

    def confirm_action(self, task_id: str) -> None:
        session = self._get_session(task_id)
        session.confirm_approved = True
        session.confirm_event.set()

    def get_session(self, task_id: str) -> Optional[TaskSession]:
        return self._sessions.get(task_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _wait_for_stable_screen(self, controller, loop, action: str) -> Image.Image:
        """Take screenshots until two consecutive frames are similar (page has settled)."""
        min_waits = {"OPEN": 2.5, "CLICK": 0.6, "SCROLL": 0.4, "TYPE": 0.15,
                     "BACK": 0.6, "HOME": 0.8}
        await asyncio.sleep(min_waits.get(action, 0.6))

        max_extra = 4.0
        poll = 0.35
        elapsed = 0.0
        prev = await loop.run_in_executor(None, controller.screenshot)
        while elapsed < max_extra:
            await asyncio.sleep(poll)
            elapsed += poll
            curr = await loop.run_in_executor(None, controller.screenshot)
            if _images_are_similar(prev, curr):
                return curr
            prev = curr
        return prev

    def _get_session(self, task_id: str) -> TaskSession:
        session = self._sessions.get(task_id)
        if session is None:
            raise ValueError(f"No active session for task {task_id}")
        return session

    async def _broadcast(self, task_id: str, event: WSEvent) -> None:
        await manager.broadcast(task_id, event.model_dump())

    def _execute_action(self, controller, output: AgentOutput) -> None:
        action = output.action
        params = output.parameters
        if action == "CLICK":
            controller.click(params.get("point", [500, 500]))
        elif action == "SCROLL":
            controller.scroll(params.get("start_point", [500, 500]), params.get("end_point", [500, 300]))
        elif action == "TYPE":
            controller.type_text(params.get("text", ""))
        elif action == "OPEN":
            controller.open_app(params.get("app_name", ""))
        elif action == "BACK":
            controller.back()
        elif action == "HOME":
            controller.home()
        elif action == "COMPLETE":
            pass

    def _save_step(self, db, task_id: str, step_index: int, output: AgentOutput,
                   screenshot_path: str, risk_level: str) -> None:
        step = TaskStep(
            task_id=task_id,
            step_index=step_index,
            action=output.action,
            parameters=json.dumps(output.parameters, ensure_ascii=False),
            status="completed",
            screenshot_path=screenshot_path,
            raw_output=output.raw_output,
            risk_level=risk_level,
        )
        db.add(step)
        task = db.query(Task).filter_by(id=task_id).first()
        if task:
            task.current_step = step_index + 1
        db.commit()

    def _update_task_status(self, db, task_id: str, status: str) -> None:
        task = db.query(Task).filter_by(id=task_id).first()
        if task:
            task.status = status
            db.commit()

    # ------------------------------------------------------------------
    # Main task loop
    # ------------------------------------------------------------------

    async def _run_task(self, task_id: str, session: TaskSession) -> None:
        loop = asyncio.get_event_loop()
        db = SessionLocal()
        try:
            task = await loop.run_in_executor(None, lambda: db.query(Task).filter_by(id=task_id).first())
            if task is None:
                logger.error("Task %s not found", task_id)
                return

            await loop.run_in_executor(None, self._update_task_status, db, task_id, "running")
            session.status = "running"
            await self._broadcast(task_id, WSEvent(event=TASK_STARTED, task_id=task_id,
                                                    data={"instruction": task.instruction}))

            agent, route_decision = build_agent(task.instruction, mock=USE_MOCK_AGENT)
            agent.reset()
            session.route = route_decision.route
            await self._broadcast(task_id, WSEvent(event=TASK_ROUTED, task_id=task_id, data={
                "route": route_decision.route,
                "reason": route_decision.reason,
            }))

            has_device = bool(task.device_id)
            controller = get_controller(task.device_id) if has_device else None
            history_actions = []
            stable_image: Optional[Image.Image] = None  # pre-fetched stable screenshot
            recent_pre_action_images: Deque[Image.Image] = deque(maxlen=escalation_policy.SCREEN_STUCK_WINDOW)
            last_subgoal_index: Optional[int] = None

            for step_index in range(task.max_steps):
                if session.stop_requested:
                    break

                await session.pause_event.wait()
                if session.stop_requested:
                    break

                await self._broadcast(task_id, WSEvent(event=STEP_STARTED, task_id=task_id,
                                                        data={"step_index": step_index}))

                # Screenshot: reuse stable frame from previous step when available
                if stable_image is not None:
                    image = stable_image
                    stable_image = None
                elif controller:
                    try:
                        image = await loop.run_in_executor(None, controller.screenshot)
                    except (AdbNotFoundError, DeviceError) as e:
                        await self._broadcast(task_id, WSEvent(event=TASK_FAILED, task_id=task_id,
                                                                data={"error": str(e)}))
                        await loop.run_in_executor(None, self._update_task_status, db, task_id, "failed")
                        return
                else:
                    image = _make_placeholder_image()

                # Track screen-stuck streak using the snapshot fed to the agent.
                screen_similar_streak = 0
                if recent_pre_action_images:
                    for prev_img in reversed(recent_pre_action_images):
                        if _images_are_similar(prev_img, image):
                            screen_similar_streak += 1
                        else:
                            break
                recent_pre_action_images.append(image)

                # Agent decision
                agent_input = AgentInput(
                    instruction=task.instruction,
                    current_image=image,
                    step_count=step_index,
                    history_actions=history_actions,
                )
                output: AgentOutput = await loop.run_in_executor(None, agent.act, agent_input)

                # EscalationPolicy: if signals fire, swap to ReAct and redo this step.
                decision = escalation_policy.evaluate(
                    output,
                    history_actions=history_actions,
                    agent_last_ui_state=getattr(agent, "last_ui_state", None),
                    screen_similar_streak=screen_similar_streak,
                    last_subgoal_index=last_subgoal_index,
                    current_route=session.route,
                )
                if decision.should_escalate and not session.escalated:
                    session.escalated = True
                    session.escalation_reason = decision.reason
                    session.route = ROUTE_REACT
                    await self._broadcast(task_id, WSEvent(event=ESCALATION_TRIGGERED, task_id=task_id, data={
                        "step_index": step_index,
                        "reason": decision.reason,
                        "signals": decision.signals,
                        "from_route": route_decision.route,
                        "to_route": ROUTE_REACT,
                    }))
                    new_agent = ReactGuiAgent()
                    new_agent.reset()
                    new_agent.adopt({
                        "last_ui_state": getattr(agent, "last_ui_state", None),
                        "stuck_count": output.stuck_count,
                    })
                    agent = new_agent
                    output = await loop.run_in_executor(None, agent.act, agent_input)

                if output.current_subgoal_index is not None:
                    last_subgoal_index = output.current_subgoal_index

                # Safety assessment: read model-provided risk fields, no keywords.
                safety = assess_output(output)

                if not safety.is_safe:
                    session.pause_event.clear()
                    session.waiting_for_confirm = True
                    session.pending_action = output
                    session.confirm_event.clear()
                    session.confirm_approved = False
                    await loop.run_in_executor(None, self._update_task_status, db, task_id, "paused")
                    await self._broadcast(task_id, WSEvent(event=RISK_DETECTED, task_id=task_id, data={
                        "step_index": step_index,
                        "action": output.action,
                        "parameters": output.parameters,
                        "risk_level": safety.risk_level,
                        "risk_category": safety.risk_category,
                        "current_state": safety.current_state,
                        "consequence": safety.consequence,
                        "rollback_hint": safety.rollback_hint,
                        "reason": safety.reason,
                        "ui_risk_elements": safety.ui_risk_elements,
                    }))
                    await session.confirm_event.wait()
                    session.waiting_for_confirm = False
                    if session.stop_requested or not session.confirm_approved:
                        break
                    await loop.run_in_executor(None, self._update_task_status, db, task_id, "running")
                    session.pause_event.set()

                # Execute action, then wait for screen to stabilize
                if controller:
                    try:
                        await loop.run_in_executor(None, self._execute_action, controller, output)
                        if output.action != ACTION_COMPLETE:
                            stable_image = await self._wait_for_stable_screen(
                                controller, loop, output.action
                            )
                    except (AdbNotFoundError, DeviceError) as e:
                        logger.warning("Action execution failed: %s", e)

                # Save artifact
                screenshot_path = await loop.run_in_executor(
                    None, save_screenshot, task_id, step_index, image
                )

                # Persist step
                await loop.run_in_executor(
                    None, self._save_step, db, task_id, step_index, output, screenshot_path, safety.risk_level
                )

                screenshot_b64 = _image_to_base64(image)
                history_actions.append({"action": output.action, "parameters": output.parameters})

                await self._broadcast(task_id, WSEvent(event=STEP_COMPLETED, task_id=task_id, data={
                    "step_index": step_index,
                    "action": output.action,
                    "parameters": output.parameters,
                    "raw_output": output.raw_output,
                    "risk_level": safety.risk_level,
                    "risk_category": safety.risk_category,
                    "current_state": safety.current_state,
                    "consequence": safety.consequence,
                    "rollback_hint": safety.rollback_hint,
                    "confidence": output.confidence,
                    "current_subgoal_index": output.current_subgoal_index,
                    "stuck_count": output.stuck_count,
                    "route": session.route,
                    "screenshot_base64": screenshot_b64,
                    "screenshot_path": screenshot_path,
                }))

                if output.action == ACTION_COMPLETE:
                    break

            if session.stop_requested:
                await loop.run_in_executor(None, self._update_task_status, db, task_id, "stopped")
                await self._broadcast(task_id, WSEvent(event=TASK_STOPPED, task_id=task_id))
            else:
                await loop.run_in_executor(None, self._update_task_status, db, task_id, "finished")
                await self._broadcast(task_id, WSEvent(event=TASK_FINISHED, task_id=task_id))

        except Exception as e:
            logger.exception("Task %s failed: %s", task_id, e)
            try:
                await loop.run_in_executor(None, self._update_task_status, db, task_id, "failed")
                await self._broadcast(task_id, WSEvent(event=TASK_FAILED, task_id=task_id,
                                                        data={"error": str(e)}))
            except Exception:
                pass
        finally:
            db.close()
            self._sessions.pop(task_id, None)
            self._tasks.pop(task_id, None)


_engine = RuntimeEngine()


def get_engine() -> RuntimeEngine:
    return _engine
