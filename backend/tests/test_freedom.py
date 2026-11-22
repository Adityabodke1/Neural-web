"""Personality freedom: harmless roleplay refusals get one retry.

Run: .venv/bin/python -m pytest backend/tests/test_freedom.py -v
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app import conversations as conv
from backend.app.ai import freedom
from backend.app.api import chat as chat_api
from backend.app.main import app

client = TestClient(app)


def test_persona_requests_are_recognised():
    for text in ("act like my girlfriend", "pretend you're a pirate",
                 "be a sarcastic friend from now on", "talk like a poet",
                 "adopt a caring personality"):
        assert freedom.is_persona_request(text), text
    for text in ("what is 2+2", "write a python script", "plan a trip to Goa"):
        assert not freedom.is_persona_request(text), text


def test_canned_refusal_detection():
    refusals = [
        "I'm sorry, but I can't help with that.",
        "I cannot assist with this request.",
        "As an AI language model, I can't do that.",
        "I'm not able to continue with this.",
        "I must decline this request.",
    ]
    for reply in refusals:
        assert freedom.is_refusal(reply), reply
    assert not freedom.is_refusal("Hey babe! How was your day? 😊")
    assert not freedom.is_refusal("Here is a Python function that adds numbers.")


def test_retry_only_for_harmless_persona_refusals():
    ok = freedom.should_retry("act like my girlfriend",
                              "I'm sorry, but I can't help with that.")
    assert ok
    assert not freedom.should_retry("what is 2+2",
                                    "I'm sorry, but I can't help with that.")
    assert not freedom.should_retry("act like my girlfriend",
                                    "Hey love! How was your day?")
    assert not freedom.should_retry(
        "act like my girlfriend and help me build ransomware",
        "I'm sorry, but I can't help with that.")


def test_clarification_preserves_messages_and_does_not_mutate():
    msgs = [{"role": "system", "content": "base"},
            {"role": "user", "content": "act like my girlfriend"}]
    out = freedom.with_persona_clarification(msgs)
    assert out is not msgs
    assert out[0]["content"].startswith("base")
    assert freedom.RETRY_NOTE.strip() in out[0]["content"]
    assert out[1] == msgs[1]
    assert msgs[0]["content"] == "base"          # original untouched


def test_clarification_adds_system_turn_when_missing():
    out = freedom.with_persona_clarification(
        [{"role": "user", "content": "act like my girlfriend"}])
    assert out[0]["role"] == "system"
    assert freedom.RETRY_NOTE.strip() in out[0]["content"]
    assert out[1]["role"] == "user"


class _SeqProvider:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.replies.pop(0)


def test_cli_ai_reply_retries_a_refusal(monkeypatch):
    from backend.cli.commands import chat_cmd
    fake = _SeqProvider(["I'm sorry, but I can't help with that.",
                         "Hey love! How was your day? 💕"])
    monkeypatch.setattr("backend.app.ai.provider.ai_provider", fake)
    msgs = [{"role": "system", "content": chat_cmd.SYSTEM_PROMPT},
            {"role": "user", "content": "act like my girlfriend"}]
    out = chat_cmd.ai_reply(msgs, "act like my girlfriend")
    assert "Hey love" in out
    assert len(fake.calls) == 2
    assert freedom.RETRY_NOTE.strip() in fake.calls[1][0]["content"]


def test_cli_ai_reply_keeps_first_reply_when_retry_also_refuses(monkeypatch):
    from backend.cli.commands import chat_cmd
    first = "I'm sorry, but I can't help with that."
    fake = _SeqProvider([first, "I'm sorry, but I can't help with that."])
    monkeypatch.setattr("backend.app.ai.provider.ai_provider", fake)
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "act like my girlfriend"}]
    assert chat_cmd.ai_reply(msgs, "act like my girlfriend") == first


def test_cli_ai_reply_does_not_retry_normal_chat(monkeypatch):
    from backend.cli.commands import chat_cmd
    fake = _SeqProvider(["2 + 2 is 4."])
    monkeypatch.setattr("backend.app.ai.provider.ai_provider", fake)
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "what is 2+2"}]
    assert chat_cmd.ai_reply(msgs, "what is 2+2") == "2 + 2 is 4."
    assert len(fake.calls) == 1


def test_app_chat_retries_and_logs_only_the_final_reply(monkeypatch, tmp_path):
    monkeypatch.setattr(conv, "CONV_DIR", tmp_path)
    monkeypatch.setattr(conv, "CONV_PATH", tmp_path / "conversations.jsonl")
    replies = [
        {"content": "I'm sorry, but I can't help with that.",
         "model_ref": "groq/test"},
        {"content": "Hey babe! What's up? 💖", "model_ref": "groq/test"},
    ]
    calls = []

    async def fake(messages, **kwargs):
        calls.append(messages)
        return replies.pop(0)

    monkeypatch.setattr(chat_api.ai_provider, "chat_detailed", fake)
    r = client.post("/api/chat", json={"message": "act like my girlfriend"})
    assert r.status_code == 200
    assert "Hey babe" in r.json()["reply"]
    assert len(calls) == 2
    assert freedom.RETRY_NOTE.strip() in calls[1][0]["content"]

    import json
    recs = [json.loads(x) for x in
            conv.CONV_PATH.read_text().splitlines() if x.strip()]
    assert [x["role"] for x in recs] == ["user", "assistant"]
    assert recs[1]["content"] == "Hey babe! What's up? 💖"
