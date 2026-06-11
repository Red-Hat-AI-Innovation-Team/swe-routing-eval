"""Query the Cursor dashboard API for per-request token usage and costs.

The Cursor CLI doesn't report reasoning tokens, so costs for xhigh reasoning
runs are undercounted.  The dashboard API at
``cursor.com/api/dashboard/get-filtered-usage-events`` returns per-request
events with ``tokenUsage.totalCents`` — the actual token cost including
reasoning overhead.

Auth requires a ``WorkosCursorSessionToken`` cookie value, typically sourced
from ``$CURSOR_SESSION_TOKEN``.
"""

from __future__ import annotations

import json as _json
import logging
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_ENDPOINT = "https://cursor.com/api/dashboard/get-filtered-usage-events"


@dataclass
class UsageEvent:
    timestamp_ms: int
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    total_cents: float
    is_headless: bool


@dataclass
class SessionCost:
    total_cents: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    event_count: int


class CursorUsageClient:
    """Queries the Cursor dashboard for usage events."""

    def __init__(self, session_token: str) -> None:
        self._cookie = f"WorkosCursorSessionToken={session_token}"

    def get_events(self) -> list[UsageEvent]:
        req = urllib.request.Request(
            _ENDPOINT,
            headers={"Cookie": self._cookie},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read())
        except Exception:
            logger.warning("Failed to query Cursor usage API", exc_info=True)
            return []

        events: list[UsageEvent] = []
        for raw in data.get("usageEventsDisplay", []):
            usage = raw.get("tokenUsage", {})
            events.append(UsageEvent(
                timestamp_ms=int(raw.get("timestamp", 0)),
                model=raw.get("model", ""),
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                cache_read_tokens=usage.get("cacheReadTokens", 0),
                total_cents=usage.get("totalCents", 0.0),
                is_headless=bool(raw.get("isHeadless", False)),
            ))
        return events


def sum_events(
    events: list[UsageEvent],
    *,
    model: str,
    after_ms: int,
    before_ms: int,
) -> SessionCost:
    matched = [
        e for e in events
        if e.model == model
        and after_ms <= e.timestamp_ms <= before_ms
    ]
    return SessionCost(
        total_cents=sum(e.total_cents for e in matched),
        input_tokens=sum(e.input_tokens for e in matched),
        output_tokens=sum(e.output_tokens for e in matched),
        cache_read_tokens=sum(e.cache_read_tokens for e in matched),
        event_count=len(matched),
    )
