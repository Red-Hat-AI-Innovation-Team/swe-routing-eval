"""Tests for cursor_usage module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from swe_routing_eval.cursor_usage import (
    CursorUsageClient,
    SessionCost,
    UsageEvent,
    sum_events,
)


def _make_api_response(events: list[dict]) -> bytes:
    return json.dumps({"usageEventsDisplay": events}).encode()


def _raw_event(
    *,
    timestamp: int = 1000,
    model: str = "gpt-5.4-xhigh",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    total_cents: float = 1.5,
    is_headless: bool = True,
) -> dict:
    return {
        "timestamp": timestamp,
        "model": model,
        "isHeadless": is_headless,
        "tokenUsage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "cacheReadTokens": cache_read_tokens,
            "totalCents": total_cents,
        },
    }


class TestSumEvents:
    def test_filters_by_model_and_time(self) -> None:
        events = [
            UsageEvent(1000, "gpt-5.4-xhigh", 100, 50, 0, 1.5, True),
            UsageEvent(2000, "gpt-5.4-xhigh", 200, 80, 10, 3.0, True),
            UsageEvent(1500, "gpt-5.3-codex-xhigh", 50, 20, 0, 0.5, True),
            UsageEvent(3000, "gpt-5.4-xhigh", 100, 50, 0, 1.5, True),
        ]
        result = sum_events(events, model="gpt-5.4-xhigh", after_ms=900, before_ms=2500)
        assert result == SessionCost(
            total_cents=4.5,
            input_tokens=300,
            output_tokens=130,
            cache_read_tokens=10,
            event_count=2,
        )

    def test_empty_when_no_match(self) -> None:
        events = [
            UsageEvent(1000, "gpt-5.4-xhigh", 100, 50, 0, 1.5, True),
        ]
        result = sum_events(events, model="gpt-5.3-codex-xhigh", after_ms=0, before_ms=5000)
        assert result.event_count == 0
        assert result.total_cents == 0.0

    def test_empty_input(self) -> None:
        result = sum_events([], model="gpt-5.4-xhigh", after_ms=0, before_ms=5000)
        assert result == SessionCost(0.0, 0, 0, 0, 0)

    def test_inclusive_boundaries(self) -> None:
        events = [
            UsageEvent(1000, "gpt-5.4-xhigh", 100, 50, 0, 1.5, True),
            UsageEvent(2000, "gpt-5.4-xhigh", 100, 50, 0, 1.5, True),
        ]
        result = sum_events(events, model="gpt-5.4-xhigh", after_ms=1000, before_ms=2000)
        assert result.event_count == 2


class TestCursorUsageClient:
    def test_get_events_parses_response(self) -> None:
        body = _make_api_response([
            _raw_event(timestamp=1000, model="gpt-5.4-xhigh", total_cents=2.5),
            _raw_event(timestamp=2000, model="gpt-5.3-codex-xhigh", total_cents=1.0),
        ])
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("swe_routing_eval.cursor_usage.urllib.request.urlopen", return_value=mock_resp):
            client = CursorUsageClient("fake-token")
            events = client.get_events()

        assert len(events) == 2
        assert events[0].model == "gpt-5.4-xhigh"
        assert events[0].total_cents == 2.5
        assert events[1].model == "gpt-5.3-codex-xhigh"

    def test_get_events_returns_empty_on_error(self) -> None:
        with patch(
            "swe_routing_eval.cursor_usage.urllib.request.urlopen",
            side_effect=Exception("connection refused"),
        ):
            client = CursorUsageClient("fake-token")
            events = client.get_events()

        assert events == []

    def test_cookie_header(self) -> None:
        body = _make_api_response([])
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch(
            "swe_routing_eval.cursor_usage.urllib.request.urlopen",
            return_value=mock_resp,
        ) as mock_open:
            client = CursorUsageClient("my-session-token")
            client.get_events()

        req = mock_open.call_args[0][0]
        assert req.get_header("Cookie") == "WorkosCursorSessionToken=my-session-token"
