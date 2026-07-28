import json
from unittest.mock import Mock

from src import slack


def test_notify_posts_payload(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x")
    urlopen = Mock()
    monkeypatch.setattr("src.slack.urllib.request.urlopen", urlopen)

    slack.notify("[ops] all good")

    request = urlopen.call_args.args[0]
    assert request.full_url == "https://hooks.slack.com/services/x"
    assert json.loads(request.data) == {"text": "[ops] all good"}


def test_notify_skips_without_url(monkeypatch):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    urlopen = Mock()
    monkeypatch.setattr("src.slack.urllib.request.urlopen", urlopen)

    slack.notify("hello")

    urlopen.assert_not_called()


def test_notify_survives_failure(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x")
    monkeypatch.setattr("src.slack.urllib.request.urlopen", Mock(side_effect=OSError("down")))

    slack.notify("hello")
