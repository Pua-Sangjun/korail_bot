"""Unit tests for alerts. Network and osascript are stubbed out."""
import pytest
import requests

from utils import notifier


@pytest.fixture
def calls(monkeypatch):
    calls = {"osascript": [], "ntfy": []}
    monkeypatch.setattr(notifier.subprocess, "run", lambda args, **kw: calls["osascript"].append(args))

    class Response:
        ok = True
        status_code = 200

    def fake_post(url, json, timeout):
        calls["ntfy"].append((url, json))
        return Response()

    monkeypatch.setattr(notifier.requests, "post", fake_post)
    return calls


def set_ntfy(monkeypatch, topic, server="https://ntfy.sh"):
    monkeypatch.setattr(notifier.settings, "NTFY_TOPIC", topic)
    monkeypatch.setattr(notifier.settings, "NTFY_SERVER", server)


def test_notify_skips_ntfy_when_topic_empty(monkeypatch, calls):
    set_ntfy(monkeypatch, None)
    notifier.notify("제목", "본문")
    assert len(calls["osascript"]) == 1
    assert calls["ntfy"] == []


def test_notify_pushes_to_ntfy_when_topic_set(monkeypatch, calls):
    set_ntfy(monkeypatch, "korail-abc123", server="https://ntfy.example.com/")
    notifier.notify("🎉 KTX 예약 성공", "본문", click_url="https://www.letskorail.com")
    url, payload = calls["ntfy"][0]
    assert url == "https://ntfy.example.com"
    assert payload["topic"] == "korail-abc123"
    assert payload["title"] == "🎉 KTX 예약 성공"
    assert payload["message"] == "본문"
    assert payload["click"] == "https://www.letskorail.com"


def test_ntfy_payload_has_no_click_without_url(monkeypatch, calls):
    set_ntfy(monkeypatch, "korail-abc123")
    notifier.notify("제목", "본문")
    assert "click" not in calls["ntfy"][0][1]


def test_macos_message_quotes_are_escaped(monkeypatch, calls):
    set_ntfy(monkeypatch, None)
    notifier.notify("제목", 'say "hi" \\ bye')
    script = calls["osascript"][0][2]
    assert 'display notification "say \\"hi\\" \\\\ bye" with title "제목"' in script


def test_send_ntfy_swallows_network_errors(monkeypatch):
    set_ntfy(monkeypatch, "korail-abc123")

    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(notifier.requests, "post", boom)
    assert notifier.send_ntfy("제목", "본문") is False
