"""AndroidWorld adapter for ChatGuiAgent (benchmark mode).

Bridges the official android_world harness to the platform's ChatGuiAgent:
inherits ``EnvironmentInteractingAgent``, feeds each step's screenshot and the
task goal into ``ChatGuiAgent.act`` via ``AgentInput``, and maps the resulting
action onto android_world's ``JSONAction`` space for execution.

Benchmark-mode behaviour (per feature 824, mode switch since 908):
- No DB / WS / memory / conversation context / ASK waiting: ASK maps to
  ``status(infeasible)`` and terminates the episode.
- The agent runs via ``ChatGuiAgent(mode="benchmark")``: the prompt carries
  no risk block / chat context / memory (feature 908).
- Post-hoc feedback (feature 908): after each EXECUTED action the adapter
  compares the previous step's decision screenshot with the current state
  and injects a deterministic receipt ("上步 ... 后页面已变化/无变化")
  via ``AgentInput.last_step_feedback``.
- Coordinates are converted from the agent's [0,1000] normalized space to
  logical-screen pixels in one place (``_to_pixels``).
- Per-step artifacts (before screenshot, raw VLM output, mapped action) are
  written to ``$BENCHMARK_RUN_DIR/ep<N>_<goal-slug>/step_<K>/`` when that
  environment variable is set by the benchmark runner.

This module must be importable from the android_world virtualenv: it inserts
the backend root into ``sys.path`` and depends only on the standard library,
PIL, and the packages above (incl. ``app.utils``, a FastAPI-free helper
module shared with the engine).
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Make `app` importable regardless of the caller's working directory.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from PIL import Image

from android_world.agents import base_agent
from android_world.env import json_action

from app.agent import schemas as agent_schemas
from app.agent.chat_agent import ChatGuiAgent
from app.utils import images_are_similar
from benchmark.app_name_map import resolve_app_name

_STATUS = "status"
_GOAL_COMPLETE = "complete"
_GOAL_INFEASIBLE = "infeasible"


class AndroidWorldAdapter(base_agent.EnvironmentInteractingAgent):
    """Runs ChatGuiAgent inside the android_world benchmark harness."""

    def __init__(
        self,
        env,
        name: str = "chat_gui_agent",
        disable_business_overrides: bool = True,
    ):
        super().__init__(env, name)
        # `disable_business_overrides` kept for run_benchmark.py / old flags;
        # benchmark mode implies everything it expressed (feature 908).
        self._agent = ChatGuiAgent(mode="benchmark")
        self._history_actions = []
        self._step_count = 0
        self._episode_index = 0
        self._episode_dir: Optional[Path] = None
        # Post-hoc feedback state: the previous step's decision screenshot
        # and executed action (None when the step did not execute anything).
        self._prev_before: Optional[Image.Image] = None
        self._last_executed: Optional[Tuple[str, str]] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self, go_home: bool = False):
        super().reset(go_home)
        # Hide on-screen coordinates overlay so it doesn't confuse the VLM.
        try:
            self.env.hide_automation_ui()
        except Exception:
            pass
        self._agent.reset()
        self._history_actions = []
        self._step_count = 0
        self._episode_index += 1
        self._episode_dir = None
        self._prev_before = None
        self._last_executed = None

    # ------------------------------------------------------------------
    # Main step
    # ------------------------------------------------------------------

    def step(self, goal: str) -> base_agent.AgentInteractionResult:
        self._ensure_episode_dir(goal)
        state = self.get_post_transition_state()
        image = Image.fromarray(state.pixels)

        agent_input = agent_schemas.AgentInput(
            instruction=goal,
            current_image=image,
            step_count=self._step_count,
            history_actions=list(self._history_actions),
            last_step_feedback=self._build_feedback(image),
        )
        output = self._agent.act(agent_input)

        json_act, done = self._map_action(output)

        if json_act is None and not done:
            # Skipped step == one quiet round (semantics of the official
            # `wait` action): absorb app/page loading before the next poll.
            # Deliberate loading waits are a separate action since feature
            # 825: WAIT maps onto the official `wait` action instead.
            time.sleep(2)

        executed = False
        if json_act is not None and json_act.action_type != _STATUS:
            try:
                self.env.execute_action(json_act)
                executed = True
            except Exception as exc:  # keep the run alive; recorded as failure
                json_act = json_action.JSONAction(
                    action_type="unknown", text=f"execute_error: {exc}"
                )

        step_data: Dict[str, Any] = {
            "action": output.action,
            "parameters": output.parameters,
            "mapped_action": json_act.json_str() if json_act else None,
            "executed": executed,
            "skip_reason": output.skip_reason,
            "raw_output": output.raw_output,
            "goal": goal,
        }

        self._write_step_artifacts(image, output, json_act, done, executed)
        if executed:
            self._history_actions.append(
                {"action": output.action, "parameters": output.parameters}
            )

        # Post-hoc feedback state for the NEXT step's receipt (feature 908):
        # remember this step's decision screenshot + action when (and only
        # when) something was actually executed.
        if executed:
            summary = json.dumps(output.parameters, ensure_ascii=False)
            if len(summary) > 80:
                summary = summary[:80] + "…"
            self._prev_before = image
            self._last_executed = (output.action, summary)
        else:
            self._prev_before = None
            self._last_executed = None

        self._step_count += 1
        return base_agent.AgentInteractionResult(done, step_data)

    def _build_feedback(self, current: Image.Image) -> str:
        """Deterministic receipt of the previous executed action (908)."""
        if self._prev_before is None or self._last_executed is None:
            return ""
        action, summary = self._last_executed
        changed = not images_are_similar(self._prev_before, current)
        verdict = "已变化" if changed else "无变化"
        return f"上一步 {action} {summary} 后页面{verdict}"

    # ------------------------------------------------------------------
    # Action mapping ([0,1000] normalized -> android_world JSONAction)
    # ------------------------------------------------------------------

    def _map_action(
        self, output: agent_schemas.AgentOutput
    ) -> Tuple[Optional[json_action.JSONAction], bool]:
        action = output.action
        params = output.parameters or {}

        if action == agent_schemas.ACTION_COMPLETE:
            return (
                json_action.JSONAction(
                    action_type=_STATUS, goal_status=_GOAL_COMPLETE
                ),
                True,
            )
        if action == agent_schemas.ACTION_ASK:
            # Unattended benchmark: nobody answers; treat as infeasible.
            return (
                json_action.JSONAction(
                    action_type=_STATUS, goal_status=_GOAL_INFEASIBLE
                ),
                True,
            )
        if not output.executable or output.skip_reason:
            return None, False

        if action == agent_schemas.ACTION_WAIT:
            # The agent's explicit "let the page settle" action: mapped onto
            # the official wait action (native in android_world's action
            # space); the harness performs the actual sleep.
            return json_action.JSONAction(action_type="wait"), False

        if action == agent_schemas.ACTION_CLICK:
            point = params.get("point")
            if not point:
                return None, False
            x, y = self._to_pixels(point)
            return json_action.JSONAction(action_type="click", x=x, y=y), False

        if action == agent_schemas.ACTION_SCROLL:
            direction = self._scroll_direction(params)
            return (
                json_action.JSONAction(action_type="scroll", direction=direction),
                False,
            )

        if action == agent_schemas.ACTION_TYPE:
            text = str(params.get("text", ""))
            if not text:
                return None, False
            return json_action.JSONAction(action_type="input_text", text=text), False

        if action == agent_schemas.ACTION_OPEN:
            raw = str(params.get("app_name", "")).strip()
            if not raw:
                return None, False
            app_name = resolve_app_name(raw)
            return (
                json_action.JSONAction(action_type="open_app", app_name=app_name),
                False,
            )

        if action == agent_schemas.ACTION_BACK:
            return json_action.JSONAction(action_type="navigate_back"), False

        if action == agent_schemas.ACTION_HOME:
            return json_action.JSONAction(action_type="navigate_home"), False

        return None, False

    def _to_pixels(self, point) -> Tuple[int, int]:
        width, height = self.env.logical_screen_size
        x = max(0, min(int(point[0] / 1000 * width), width - 1))
        y = max(0, min(int(point[1] / 1000 * height), height - 1))
        return x, y

    def _scroll_direction(self, params: Dict[str, Any]) -> str:
        start = params.get("start_point") or [500, 800]
        end = params.get("end_point") or [500, 300]
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        if abs(dx) > abs(dy):
            return "right" if dx > 0 else "left"
        # Finger swipes up (dy<0) to reveal content below => scroll "down",
        # matching android_world's scroll-direction semantics.
        return "down" if dy < 0 else "up"

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------

    def _ensure_episode_dir(self, goal: str) -> None:
        if self._episode_dir is not None:
            return
        run_dir = os.environ.get("BENCHMARK_RUN_DIR", "")
        if not run_dir:
            return
        slug = re.sub(r"[^0-9A-Za-z_.-]+", "_", goal).strip("_")[:40] or "task"
        self._episode_dir = Path(run_dir) / f"ep{self._episode_index:03d}_{slug}"
        self._episode_dir.mkdir(parents=True, exist_ok=True)
        (self._episode_dir / "goal.txt").write_text(goal, encoding="utf-8")

    def _write_step_artifacts(self, image, output, json_act, done, executed):
        if self._episode_dir is None:
            return
        try:
            step_dir = self._episode_dir / f"step_{self._step_count:03d}"
            step_dir.mkdir(parents=True, exist_ok=True)
            image.save(step_dir / "before.png")
            (step_dir / "raw_output.txt").write_text(
                output.raw_output or "", encoding="utf-8"
            )
            (step_dir / "action.json").write_text(
                json.dumps(
                    {
                        "action": output.action,
                        "parameters": output.parameters,
                        "mapped": json_act.as_dict() if json_act else None,
                        "executed": executed,
                        "done": done,
                        "skip_reason": output.skip_reason,
                        "executable": output.executable,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            # Artifact persistence must never break a benchmark run.
            pass
