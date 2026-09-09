"""AndroidWorld benchmark entry point (feature 824).

Standalone runner that replicates the official ``run.py`` flow without
modifying the cloned android_world repository:

    env_launcher.load_and_setup_env -> create_suite -> suite_utils.run

Reasons for a standalone entry (vs. patching run.py's ``_get_agent``):
- run.py locates adb only under macOS/Linux paths; on Windows we probe
  explicit SDK locations / PATH instead.
- Lets us inject the backend into ``sys.path``, load ``backend/.env`` for the
  VLM credentials, and route checkpoints + per-step artifacts into
  ``artifacts/benchmark/<run_id>/``.

Must be executed with the android_world virtualenv interpreter, with the
emulator already running (``emulator -avd AndroidWorldAvd -no-snapshot -grpc
8554``). Examples:

    python run_benchmark.py --tasks ContactsAddContact
    python run_benchmark.py --smoke
    python run_benchmark.py --all --perform-emulator-setup
    python run_benchmark.py --resume artifacts/benchmark/<run_id>/checkpoint
"""

import argparse
import datetime
import os
import shutil
import sys
from pathlib import Path

_BENCHMARK_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _BENCHMARK_DIR.parent  # backend/
_REPO_ROOT = _BACKEND_ROOT.parent      # android-gui-agent-platform/

for p in (str(_BACKEND_ROOT),):
    if p not in sys.path:
        sys.path.insert(0, p)

from android_world import checkpointer as checkpointer_lib
from android_world import registry
from android_world import suite_utils
from android_world.env import env_launcher

from benchmark.aw_adapter import AndroidWorldAdapter

TASK_RANDOM_SEED = 30
N_TASK_COMBINATIONS = 1


def _load_dotenv(path: Path) -> None:
    """Load VLM_* keys from backend/.env into os.environ (no override)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("VLM_") and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def _find_adb(explicit: str) -> str:
    if explicit:
        return explicit
    candidates = [
        os.environ.get("ANDROID_HOME", "") and os.path.join(
            os.environ["ANDROID_HOME"], "platform-tools", "adb.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     "Android", "Sdk", "platform-tools", "adb.exe"),
        os.path.join(os.environ.get("USERPROFILE", ""),
                     "AppData", "Local", "Android", "Sdk",
                     "platform-tools", "adb.exe"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    which = shutil.which("adb")
    if which:
        return which
    raise SystemExit(
        "adb not found; pass --adb-path <path to adb.exe> "
        "(install Android SDK platform-tools first)"
    )


def _resolve_tasks(args) -> list:
    if args.tasks:
        return [t.strip() for t in args.tasks.split(",") if t.strip()]
    if args.smoke:
        lines = (_BENCHMARK_DIR / "smoke_tasks.txt").read_text(
            encoding="utf-8").splitlines()
        return [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    return None  # all tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument("--tasks", help="Comma-separated task names")
    task_group.add_argument("--smoke", action="store_true",
                            help="Run the smoke subset (smoke_tasks.txt)")
    task_group.add_argument("--all", action="store_true",
                            help="Run the entire android_world suite")
    parser.add_argument("--resume",
                        help="Checkpoint dir of a previous run to resume")
    parser.add_argument("--perform-emulator-setup", action="store_true",
                        help="First-time app installation/permissions")
    parser.add_argument("--console-port", type=int, default=5554)
    parser.add_argument("--adb-path", default="")
    args = parser.parse_args()

    _load_dotenv(_BACKEND_ROOT / ".env")
    adb_path = _find_adb(args.adb_path)
    tasks = _resolve_tasks(args)

    env = env_launcher.load_and_setup_env(
        console_port=args.console_port,
        emulator_setup=args.perform_emulator_setup,
        adb_path=adb_path,
    )

    task_registry = registry.TaskRegistry()
    suite = suite_utils.create_suite(
        task_registry.get_registry(
            family=registry.TaskRegistry.ANDROID_WORLD_FAMILY),
        n_task_combinations=N_TASK_COMBINATIONS,
        seed=TASK_RANDOM_SEED,
        tasks=tasks,
        use_identical_params=False,
    )
    suite.suite_family = registry.TaskRegistry.ANDROID_WORLD_FAMILY

    agent = AndroidWorldAdapter(env)
    # android_world (non-MiniWoB): wait for the screen to stabilize
    # dynamically instead of a fixed pause.
    agent.transition_pause = None

    if args.resume:
        checkpoint_dir = args.resume
        run_dir = Path(checkpoint_dir).parent.parent
    else:
        run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = _REPO_ROOT / "artifacts" / "benchmark" / run_id
        checkpoint_dir = str(run_dir / "checkpoint")

    run_dir.mkdir(parents=True, exist_ok=True)
    os.environ["BENCHMARK_RUN_DIR"] = str(run_dir)

    n_tasks = len(suite) if tasks is None else len(tasks)
    print(f"[benchmark] agent={agent.name} tasks={n_tasks} "
          f"checkpoint={checkpoint_dir}")
    print(f"[benchmark] artifacts={run_dir}")

    suite_utils.run(
        suite,
        agent,
        checkpointer=checkpointer_lib.IncrementalCheckpointer(checkpoint_dir),
        demo_mode=False,
    )
    print(f"[benchmark] finished; results in {checkpoint_dir}")
    env.close()


if __name__ == "__main__":
    main()
