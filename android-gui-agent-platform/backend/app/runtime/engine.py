"""ConversationEngine: one turn per user message, chat-driven execution.

Replaces the old single-instruction RuntimeEngine. Key semantics (fixed by
tests/README §5.3):

- two-level mutual exclusion: one active turn per conversation AND per device
- a fresh agent instance per turn (no cross-turn subgoal leakage); cross-turn
  context flows exclusively through AgentInput.conversation_context / memory_text
- ACTION_ASK pauses the turn (waiting_ask) and resumes with the user's reply
  injected; steps keep advancing, the turn never restarts
- high-risk actions pause for confirmation (waiting_confirm); cancelling stops
  the turn but the conversation accepts a new one
- every agent output (including ASK and COMPLETE) is persisted as a TurnStep
- turn end: summarize -> summary Message -> memory write (explicit + auto)
  -> status=finished -> WS broadcast, so a visible "finished" turn always has
  its summary and memory already applied
- stop_turn sets ask_event AND confirm_event (no hanging waits)
"""
import asyncio
import base64
import io
import json
import logging
import os
import threading
from collections import deque
from typing import Deque, Dict, Optional

from PIL import Image

from app.agent.chat_agent import ChatGuiAgent
from app.agent.gui_agent import MockGuiAgent
from app.agent.schemas import AgentInput, AgentOutput, ACTION_ASK, ACTION_COMPLETE
from app.device.base import AdbNotFoundError, DeviceError
from app.device.registry import get_controller
# Imported for the engine's own calls; tests monkeypatch these names in this
# module's namespace (see tests/conftest.py) — do not change the import style.
from app.memory.extractor import detect_explicit_memory, extract_turn_memory
from app.memory.retriever import build_memory_context
from app.runtime.events import (
    WSEvent,
    MESSAGE_CREATED, TURN_STARTED, TURN_FINISHED, TURN_FAILED, TURN_STOPPED,
    STEP_STARTED, STEP_COMPLETED, ASK_REQUESTED, ASK_ANSWERED,
    RISK_DETECTED, RISK_OBSERVED, MEMORY_UPDATED,
)
from app.runtime.session import ConversationSession
from app.safety.policy import assess_output
from app.storage.artifact_store import save_screenshot
from app.storage.db import SessionLocal
from app.storage.models import Conversation, Message, Turn, TurnStep
from app.ws.connection_manager import manager

logger = logging.getLogger(__name__)

USE_MOCK_AGENT = os.environ.get("USE_MOCK_AGENT", "").lower() in {"1", "true", "yes"}

CONTEXT_MESSAGES = 6

# Per-action max wait seconds for screen stabilization (unchanged from the
# legacy engine; ASK performs no device action and never waits).
_STABLE_MAX: Dict[str, float] = {
    "OPEN": 20.0,
    "CLICK": 10.0,
    "SCROLL": 6.0,
    "TYPE": 3.0,
    "BACK": 8.0,
    "HOME": 8.0,
}
_STABLE_HARD_CAP: float = float(os.environ.get("WAIT_STABLE_MAX_SECONDS", "25"))
_STABLE_CONSECUTIVE: int = 2
_STABLE_EARLY_THRESHOLD: float = 0.005


