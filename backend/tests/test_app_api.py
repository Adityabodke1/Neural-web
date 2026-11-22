"""App backend tests: chat, history, model meta, folder picker.

Run: .venv/bin/python -m pytest backend/tests/test_app_api.py -v
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import conversations as conv
from backend.app.api import chat as chat_api
from backend.app.main import app
from backend.app.providers import manager as pm

client = TestClient(app)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(conv, "CONV_DIR", tmp_path)
    monkeypatch.setattr(conv, "CONV_PATH", tmp_path / "conversations.jsonl")


def test_meta_filters_disabled_and_picks_active(monkeypatch):
    monkeypatch.setattr(pm, "list_models", lambda provider_id=None: [
        {"id": "groq/openai/gpt-oss-120b", "provider_id": "groq",
         "model_id": "openai/gpt-oss-120b", "enabled": 1, "is_default": 1},
        {"id": "off/model", "provider_id": "off", "model_id": "model",
         "enabled": 0, "is_default": 0},
    ])
    monkeypatch.setattr(pm, "resolve_model",
                        lambda spec="auto": {"id": "groq/openai/gpt-oss-120b"})
    r = client.get("/api/chat/meta")
    assert r.status_code == 200
    data = r.json()
    assert data["model"] == "groq/openai/gpt-oss-120b"
    assert [m["ref"] for m in data["models"]] == ["groq/openai/gpt-oss-120b"]
    assert data["models"][0]["provider"] == "groq"
    assert data["models"][0]["default"] is True


def test_meta_survives_empty_catalog(monkeypatch):
    monkeypatch.setattr(pm, "list_models", lambda provider_id=None: [])

    def boom(spec="auto"):
        raise ValueError("no models")

    monkeypatch.setattr(pm, "resolve_model", boom)
    r = client.get("/api/chat/meta")
    assert r.status_code == 200
    assert r.json() == {"model": "", "models": []}


def test_chat_logs_both_turns_and_returns_used_model(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    seen: dict = {}

    async def fake(messages, **kwargs):
        seen["messages"] = messages
        seen["model_spec"] = kwargs.get("model_spec")
        return {"content": "flat UI, no glow.", "model_ref": "groq/fake"}

    monkeypatch.setattr(chat_api.ai_provider, "chat_detailed", fake)
    r = client.post("/api/chat", json={"message": "describe the app",
                                       "workspace": "/tmp",
                                       "model": "groq/fake"})
    assert r.status_code == 200
    body = r.json()
    assert body == {"reply": "flat UI, no glow.", "model": "groq/fake"}
    assert seen["model_spec"] == "groq/fake"
    assert seen["messages"][0]["role"] == "system"
    assert seen["messages"][-1]["content"] == "describe the app"

    recs = [line for line in conv.CONV_PATH.read_text().splitlines() if line]
    import json
    parsed = [json.loads(x) for x in recs]
    assert [p["role"] for p in parsed] == ["user", "assistant"]
    assert all(p["session"] == "app" for p in parsed)
    assert parsed[1]["content"] == "flat UI, no glow."


def test_chat_model_defaults_to_auto(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    seen: dict = {}

    async def fake(messages, **kwargs):
        seen["model_spec"] = kwargs.get("model_spec")
        return {"content": "ok", "model_ref": "groq/x"}

    monkeypatch.setattr(chat_api.ai_provider, "chat_detailed", fake)
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    assert seen["model_spec"] == "auto"


def test_chat_rejects_blank_and_empty_reply(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422

    async def empty(messages, **kwargs):
        return {"content": "   ", "model_ref": "groq/x"}

    monkeypatch.setattr(chat_api.ai_provider, "chat_detailed", empty)
    r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 502
    assert "empty reply" in r.json()["detail"]
    assert not conv.CONV_PATH.exists()          # nothing logged on failure


def test_history_shows_only_app_thread(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    conv.log_turn("user", "from the shell", session="shell")
    conv.log_turn("assistant", "shell reply", session="shell")
    conv.log_turn("user", "from the app", session="app")
    conv.log_turn("assistant", "app reply", session="app")

    r = client.get("/api/chat/history?limit=50")
    assert r.status_code == 200
    data = r.json()
    assert [t["content"] for t in data["turns"]] == ["from the app", "app reply"]
    assert data["messages"] == 4                # global memory is still intact

    # memory preload still sees every surface
    assert [m["content"] for m in conv.recent_messages(4)] == [
        "from the shell", "shell reply", "from the app", "app reply"]


def test_new_chat_keeps_other_sessions(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    conv.log_turn("user", "shell memory", session="shell")
    conv.log_turn("user", "app memory", session="app")
    r = client.delete("/api/chat/history")
    assert r.status_code == 200
    assert r.json() == {"cleared": True, "removed": 1}
    assert [m["content"] for m in conv.recent_messages(4)] == ["shell memory"]
    assert client.get("/api/chat/history").json()["turns"] == []


def test_chat_gives_the_model_exact_time_and_dated_history(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    conv.log_turn("user", "yesterday I was fixing the build", session="app")
    conv.log_turn("assistant", "did it work?", session="app")
    seen: dict = {}

    async def fake(messages, **kwargs):
        seen["messages"] = messages
        return {"content": "ok", "model_ref": "groq/x"}

    monkeypatch.setattr(chat_api.ai_provider, "chat_detailed", fake)
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
    system = seen["messages"][0]["content"]
    assert "CURRENT DATE/TIME" in system
    assert "DATED MEMORY" in system
    recalled = [m["content"] for m in seen["messages"][1:-1]]
    assert recalled[0].startswith("[today, ")          # real stored timestamp
    assert recalled[0].endswith("yesterday I was fixing the build")
    assert recalled[1].startswith("[today, ")


def test_browse_lists_directories_and_parent(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "file.txt").write_text("x")
    r = client.get("/api/computer/browse", params={"path": str(tmp_path)})
    assert r.status_code == 200
    data = r.json()
    assert [d["name"] for d in data["dirs"]] == ["alpha", "beta"]
    assert data["path"] == str(tmp_path)
    assert data["parent"] == str(tmp_path.parent)


def test_browse_hidden_flag_and_errors(tmp_path):
    (tmp_path / ".config").mkdir()
    data = client.get("/api/computer/browse",
                      params={"path": str(tmp_path), "show_hidden": True}).json()
    assert [d["name"] for d in data["dirs"]] == [".config"]
    assert client.get("/api/computer/browse",
                      params={"path": str(tmp_path / "missing")}).status_code == 404
    assert client.get("/api/computer/browse",
                      params={"path": str(tmp_path / "file.txt")}).status_code == 404


def test_browse_mkdir(tmp_path):
    target = tmp_path / "new project"
    r = client.post("/api/computer/browse/mkdir", json={"path": str(target)})
    assert r.status_code == 200
    assert target.is_dir()
    # idempotent
    assert client.post("/api/computer/browse/mkdir",
                       json={"path": str(target)}).status_code == 200
