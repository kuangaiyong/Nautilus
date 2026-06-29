"""
Tests for services.heartbeat_monitor (HeartbeatMonitor.scan).

Drives a real OpenClawProtocol instance with controlled heartbeat timestamps
and a fixed `now` to assert the inactivity bucketing — no mocks, no DB. Backs
the restored module consumed by /dashboard/heartbeat and the scheduler.
"""
from datetime import datetime, timedelta

from services.openclaw_protocol import OpenClawProtocol
from services.heartbeat_monitor import (
    HeartbeatMonitor,
    INACTIVE_AFTER,
    COMA_AFTER,
    DEAD_AFTER,
)


def _monitor_with(now, ages_seconds):
    """Build a monitor whose protocol has one agent per given age (seconds)."""
    proto = OpenClawProtocol()
    for agent_id, age in ages_seconds.items():
        proto._heartbeats[agent_id] = now - timedelta(seconds=age)
    return HeartbeatMonitor(protocol=proto)


def test_buckets_by_inactivity_band():
    now = datetime(2026, 6, 25, 12, 0, 0)
    mon = _monitor_with(now, {
        1: 10,                 # fresh -> online
        2: INACTIVE_AFTER + 1,  # -> inactive
        3: COMA_AFTER + 1,      # -> coma
        4: DEAD_AFTER + 1,      # -> dead
    })
    s = mon.scan(now=now)
    assert s["online"] == [1]
    assert s["to_inactive"] == [2]
    assert s["to_coma"] == [3]
    assert s["to_dead"] == [4]
    assert s["total_known"] == 4


def test_boundaries_are_inclusive_lower_bound():
    now = datetime(2026, 6, 25, 12, 0, 0)
    mon = _monitor_with(now, {
        1: INACTIVE_AFTER,  # exactly at threshold -> inactive
        2: COMA_AFTER,      # exactly at threshold -> coma
        3: DEAD_AFTER,      # exactly at threshold -> dead
    })
    s = mon.scan(now=now)
    assert s["to_inactive"] == [1]
    assert s["to_coma"] == [2]
    assert s["to_dead"] == [3]


def test_empty_when_no_heartbeats():
    now = datetime(2026, 6, 25, 12, 0, 0)
    mon = _monitor_with(now, {})
    s = mon.scan(now=now)
    assert s["total_known"] == 0
    assert s["online"] == []
    assert s["to_inactive"] == []
    assert s["to_coma"] == []
    assert s["to_dead"] == []
