"""Group duplicate incidents (same service + error signature, close in time).

Uses real datetime math -- comparing just HH:MM bites you the moment two
identical incidents land a day apart.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .config import ClusterConfig

_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M",
)


def parse_ts(ts: str) -> datetime:
    ts = ts.strip()
    for fmt in _FORMATS:
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Unparseable timestamp: {ts!r}")


def signature(incident: dict[str, Any], tokens: list[str]) -> str:
    text = (
        incident.get("short_description", "") + " " + incident.get("description", "")
    ).lower()
    # word boundaries so "500" doesn't match inside "1500"
    found = sorted({t for t in tokens if re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", text)})
    return incident.get("cmdb_ci", "?") + "|" + ",".join(found)


def cluster(incidents, cfg: ClusterConfig):
    groups: list[list[dict[str, Any]]] = []
    for inc in sorted(incidents, key=lambda i: i.get("opened_at", "")):
        sig = signature(inc, cfg.signature_tokens)
        try:
            when = parse_ts(inc.get("opened_at", ""))
        except ValueError:
            # bad timestamp: investigate on its own, never merge blind
            groups.append([inc])
            continue
        placed = False
        for group in groups:
            head = group[0]
            if signature(head, cfg.signature_tokens) != sig:
                continue
            try:
                head_when = parse_ts(head.get("opened_at", ""))
            except ValueError:
                continue
            delta_min = abs((when - head_when).total_seconds()) / 60.0
            if delta_min <= cfg.window_minutes:
                group.append(inc)
                placed = True
                break
        if not placed:
            groups.append([inc])
    return groups
