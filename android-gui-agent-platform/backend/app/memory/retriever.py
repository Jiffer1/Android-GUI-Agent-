"""Build the memory context injected into the agent prompts."""
from __future__ import annotations

from typing import Optional

from app.memory.store import MemoryStore, get_store

BUDGET_CHARS = 4000
RECENT_SECTIONS_FALLBACK = 3


def build_memory_context(instruction: str, store: Optional[MemoryStore] = None) -> str:
    """Assemble the memory text for one instruction.

    Rules (README §3.2): all preferences + path sections whose task_type
    appears in the instruction; with no keyword hit, the most recent
    sections (last in file order). Hard budget: 4000 chars.
    """
    store = store or get_store()

    parts = []

    prefs = store.preferences()
    if prefs:
        parts.append("【用户偏好】\n" + "\n".join(f"- {p}" for p in prefs))

    paths = store.paths()
    if paths:
        instruction_text = instruction or ""
        hits = [tt for tt in paths if tt and tt in instruction_text]
        sections = hits if hits else list(paths.keys())[-RECENT_SECTIONS_FALLBACK:]
        for tt in sections:
            entries = paths[tt]
            if not entries:
                continue
            parts.append(f"【历史路径·{tt}】\n" + "\n".join(f"- {e}" for e in entries))

    ctx = "\n\n".join(parts)
    if len(ctx) > BUDGET_CHARS:
        ctx = ctx[:BUDGET_CHARS]
    return ctx
