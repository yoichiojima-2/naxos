from datetime import UTC, datetime

from naxos import mcp


def test_result_shape():
    assert mcp.result("hello") == {"content": [{"type": "text", "text": "hello"}]}


def test_dumps_keeps_unicode():
    assert mcp.dumps({"city": "東京"}) == '{"city": "東京"}'


def test_dumps_serializes_datetimes():
    assert "2026-07-28" in mcp.dumps({"ts": datetime(2026, 7, 28, tzinfo=UTC)})
