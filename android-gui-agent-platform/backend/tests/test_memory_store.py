"""TDD contract tests for the long-term memory module (plan.md Step 3).

Covers: MemoryStore md read/write, entry parsing, add/delete, dedup merge,
index rebuild, retriever keyword matching + budget truncation, explicit
memory detection and extractor degradation.

These tests are RED until ``app/memory`` is implemented.
"""

import pytest

from app.memory.extractor import (
    detect_explicit_memory,
    extract_turn_memory,
    parse_ops,
)
from app.memory.retriever import build_memory_context
from app.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# MemoryStore: files & entry format
# ---------------------------------------------------------------------------

def test_ensure_files_creates_three_md_files(memory_store, memory_dir):
    memory_store.ensure_files()
    assert (memory_dir / "MEMORY.md").exists()
    assert (memory_dir / "preferences.md").exists()
    assert (memory_dir / "paths.md").exists()


def test_add_preference_writes_dated_entry(memory_store, memory_dir):
    memory_store.ensure_files()
    memory_store.add("preference", "用户常用高德地图", date="2026-08-16")

    text = (memory_dir / "preferences.md").read_text(encoding="utf-8")
    assert "- [2026-08-16] 用户常用高德地图" in text
    assert memory_store.preferences() == ["用户常用高德地图"]


def test_add_path_groups_by_task_type_section(memory_store, memory_dir):
    memory_store.ensure_files()
    memory_store.add("path", "查航班先打开航旅纵横再点机票tab", task_type="查航班", date="2026-08-16")
    memory_store.add("path", "导航先打开高德再搜目的地", task_type="导航", date="2026-08-16")

    paths = memory_store.paths()
    assert paths["查航班"] == ["查航班先打开航旅纵横再点机票tab"]
    assert paths["导航"] == ["导航先打开高德再搜目的地"]

    text = (memory_dir / "paths.md").read_text(encoding="utf-8")
    assert "## 查航班" in text
    assert "## 导航" in text


def test_entries_survive_store_reopen(memory_store, memory_dir):
    memory_store.ensure_files()
    memory_store.add("preference", "用户常用高德地图")
    memory_store.add("path", "查航班先打开航旅纵横", task_type="查航班")

    reopened = MemoryStore(memory_dir)
    assert reopened.preferences() == ["用户常用高德地图"]
    assert reopened.paths()["查航班"] == ["查航班先打开航旅纵横"]


# ---------------------------------------------------------------------------
# MemoryStore: dedup / update / delete / clear
# ---------------------------------------------------------------------------

def test_add_same_preference_twice_does_not_accumulate(memory_store):
    memory_store.ensure_files()
    memory_store.add("preference", "用户常用高德地图", date="2026-08-16")
    memory_store.add("preference", "用户常用高德地图", date="2026-08-17")

    prefs = memory_store.preferences()
    assert prefs.count("用户常用高德地图") == 1


def test_update_replaces_matched_entry(memory_store):
    memory_store.ensure_files()
    memory_store.add("preference", "用户常用百度地图")

    assert memory_store.update("用户常用百度地图", "用户常用高德地图") is True
    assert memory_store.preferences() == ["用户常用高德地图"]


def test_update_without_match_returns_false(memory_store):
    memory_store.ensure_files()
    assert memory_store.update("不存在的条目", "新内容") is False


def test_delete_entry_by_index(memory_store):
    memory_store.ensure_files()
    memory_store.add("preference", "偏好一")
    memory_store.add("preference", "偏好二")

    assert memory_store.delete("preferences", 0) is True
    assert memory_store.preferences() == ["偏好二"]
    assert memory_store.delete("preferences", 5) is False


def test_clear_empties_all_files(memory_store):
    memory_store.ensure_files()
    memory_store.add("preference", "偏好一")
    memory_store.add("path", "路径一", task_type="导航")

    memory_store.clear()
    assert memory_store.preferences() == []
    assert memory_store.paths() == {}


# ---------------------------------------------------------------------------
# MemoryStore: index & apply ops
# ---------------------------------------------------------------------------

def test_rebuild_index_lists_entries(memory_store, memory_dir):
    memory_store.ensure_files()
    memory_store.add("preference", "用户常用高德地图")
    memory_store.add("path", "查航班先打开航旅纵横", task_type="查航班")

    memory_store.rebuild_index()

    index = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "用户常用高德地图" in index
    assert "查航班" in index


