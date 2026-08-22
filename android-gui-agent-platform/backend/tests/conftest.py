"""Shared fixtures for the conversation-platform test suite (plan.md Step 7).

Environment overrides happen at module import time, BEFORE any ``app.*``
import: ``app.runtime.engine`` reads USE_MOCK_AGENT at import time,
``app.storage.artifact_store`` resolves ARTIFACTS_DIR at import time, and
``app.config.settings`` reads DATABASE_URL on instantiation. Therefore this
file must not import any ``app.*`` module at top level — only inside
fixtures/functions.

TDD contract fixed by this suite (implementation must satisfy):

- ``app.runtime.engine.ConversationEngine(agent_factory=None, memory_store=None)``
  — injectable for tests; defaults come from USE_MOCK_AGENT env / global store.
- ``app.runtime.engine`` imports ``build_memory_context``,
  ``detect_explicit_memory`` and ``extract_turn_memory`` at module level so
  tests can monkeypatch them there.
- ``app.storage.db.init_db()`` drops legacy tables and creates new ones.
- ``app.memory.store.get_store()`` returns the default MemoryStore singleton.
"""

import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Session-wide temp environment (set before any app import)
# ---------------------------------------------------------------------------

_TMP_DIR = Path(tempfile.mkdtemp(prefix="guiagent-tests-"))

os.environ["USE_MOCK_AGENT"] = "1"
os.environ["VLM_API_KEY"] = ""
os.environ["DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
os.environ["ARTIFACTS_DIR"] = str(_TMP_DIR / "artifacts")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

@pytest.fixture()
def db():
    """Fresh schema per test. Legacy-table dropping is asserted separately."""
    from app.storage.db import Base, SessionLocal, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_turn(turn_id):
    """Read a Turn row through a fresh session (engine writes from its own)."""
    from sqlalchemy.exc import OperationalError

    from app.storage.models import Turn
    from app.storage.db import SessionLocal

    for _ in range(20):
        try:
            with SessionLocal() as s:
                return s.query(Turn).filter_by(id=turn_id).first()
        except OperationalError:
            time.sleep(0.05)
    raise RuntimeError(f"turn {turn_id} is not readable")


# ---------------------------------------------------------------------------
# Polling helpers
# ---------------------------------------------------------------------------

async def wait_for(predicate, timeout=8.0, interval=0.05, message="condition not met"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    raise TimeoutError(message)


def wait_for_sync(predicate, timeout=8.0, interval=0.05, message="condition not met"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise TimeoutError(message)


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------

@pytest.fixture()
def memory_dir(tmp_path):
    return tmp_path / "memory"


@pytest.fixture()
def memory_store(memory_dir):
    from app.memory.store import MemoryStore

    return MemoryStore(memory_dir)


# ---------------------------------------------------------------------------
# WebSocket event capture
# ---------------------------------------------------------------------------

@pytest.fixture()
def captured_events(monkeypatch):
    events = []

    async def fake_broadcast(key, message):
        events.append(message)

    from app.ws.connection_manager import manager

    monkeypatch.setattr(manager, "broadcast", fake_broadcast)
    return events


def event_names(events):
    return [e.get("event") for e in events]


# ---------------------------------------------------------------------------
# Conversation / engine
# ---------------------------------------------------------------------------

@pytest.fixture()
def conversation_id(db):
    from app.storage.models import Conversation

    c = Conversation(title="测试会话", device_id=None)
    db.add(c)
    db.commit()
    return c.id


@pytest.fixture()
def make_engine(memory_store, monkeypatch, captured_events):
    """Build a ConversationEngine with injected agent factory and memory store.

    Usage::

        agents = []
        def factory():
            agent = ScriptAgent([...])
            agents.append(agent)
            return agent
        engine = make_engine(factory)
    """

    def _make(agent_factory):
        from app.memory.retriever import build_memory_context as _real_bmc
        from app.runtime import engine as engine_module
        from app.runtime.engine import ConversationEngine

        # Default: no memory text — but never clobber a patch a test already
        # installed (tests may patch before OR after calling make_engine).
        if engine_module.build_memory_context is _real_bmc:
            monkeypatch.setattr(
                engine_module, "build_memory_context", lambda instruction, store=None: ""
            )
        return ConversationEngine(agent_factory=agent_factory, memory_store=memory_store)

    return _make
