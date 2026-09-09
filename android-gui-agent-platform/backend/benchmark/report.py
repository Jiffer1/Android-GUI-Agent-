"""Aggregate android_world checkpoint results into a baseline report.

Run with the android_world virtualenv interpreter (the checkpoint stores
gzipped pickles of android_world objects):

    python report.py <checkpoint_dir> [--out report.md] [--name baseline]

Reads all episodes via IncrementalCheckpointer, reuses the official
`suite_utils.process_episodes` aggregation (per-task success rate, steps,
runtime; joins task_metadata.json for tags/difficulty), adds a coarse
failure breakdown based on our adapter's step data, and writes Markdown.
"""

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

from android_world import checkpointer as checkpointer_lib
from android_world import suite_utils
from android_world import constants


def _failure_category(episode: dict) -> str:
    """Coarse failure classification from episode metadata + step data."""
    if episode.get(constants.EpisodeConstants.EXCEPTION_INFO) is not None:
        return "execution_exception"
    data = episode.get(constants.EpisodeConstants.EPISODE_DATA)
    if isinstance(data, dict) and data.get("skip_reason"):
        reasons = [r for r in data["skip_reason"] if r]
        if reasons and len(reasons) >= max(1, len(data["skip_reason"]) // 2):
            return "invalid_vlm_output"
    return "agent_logic"


def _env_snapshot() -> dict:
    return {
        "VLM_MODEL_ID": os.environ.get("VLM_MODEL_ID", "(default doubao)"),
        "android_world_commit": subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[3] / "third_party"
            / "android_world",
            capture_output=True, text=True,
        ).stdout.strip() or "unknown",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint_dir")
    parser.add_argument("--out", default="")
    parser.add_argument("--name", default="androidworld-baseline")
    args = parser.parse_args()

    checkpointer = checkpointer_lib.IncrementalCheckpointer(args.checkpoint_dir)
    episodes = checkpointer.load()
    if not episodes:
        raise SystemExit("No episodes found in checkpoint dir.")

    df = suite_utils.process_episodes(episodes, print_summary=False)

    total = len(episodes)
    scored = [e for e in episodes
              if e.get(constants.EpisodeConstants.EXCEPTION_INFO) is None]
    successes = sum(
        e.get(constants.EpisodeConstants.IS_SUCCESSFUL) or 0 for e in scored)

    failures = [e for e in scored
                if (e.get(constants.EpisodeConstants.IS_SUCCESSFUL) or 0) < 0.5]
    categories: dict[str, list] = {}
    for ep in failures:
        categories.setdefault(_failure_category(ep), []).append(ep)

    lines = []
    a = lines.append
    a(f"# {args.name}")
    a("")
    a(f"- Date: {datetime.datetime.now().isoformat(timespec='seconds')}")
    a(f"- Checkpoint: `{args.checkpoint_dir}`")
    a(f"- Tasks run: {total} (scored: {len(scored)}, "
      f"exceptions: {total - len(scored)})")
    a(f"- **Overall success rate: {successes}/{len(scored)}"
      f" = {successes / max(1, len(scored)):.1%}**")
    snap = _env_snapshot()
    a(f"- VLM model: `{snap['VLM_MODEL_ID']}`")
    a(f"- android_world commit: `{snap['android_world_commit']}`")
    a("- Config: n_task_combinations=1, seed=30")
    a("")

    a("## Per-task results")
    a("")
    a("| task | trials | success | mean_steps | runtime_s |")
    a("|---|---|---|---|---|")
    for name, row in df.iterrows():
        a(f"| {name} | {int(row['num_complete_trials'])} | "
          f"{row['mean_success_rate']:.2f} | "
          f"{row['mean_episode_length']:.1f} | "
          f"{row['total_runtime_s']:.1f} |")
    a("")

    if "tags" in df.columns:
        a("## By app family (tags)")
        a("")
        a("| tag | tasks | mean success |")
        a("|---|---|---|")
        exploded = df.explode("tags")
        grouped = exploded.groupby("tags", dropna=True)["mean_success_rate"].agg(
            ["count", "mean"])
        for tag, row in grouped.iterrows():
            a(f"| {tag} | {int(row['count'])} | {row['mean']:.2f} |")
        a("")

    a("## Failure breakdown")
    a("")
    a("| category | count |")
    a("|---|---|")
    for cat, eps in sorted(categories.items()):
        a(f"| {cat} | {len(eps)} |")
    a("")
    for cat, eps in sorted(categories.items()):
        a(f"### {cat} ({len(eps)})")
        a("")
        for ep in eps[:5]:
            goal = str(ep.get(constants.EpisodeConstants.GOAL, ""))[:80]
            steps = ep.get(constants.EpisodeConstants.EPISODE_LENGTH)
            a(f"- `{ep.get(constants.EpisodeConstants.TASK_TEMPLATE)}` "
              f"(steps={steps}): {goal}")
        a("")

    text = "\n".join(lines)
    out = args.out or (args.checkpoint_dir.rstrip("/\\") + "_report.md")
    Path(out).write_text(text, encoding="utf-8")
    print(f"[report] wrote {out}")
    print(f"[report] overall: {successes}/{len(scored)} "
          f"= {successes / max(1, len(scored)):.1%}")


if __name__ == "__main__":
    main()
