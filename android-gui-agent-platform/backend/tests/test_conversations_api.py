"""TDD contract tests for the conversation & memory REST API (plan.md Step 1/5).

Covers: conversation CRUD, message→turn flow with the MockGuiAgent (AC-08),
reply endpoint, legacy /api/tasks removal (AC-09), init_db legacy-table
drop, and the memory management endpoints (AC-06).

These tests are RED until the API is implemented.
"""

import pytest
from fastapi.testclient import TestClient

from tests.conftest import wait_for_sync


@pytest.fixture()
def client(db, memory_store, monkeypatch, captured_events):
    from app.main import app

    # memory endpoints must expose the store through an injectable getter
    from app.api import memory as memory_api

    monkeypatch.setattr(memory_api, "get_store", lambda: memory_store)
    return TestClient(app)


def create_conversation(client, **payload):
    resp = client.post("/api/conversations", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def get_detail(client, cid):
    resp = client.get(f"/api/conversations/{cid}")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

class TestConversationCrud:
    def test_create_conversation(self, client):
        body = create_conversation(client, title="测试会话")
        assert body["id"]
        assert body["status"] in ("idle", "active", "pending")
        assert "created_at" in body

    def test_list_conversations(self, client):
        create_conversation(client, title="会话一")
        create_conversation(client, title="会话二")
        items = client.get("/api/conversations").json()
        titles = [c.get("title") for c in items]
        assert "会话一" in titles and "会话二" in titles

    def test_detail_contains_messages_and_turns(self, client):
        cid = create_conversation(client, title="详情")["id"]
        detail = get_detail(client, cid)
        assert detail["id"] == cid
        assert detail["messages"] == []
        assert detail["turns"] == []

    def test_get_missing_conversation_404(self, client):
        assert client.get("/api/conversations/no-such-id").status_code == 404

    def test_delete_conversation(self, client):
        cid = create_conversation(client, title="待删除")["id"]
        assert client.delete(f"/api/conversations/{cid}").status_code == 200
        assert client.get(f"/api/conversations/{cid}").status_code == 404


# ---------------------------------------------------------------------------
# Message → turn flow with MockGuiAgent (AC-08: no device, mock agent)
# ---------------------------------------------------------------------------

class TestMessageFlow:
    def test_full_mock_flow_ask_reply_summary(self, client):
        cid = create_conversation(client, title="Mock 链路")["id"]

        # 1. send a message → turn starts
        resp = client.post(f"/api/conversations/{cid}/messages", json={"text": "帮我导航去机场"})
        assert resp.status_code == 200, resp.text

        # 2. mock agent asks at step 1 → ask card appears in the chat
        wait_for_sync(
            lambda: any(m.get("kind") == "ask" for m in get_detail(client, cid)["messages"]),
            message="ask card never appeared")

        # 3. reply to the ask → turn runs to completion with a summary
        resp = client.post(f"/api/conversations/{cid}/reply", json={"text": "用高德"})
        assert resp.status_code == 200, resp.text

        wait_for_sync(
            lambda: any(m.get("kind") == "summary" for m in get_detail(client, cid)["messages"]),
            message="turn summary never appeared")

        detail = get_detail(client, cid)
        assert detail["turns"], "turn must be listed in the detail"
        assert detail["turns"][0]["status"] == "finished"
        roles = {m["kind"] for m in detail["messages"]}
        assert {"text", "ask", "summary"} <= roles

    def test_reply_without_pending_ask_400(self, client):
        cid = create_conversation(client)["id"]
        resp = client.post(f"/api/conversations/{cid}/reply", json={"text": "没人问我"})
        assert resp.status_code == 400

    def test_stop_without_active_turn_400(self, client):
        cid = create_conversation(client)["id"]
        assert client.post(f"/api/conversations/{cid}/stop").status_code == 400


# ---------------------------------------------------------------------------
# Legacy removal (AC-09) & database rebuild
# ---------------------------------------------------------------------------

class TestLegacyRemoval:
    def test_tasks_api_gone(self, client):
        assert client.get("/api/tasks").status_code == 404
        assert client.post("/api/tasks", json={"instruction": "x"}).status_code == 404

    def test_init_db_drops_legacy_tables(self):
        from sqlalchemy import inspect, text

        from app.storage.db import engine as db_engine, init_db

        with db_engine.connect() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS tasks (id VARCHAR PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE IF NOT EXISTS task_steps (id VARCHAR PRIMARY KEY)"))
            conn.commit()

        init_db()

        names = inspect(db_engine).get_table_names()
        assert "tasks" not in names
        assert "task_steps" not in names
        assert "conversations" in names
        assert "messages" in names


# ---------------------------------------------------------------------------
# Memory endpoints (AC-06)
# ---------------------------------------------------------------------------

class TestMemoryApi:
    def test_get_memory_returns_parsed_entries(self, client, memory_store):
        memory_store.ensure_files()
        memory_store.add("preference", "用户常用高德地图")
        memory_store.add("path", "导航先打开高德", task_type="导航")

        body = client.get("/api/memory").json()
        assert "用户常用高德地图" in body["preferences"]
        assert body["paths"]["导航"] == ["导航先打开高德"]

    def test_delete_single_entry(self, client, memory_store):
        memory_store.ensure_files()
        memory_store.add("preference", "偏好一")
        memory_store.add("preference", "偏好二")

        resp = client.request("DELETE", "/api/memory/entries",
                              json={"file": "preferences", "index": 0})
        assert resp.status_code == 200, resp.text
        assert memory_store.preferences() == ["偏好二"]

    def test_clear_all_memory(self, client, memory_store):
        memory_store.ensure_files()
        memory_store.add("preference", "偏好一")
        memory_store.add("path", "路径", task_type="导航")

        assert client.delete("/api/memory").status_code == 200
        assert client.get("/api/memory").json()["preferences"] == []
