"""
Agent heartbeat monitor.

Classifies OpenClaw agents by how long ago they last sent a heartbeat. The
liveness source of truth is OpenClawProtocol._heartbeats (agent_id -> last
heartbeat datetime), populated by POST /api/openclaw/heartbeat. Thresholds are
derived from the protocol's existing offline timeout so there is a single tuning
knob; the monitor only reports (it does not mutate agent state).

Consumed by:
- api/dashboard.py GET /dashboard/heartbeat -> scan()
- services/scheduler.py periodic _heartbeat_scan (every 60s)
"""
from datetime import datetime
from typing import Dict, List, Optional

from services.openclaw_protocol import (
    HEARTBEAT_TIMEOUT_SECONDS,
    OpenClawProtocol,
    get_openclaw_protocol,
)

# Escalation bands, in seconds, anchored on the protocol's offline timeout.
INACTIVE_AFTER = HEARTBEAT_TIMEOUT_SECONDS        # 15 min  -> inactive
COMA_AFTER = HEARTBEAT_TIMEOUT_SECONDS * 4        # 1 h     -> coma
DEAD_AFTER = HEARTBEAT_TIMEOUT_SECONDS * 24       # 6 h     -> dead


class HeartbeatMonitor:
    """Reads agent heartbeats and buckets them by inactivity."""

    def __init__(self, protocol: Optional[OpenClawProtocol] = None) -> None:
        self._protocol = protocol or get_openclaw_protocol()

    def scan(self, now: Optional[datetime] = None) -> Dict:
        now = now or datetime.utcnow()
        heartbeats = self._protocol._heartbeats

        online: List[int] = []
        to_inactive: List[int] = []
        to_coma: List[int] = []
        to_dead: List[int] = []

        for agent_id, last in heartbeats.items():
            elapsed = (now - last).total_seconds()
            if elapsed >= DEAD_AFTER:
                to_dead.append(agent_id)
            elif elapsed >= COMA_AFTER:
                to_coma.append(agent_id)
            elif elapsed >= INACTIVE_AFTER:
                to_inactive.append(agent_id)
            else:
                online.append(agent_id)

        return {
            "scanned_at": now.isoformat(),
            "total_known": len(heartbeats),
            "online": online,
            "to_inactive": to_inactive,
            "to_coma": to_coma,
            "to_dead": to_dead,
        }


_monitor: Optional[HeartbeatMonitor] = None


def get_monitor() -> HeartbeatMonitor:
    """Return the singleton heartbeat monitor."""
    global _monitor
    if _monitor is None:
        _monitor = HeartbeatMonitor()
    return _monitor