def _image_to_base64(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _images_are_similar(img1: Image.Image, img2: Image.Image, threshold: float = 0.02) -> bool:
    """Return True when two screenshots differ by less than threshold."""
    size = (100, 100)
    a = list(img1.resize(size).convert("L").getdata())
    b = list(img2.resize(size).convert("L").getdata())
    diff = sum(abs(p - q) for p, q in zip(a, b))
    return diff / (255 * len(a)) < threshold


def _make_placeholder_image() -> Image.Image:
    return Image.new("RGB", (400, 200), color=(30, 30, 30))


class ConversationEngine:
    def __init__(self, agent_factory=None, memory_store=None):
        self._sessions: Dict[str, ConversationSession] = {}
        self._agent_factory = agent_factory
        self._memory_store = memory_store
        # High-risk actions the user explicitly declined, per conversation.
        # The same action proposed again in a later turn is auto-skipped
        # instead of re-prompting (contract: risk_cancel ... conversation_reusable).
        self._declined_actions: Dict[str, set] = {}
        # Turns run on a dedicated background loop: their lifetime must not
        # be tied to whatever event loop happened to serve the HTTP request
        # (test clients create and dispose a fresh loop per request).
        self._turn_loop: Optional[asyncio.AbstractEventLoop] = None
        self._turn_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Public control API
    # ------------------------------------------------------------------

    async def start_turn(self, conversation_id: str, text: str) -> Turn:
        if conversation_id in self._sessions:
            raise ValueError(f"Conversation {conversation_id} already has an active turn")

        db = SessionLocal()
        try:
            conversation = db.query(Conversation).filter_by(id=conversation_id).first()
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found")

            # Device-level mutual exclusion: one active turn per device.
            if conversation.device_id:
                for other_id, other in self._sessions.items():
                    if other_id != conversation_id and other.device_id == conversation.device_id:
                        raise ValueError(
                            f"Device {conversation.device_id} is busy "
                            f"(conversation {other_id} is running)")

            message = Message(
                conversation_id=conversation_id,
                role="user",
                kind="text",
                content=text,
            )
            db.add(message)
            db.flush()

            turn = Turn(
                conversation_id=conversation_id,
                user_message_id=message.id,
                status="pending",
                max_steps=self._max_steps(),
            )
            db.add(turn)
            conversation.status = "active"
            db.commit()
            db.refresh(turn)

            await self._broadcast(conversation_id, WSEvent(
                event=MESSAGE_CREATED, conversation_id=conversation_id,
                data={"message_id": message.id, "role": "user", "kind": "text", "content": text}))
            await self._broadcast(conversation_id, WSEvent(
                event=TURN_STARTED, conversation_id=conversation_id,
                data={"turn_id": turn.id, "instruction": text}))

            session = ConversationSession(conversation_id)
            session.turn_id = turn.id
            session.device_id = conversation.device_id
            self._sessions[conversation_id] = session

            turn_loop = self._get_turn_loop()
            session.turn_loop = turn_loop
            session.turn_task = asyncio.run_coroutine_threadsafe(
                self._run_turn(turn.id, conversation_id, text, session), turn_loop)
            return turn
        finally:
            db.close()

    async def reply_ask(self, conversation_id: str, text: str) -> None:
        session = self._sessions.get(conversation_id)
        if session is None or not session.waiting_ask:
            raise ValueError(f"No pending ask for conversation {conversation_id}")

        db = SessionLocal()
        try:
            message = Message(
                conversation_id=conversation_id,
                turn_id=session.turn_id,
                role="user",
                kind="text",
                content=text,
            )
            db.add(message)
            db.commit()
            await self._broadcast(conversation_id, WSEvent(
                event=MESSAGE_CREATED, conversation_id=conversation_id,
                data={"message_id": message.id, "role": "user", "kind": "text", "content": text}))
        finally:
            db.close()

        await self._broadcast(conversation_id, WSEvent(
            event=ASK_ANSWERED, conversation_id=conversation_id,
            data={"turn_id": session.turn_id, "reply": text}))
        session.ask_reply = text
        self._set_event_threadsafe(session.turn_loop, session.ask_event)

    def confirm(self, conversation_id: str, approved: bool) -> None:
        session = self._sessions.get(conversation_id)
        if session is None or not session.waiting_confirm:
            raise ValueError(f"No pending risk confirmation for conversation {conversation_id}")
        session.confirm_approved = approved
        self._set_event_threadsafe(session.turn_loop, session.confirm_event)

    def stop_turn(self, conversation_id: str) -> None:
        session = self._sessions.get(conversation_id)
        if session is None:
            raise ValueError(f"No active turn for conversation {conversation_id}")
        session.stop_requested = True
        # Release every wait point, or a waiting coroutine could hang forever.
        self._set_event_threadsafe(session.turn_loop, session.ask_event)
        self._set_event_threadsafe(session.turn_loop, session.confirm_event)

    async def stop_and_wait(self, conversation_id: str, timeout: float = 5.0) -> None:
        """Stop the active turn (if any) and wait for its coroutine to exit.

        Used before deleting a conversation so the coroutine cannot write rows
        that were just deleted.
        """
        session = self._sessions.get(conversation_id)
        if session is None:
            return
        try:
            self.stop_turn(conversation_id)
        except ValueError:
            return
        task = session.turn_task
        if task is not None:
            try:
                await asyncio.wait_for(asyncio.wrap_future(task), timeout)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception) as e:
                logger.warning("stop_and_wait: turn task did not exit cleanly: %s", e)

    def get_session(self, conversation_id: str) -> Optional[ConversationSession]:
        return self._sessions.get(conversation_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _max_steps() -> int:
        from app.config.settings import settings
        return settings.MAX_STEPS

    def _make_agent(self):
        if self._agent_factory is not None:
            return self._agent_factory()
        if USE_MOCK_AGENT:
            return MockGuiAgent()
        return ChatGuiAgent()

    def _get_turn_loop(self) -> asyncio.AbstractEventLoop:
        if self._turn_loop is None or self._turn_loop.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever, daemon=True, name="conversation-turns")
            thread.start()
            self._turn_loop = loop
            self._turn_thread = thread
        return self._turn_loop

    @staticmethod
    def _set_event_threadsafe(loop: Optional[asyncio.AbstractEventLoop],
                              event: asyncio.Event) -> None:
        """Set an asyncio.Event from any thread (events belong to the turn loop)."""
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(event.set)
        else:
            event.set()

    async def _broadcast(self, conversation_id: str, event: WSEvent) -> None:
        await manager.broadcast(conversation_id, event.model_dump())

    async def _wait_for_stable_screen(self, controller, loop, action: str) -> Image.Image:
        min_waits = {"OPEN": 2.5, "CLICK": 0.6, "SCROLL": 0.4, "TYPE": 0.15,
                     "BACK": 0.6, "HOME": 0.8}
        await asyncio.sleep(min_waits.get(action, 0.6))

        max_extra = min(_STABLE_MAX.get(action, 10.0), _STABLE_HARD_CAP)
        poll = 0.35
        elapsed = 0.0
        consecutive_similar = 0
        consecutive_static = 0
        prev = await loop.run_in_executor(None, controller.screenshot)

        while elapsed < max_extra:
            await asyncio.sleep(poll)
            elapsed += poll
            curr = await loop.run_in_executor(None, controller.screenshot)

            if _images_are_similar(prev, curr, threshold=_STABLE_EARLY_THRESHOLD):
                consecutive_static += 1
                consecutive_similar += 1
            elif _images_are_similar(prev, curr):
                consecutive_static = 0
                consecutive_similar += 1
            else:
                consecutive_static = 0
                consecutive_similar = 0

            if consecutive_static >= _STABLE_CONSECUTIVE or consecutive_similar >= _STABLE_CONSECUTIVE:
                return curr
            prev = curr

        return prev

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
        elif action in ("COMPLETE", "ASK"):
            pass

    def _load_context(self, db, conversation_id: str):
        msgs = (db.query(Message)
                .filter_by(conversation_id=conversation_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(CONTEXT_MESSAGES)
                .all())
        msgs.reverse()
        return [{"role": m.role, "kind": m.kind, "content": m.content} for m in msgs]

    def _save_step(self, db, turn_id: str, step_index: int, output: AgentOutput,
                   screenshot_path: str, risk_level: str) -> None:
        step = TurnStep(
            turn_id=turn_id,
            step_index=step_index,
            action=output.action,
            parameters=json.dumps(output.parameters, ensure_ascii=False),
            status="completed",
            screenshot_path=screenshot_path,
            raw_output=output.raw_output,
            risk_level=risk_level,
        )
        db.add(step)
        db.commit()

    # ------------------------------------------------------------------
    # Main turn loop
    # ------------------------------------------------------------------

    async def _run_turn(self, turn_id: str, conversation_id: str,
                        instruction: str, session: ConversationSession) -> None:
        loop = asyncio.get_running_loop()
        db = SessionLocal()
        cancelled = False
        try:
            conversation = db.query(Conversation).filter_by(id=conversation_id).first()
            turn = db.query(Turn).filter_by(id=turn_id).first()
            if conversation is None or turn is None:
                logger.error("Turn %s: conversation/turn row missing", turn_id)
                return

            turn.status = "running"
            db.commit()
            session.status = "running"

            has_device = bool(conversation.device_id)
            controller = get_controller(conversation.device_id) if has_device else None

            context = self._load_context(db, conversation_id)
            memory_text = ""
            try:
                memory_text = build_memory_context(instruction, store=self._memory_store)
            except Exception as e:
                logger.warning("build_memory_context failed (ignored): %s", e)

            agent = self._make_agent()
            agent.reset()

            history_actions = []
            stable_image: Optional[Image.Image] = None
            last_input: Optional[AgentInput] = None
            risk_cancelled = False

            for step_index in range(turn.max_steps):
                if session.stop_requested:
                    break

                await self._broadcast(conversation_id, WSEvent(
                    event=STEP_STARTED, conversation_id=conversation_id,
                    data={"turn_id": turn_id, "step_index": step_index}))

                if stable_image is not None:
                    image = stable_image
                    stable_image = None
                elif controller:
                    try:
                        image = await loop.run_in_executor(None, controller.screenshot)
                    except (AdbNotFoundError, DeviceError) as e:
                        await self._fail_turn(db, conversation_id, turn, str(e))
                        return
                else:
                    image = _make_placeholder_image()

                agent_input = AgentInput(
                    instruction=instruction,
                    current_image=image,
                    step_count=step_index,
                    history_actions=history_actions,
                    conversation_context=context,
                    memory_text=memory_text,
                    ask_reply=session.ask_reply,
                )
                last_input = agent_input
                output: AgentOutput = await loop.run_in_executor(None, agent.act, agent_input)

                safety = assess_output(output)

                if output.action == ACTION_ASK:
                    question = output.parameters.get("question", "")
                    options = output.parameters.get("options", [])
                    screenshot_path = await loop.run_in_executor(
                        None, save_screenshot, conversation_id, turn_id, step_index, image)
                    await loop.run_in_executor(
                        None, self._save_step, db, turn_id, step_index, output,
                        screenshot_path, safety.risk_level)
                    ask_message = Message(
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        role="assistant",
                        kind="ask",
                        content=question,
                    )
                    ask_message.set_extra({"options": options})
                    db.add(ask_message)
                    turn.status = "waiting_ask"
                    db.commit()
                    session.waiting_ask = True
                    await self._broadcast(conversation_id, WSEvent(
                        event=ASK_REQUESTED, conversation_id=conversation_id,
                        data={"turn_id": turn_id, "step_index": step_index,
                              "question": question, "options": options}))
                    await session.ask_event.wait()
                    session.waiting_ask = False
                    # Write the reply back into the input that triggered the ASK:
                    # agents keep input references, so the answer is visible there.
                    agent_input.ask_reply = session.ask_reply
                    if session.stop_requested:
                        break
                    # Resume from the current screen: keep advancing steps.
                    continue

                if safety.risk_level == "medium":
                    await self._broadcast(conversation_id, WSEvent(
                        event=RISK_OBSERVED, conversation_id=conversation_id,
                        data={"turn_id": turn_id, "step_index": step_index,
                              "action": output.action, "parameters": output.parameters,
                              "risk_level": safety.risk_level,
                              "risk_category": safety.risk_category,
                              "current_state": safety.current_state,
                              "consequence": safety.consequence,
                              "reason": safety.reason}))

                if not safety.is_safe:
                    signature = (output.action, json.dumps(
                        output.parameters, sort_keys=True, ensure_ascii=False))
                    declined = self._declined_actions.setdefault(conversation_id, set())
                    if signature in declined:
                        # The user declined this exact action in an earlier turn
                        # of this conversation: skip it instead of asking again.
                        output.executable = False
                        output.skip_reason = "risk_previously_declined"
                    else:
                        turn.status = "waiting_confirm"
                        db.commit()
                        session.waiting_confirm = True
                        session.pending_action = output
                        session.confirm_event.clear()
                        session.confirm_approved = False
                        await self._broadcast(conversation_id, WSEvent(
                            event=RISK_DETECTED, conversation_id=conversation_id,
                            data={"turn_id": turn_id, "step_index": step_index,
                                  "action": output.action, "parameters": output.parameters,
                                  "risk_level": safety.risk_level,
                                  "risk_category": safety.risk_category,
                                  "current_state": safety.current_state,
                                  "consequence": safety.consequence,
                                  "rollback_hint": safety.rollback_hint,
                                  "reason": safety.reason,
                                  "ui_risk_elements": safety.ui_risk_elements}))
                        await session.confirm_event.wait()
                        session.waiting_confirm = False
                        if session.stop_requested or not session.confirm_approved:
                            if not session.confirm_approved and not session.stop_requested:
                                declined.add(signature)
                            risk_cancelled = not session.confirm_approved and not session.stop_requested
                            break

                action_executed = output.executable
                if not output.executable:
                    logger.info("Step %d skipped (skip_reason=%s action=%s)",
                                step_index, output.skip_reason, output.action)

                if action_executed and controller:
                    try:
                        await loop.run_in_executor(None, self._execute_action, controller, output)
                        if output.action != ACTION_COMPLETE:
                            stable_image = await self._wait_for_stable_screen(
                                controller, loop, output.action)
                    except (AdbNotFoundError, DeviceError) as e:
                        logger.warning("Action execution failed: %s", e)

                screenshot_path = await loop.run_in_executor(
                    None, save_screenshot, conversation_id, turn_id, step_index, image)

                await loop.run_in_executor(
                    None, self._save_step, db, turn_id, step_index, output,
                    screenshot_path, safety.risk_level)

                screenshot_b64 = _image_to_base64(image)
                if action_executed:
                    history_actions.append(
                        {"action": output.action, "parameters": output.parameters})

                await self._broadcast(conversation_id, WSEvent(
                    event=STEP_COMPLETED, conversation_id=conversation_id,
                    data={"turn_id": turn_id, "step_index": step_index,
                          "action": output.action, "parameters": output.parameters,
                          "raw_output": output.raw_output,
                          "risk_level": safety.risk_level,
                          "risk_category": safety.risk_category,
                          "current_state": safety.current_state,
                          "consequence": safety.consequence,
                          "rollback_hint": safety.rollback_hint,
                          "confidence": output.confidence,
                          "current_subgoal_index": output.current_subgoal_index,
                          "stuck_count": output.stuck_count,
                          "screenshot_base64": screenshot_b64,
                          "screenshot_path": screenshot_path}))

                if output.action == ACTION_COMPLETE:
                    break

            if session.stop_requested or risk_cancelled:
                turn.status = "stopped"
                from datetime import datetime as _dt
                turn.finished_at = _dt.utcnow()
                db.commit()
                self._set_conversation_idle(db, conversation_id)
                await self._broadcast(conversation_id, WSEvent(
                    event=TURN_STOPPED, conversation_id=conversation_id,
                    data={"turn_id": turn_id}))
            else:
                summary = ""
                try:
                    summarize_input = last_input or AgentInput(
                        instruction=instruction, current_image=None, step_count=0)
                    summary = await loop.run_in_executor(
                        None, agent.summarize_turn, summarize_input)
                except Exception as e:
                    logger.warning("summarize_turn failed (ignored): %s", e)
                if not summary:
                    summary = f"本轮共执行了 {len(history_actions)} 步操作。"

                summary_message = Message(
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    role="assistant",
                    kind="summary",
                    content=summary,
                )
                db.add(summary_message)

                memory_changed = False
                try:
                    explicit = detect_explicit_memory(instruction)
                    if explicit and self._memory_store is not None:
                        self._memory_store.add("preference", explicit)
                        memory_changed = True
                except Exception as e:
                    logger.warning("explicit memory save failed (ignored): %s", e)
                try:
                    if self._memory_store is not None:
                        ops = extract_turn_memory(
                            instruction, history_actions, summary,
                            ask_qa=session.ask_reply)
                        if ops:
                            self._memory_store.apply(ops)
                            memory_changed = True
                except Exception as e:
                    logger.warning("auto memory extraction failed (ignored): %s", e)

                turn.status = "finished"
                from datetime import datetime as _dt2
                turn.finished_at = _dt2.utcnow()
                db.commit()
                self._set_conversation_idle(db, conversation_id)

                await self._broadcast(conversation_id, WSEvent(
                    event=MESSAGE_CREATED, conversation_id=conversation_id,
                    data={"message_id": summary_message.id, "role": "assistant",
                          "kind": "summary", "content": summary}))
                if memory_changed:
                    await self._broadcast(conversation_id, WSEvent(
                        event=MEMORY_UPDATED, conversation_id=conversation_id,
                        data={"turn_id": turn_id}))
                await self._broadcast(conversation_id, WSEvent(
                    event=TURN_FINISHED, conversation_id=conversation_id,
                    data={"turn_id": turn_id, "summary": summary}))

        except Exception as e:
            logger.exception("Turn %s failed: %s", turn_id, e)
            try:
                await self._fail_turn(db, conversation_id,
                                      db.query(Turn).filter_by(id=turn_id).first(), str(e))
            except Exception:
                pass
        finally:
            db.close()
            self._sessions.pop(conversation_id, None)

    def _set_conversation_idle(self, db, conversation_id: str) -> None:
        conversation = db.query(Conversation).filter_by(id=conversation_id).first()
        if conversation is not None:
            conversation.status = "idle"
            db.commit()

    async def _fail_turn(self, db, conversation_id: str, turn: Optional[Turn], error: str) -> None:
        if turn is not None:
            turn.status = "failed"
            turn.error = error
            from datetime import datetime as _dt
            turn.finished_at = _dt.utcnow()
            db.commit()
        self._set_conversation_idle(db, conversation_id)
        await self._broadcast(conversation_id, WSEvent(
            event=TURN_FAILED, conversation_id=conversation_id,
            data={"turn_id": turn.id if turn else "", "error": error}))


from app.memory.store import get_store as _get_default_store

_engine = ConversationEngine(memory_store=_get_default_store())


def get_engine() -> ConversationEngine:
    return _engine