def test_apply_ops_add_and_update(memory_store):
    memory_store.ensure_files()
    memory_store.add("preference", "用户常用百度地图")

    ops = [
        {"op": "add", "kind": "preference", "content": "偏好深色模式"},
        {"op": "update", "match": "用户常用百度地图", "content": "用户常用高德地图"},
    ]
    memory_store.apply(ops)

    prefs = memory_store.preferences()
    assert "用户常用高德地图" in prefs
    assert "偏好深色模式" in prefs
    assert "用户常用百度地图" not in prefs


# ---------------------------------------------------------------------------
# Retriever: build_memory_context
# ---------------------------------------------------------------------------

def test_context_includes_all_preferences(memory_store):
    memory_store.ensure_files()
    memory_store.add("preference", "用户常用高德地图")
    memory_store.add("preference", "支付用微信")

    ctx = build_memory_context("随便做点什么", store=memory_store)
    assert "用户常用高德地图" in ctx
    assert "支付用微信" in ctx


def test_context_matches_path_by_keyword(memory_store):
    memory_store.ensure_files()
    memory_store.add("path", "导航先打开高德再搜目的地", task_type="导航")
    memory_store.add("path", "购物先打开淘宝搜索比价", task_type="购物")

    ctx = build_memory_context("导航去机场", store=memory_store)
    assert "导航先打开高德再搜目的地" in ctx
    assert "购物先打开淘宝搜索比价" not in ctx


def test_context_without_keyword_hit_uses_recent_sections(memory_store):
    memory_store.ensure_files()
    for task_type in ["办公", "学习", "娱乐", "阅读"]:
        memory_store.add("path", f"{task_type}的路径", task_type=task_type)

    ctx = build_memory_context("和任何小节都不相关的指令", store=memory_store)
    # 最近 3 个小节:学习/娱乐/阅读;最旧的「办公」不注入
    assert "学习的路径" in ctx
    assert "娱乐的路径" in ctx
    assert "阅读的路径" in ctx
    assert "办公的路径" not in ctx


def test_context_respects_budget_limit(memory_store):
    memory_store.ensure_files()
    long_entry = "很长的偏好" * 40  # ~200 chars per entry
    for i in range(50):
        memory_store.add("preference", f"{long_entry}#{i}")

    ctx = build_memory_context("任意指令", store=memory_store)
    assert len(ctx) <= 4000


# ---------------------------------------------------------------------------
# Extractor: parse_ops / degradation / explicit detection
# ---------------------------------------------------------------------------

def test_parse_ops_accepts_markdown_wrapped_json():
    raw = '```json\n{"add": [{"kind": "preference", "content": "用户常用高德地图"}], "update": []}\n```'
    ops = parse_ops(raw)
    assert {"op": "add", "kind": "preference", "content": "用户常用高德地图"} in ops


def test_parse_ops_maps_update_entries():
    raw = '{"add": [], "update": [{"match": "用户常用百度地图", "content": "用户常用高德地图"}]}'
    ops = parse_ops(raw)
    assert {"op": "update", "match": "用户常用百度地图", "content": "用户常用高德地图"} in ops


def test_parse_ops_invalid_input_returns_empty():
    assert parse_ops("") == []
    assert parse_ops("这不是 JSON") == []
    assert parse_ops("{}", ) == []


def test_extract_turn_memory_without_vlm_key_returns_empty():
    # conftest clears VLM_API_KEY; extraction must degrade silently, not raise.
    actions = [{"action": "CLICK", "parameters": {"point": [500, 500]}}]
    assert extract_turn_memory("打开高德地图", actions, "已完成", "") == []


@pytest.mark.parametrize("text", [
    "记住我用高德地图导航",
    "以后都用微信支付",
    "记住我喜欢深色模式",
])
def test_detect_explicit_memory_matches_remember_phrasing(text):
    memory = detect_explicit_memory(text)
    assert memory is not None
    assert len(memory) > 0


def test_detect_explicit_memory_ignores_normal_instructions():
    assert detect_explicit_memory("帮我打开设置") is None
    assert detect_explicit_memory("导航去机场") is None
