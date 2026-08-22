"""Extract long-term memory from a finished turn.

Two write paths:
- explicit: the user said "记住…/以后都…" — detected by regex, no VLM needed
- automatic: a single VLM call summarizes the turn into add/update ops

Both degrade silently: no API key, parse failure or API error → no memory.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EXPLICIT_PATTERNS = [
    re.compile(r"(?:请)?记住(?P<content>.+)"),
    re.compile(r"以后(?:都|尽量|一直|总是)(?P<content>.+)"),
]


def detect_explicit_memory(text: str) -> Optional[str]:
    """Return the memory content when the user explicitly asks to remember."""
    for pattern in _EXPLICIT_PATTERNS:
        m = pattern.search((text or "").strip())
        if m:
            content = m.group("content").strip().rstrip("。.!！;；,，")
            if content:
                return content
    return None


def parse_ops(raw: str) -> List[Dict[str, Any]]:
    """Normalize a VLM reply (bare or ```json fenced) into add/update ops."""
    if not raw:
        return []
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return []
        text = text[start:end + 1]
    try:
        obj = json.loads(text)
    except Exception:
        return []
    if not isinstance(obj, dict):
        return []

    ops: List[Dict[str, Any]] = []
    for item in obj.get("add") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        op = {
            "op": "add",
            "kind": "path" if item.get("kind") == "path" else "preference",
            "content": content,
        }
        task_type = str(item.get("task_type", "") or "").strip()
        if task_type:
            op["task_type"] = task_type
        ops.append(op)
    for item in obj.get("update") or []:
        if not isinstance(item, dict):
            continue
        match = str(item.get("match", "")).strip()
        content = str(item.get("content", "")).strip()
        if match and content:
            ops.append({"op": "update", "match": match, "content": content})
    return ops


def extract_turn_memory(instruction: str, actions: List[Dict[str, Any]],
                        summary: str, ask_qa: str = "") -> List[Dict[str, Any]]:
    """Ask the VLM what is worth remembering from this turn. Silent on failure."""
    if not os.environ.get("VLM_API_KEY"):
        return []
    try:
        from app.memory.retriever import build_memory_context

        existing = build_memory_context(instruction)
        actions_text = json.dumps(
            [{"action": a.get("action"), "parameters": a.get("parameters")}
             for a in (actions or [])[-15:]],
            ensure_ascii=False,
        )
        system_prompt = (
            "你是一个记忆提取器。根据本轮任务执行记录和已有记忆，决定需要新增或更新的长期记忆。\n"
            "只提取值得跨会话记住的内容：用户偏好（如常用App、支付习惯）和可复用的执行路径。\n"
            "输出严格 JSON：\n"
            '{"add":[{"kind":"preference|path","content":"...","task_type":"..."}],'
            '"update":[{"match":"现有记忆原文","content":"更新后的内容"}]}\n'
            "没有值得记住的内容时输出 {\"add\":[],\"update\":[]}。禁止输出额外文字。"
        )
        user_prompt = (
            f"本轮指令：{instruction}\n"
            f"执行动作：{actions_text}\n"
            f"执行总结：{summary or '无'}\n"
            f"用户澄清问答：{ask_qa or '无'}\n"
            f"已有记忆：\n{existing or '（空）'}"
        )
        from app.agent.chat_agent import call_vlm
        raw = call_vlm(system_prompt, user_prompt)
        return parse_ops(raw)
    except Exception as e:
        logger.warning("extract_turn_memory failed (ignored): %s", e)
        return []
