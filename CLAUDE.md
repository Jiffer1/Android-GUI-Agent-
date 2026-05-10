# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

Two distinct projects live here:

- **`android-gui-agent-platform/`** — A browser-based control console for Android GUI automation. Users submit natural-language tasks; a backend agent executes them on a connected Android device/emulator with real-time screenshot streaming to the UI.
- **`code-for-student/`** — A standalone reference implementation of a GUI agent using OpenAI, with a Planner / UIExtractor / ActionAnalyzer pipeline.

---

## Android GUI Agent Platform

### Running the Backend

```bash
cd android-gui-agent-platform/backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Running the Frontend

```bash
cd android-gui-agent-platform/frontend
npm install
npm run dev        # dev server at http://localhost:5173
npm run build      # production build
```

PowerShell convenience scripts: `scripts/run_backend.ps1`, `scripts/run_frontend.ps1`.

### Architecture

```
Browser (React) ──REST──► FastAPI Backend ──asyncio──► RuntimeEngine
                               │                            │
                           WebSocket                  adb subprocess
                               │                            │
                           React UI               AndroidAdbController
```

**Backend key modules** (`backend/app/`):

| Module | Role |
|---|---|
| `runtime/engine.py` | Async task loop: calls agent, dispatches device actions, runs safety checks, broadcasts WebSocket events |
| `agent/gui_agent.py` | `MockGuiAgent` — deterministic scripted agent (step-indexed); replace with a real VLM here |
| `device/android_adb.py` | `adb` subprocess wrapper; normalizes tap/swipe coordinates to [0, 1000] range |
| `safety/policy.py` | Keyword-based risk detection; pauses execution on high-risk actions (payment, deletion, auth) |
| `runtime/session.py` | Per-task pause/resume/stop state |
| `storage/models.py` | SQLAlchemy ORM — `Task` and `TaskStep` tables; SQLite stored in `data/` |
| `ws/connection_manager.py` | Broadcasts real-time events to subscribed browser clients |

**Frontend key modules** (`frontend/src/`):

- `App.tsx` — React Router: Dashboard → TaskDetail → Devices
- `stores/taskStore.ts` — Zustand global state
- `api/client.ts` / `api/websocket.ts` — REST + WebSocket clients
- Pages: `Dashboard`, `TaskDetail`, `Devices`

**Coordinate system**: all device coordinates are normalized to [0, 1000] × [0, 1000] regardless of physical screen resolution.

**Mock mode**: `MockGuiAgent` runs without a real device. The backend will still execute the full task loop and stream events.

**Replacing the mock agent**: implement the `BaseAgent` interface in `agent/` and wire it into `runtime/engine.py`. The agent receives a screenshot and task description; it returns an `AgentAction`.

---

## Code for Student

```bash
cd code-for-student
pip install -r requirements.txt
python agent.py          # run the agent
python test_runner.py    # run tests
```

The agent pipeline in `agent.py`: `Planner` decomposes the task → `UIExtractor` parses the current screen → `ActionAnalyzer` selects the next action. `agent_base.py` defines the `BaseAgent` ABC. Uses OpenAI API (set `OPENAI_API_KEY`).
