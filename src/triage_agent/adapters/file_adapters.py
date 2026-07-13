"""File-backed adapters -- the whole agent runs offline against ./data."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from .base import (
    ChangeSource, FixPublisher, HistoryStore, LogSource, MetricSource,
    TicketSystem,
)


class FileTicketSystem(TicketSystem):
    def __init__(self, data_dir: str, output_dir: str):
        self._src = Path(data_dir) / "incidents.json"
        self._out = Path(output_dir) / "incidents_after.json"
        self._out.parent.mkdir(parents=True, exist_ok=True)
        self.db = json.loads(self._src.read_text())

    def open_incidents(self):
        return [i for i in self.db["incidents"] if i.get("state") == "New"]

    def get_incident(self, sys_id):
        return next((i for i in self.db["incidents"] if i.get("sys_id") == sys_id), None)

    def add_work_note(self, sys_id, note):
        inc = self.get_incident(sys_id)
        if inc is None:
            raise KeyError(f"No incident with sys_id={sys_id}")
        inc.setdefault("work_notes", []).append(note)
        self._flush()

    def set_fields(self, sys_id, fields):
        inc = self.get_incident(sys_id)
        if inc is None:
            raise KeyError(f"No incident with sys_id={sys_id}")
        inc.update(fields)
        self._flush()

    def _flush(self):
        self._out.write_text(json.dumps(self.db, indent=2))


def _safe(name: str) -> str:
    # ticket-supplied service names go into file paths; don't let them wander
    return re.sub(r"[^A-Za-z0-9._-]", "", name)


class FileLogSource(LogSource):
    def __init__(self, data_dir: str):
        self._dir = Path(data_dir) / "logs"

    def recent_errors(self, service, minutes=60):
        path = self._dir / f"{_safe(service)}.log"
        return path.read_text() if path.exists() else ""


class FileMetricSource(MetricSource):
    def __init__(self, data_dir: str):
        self._dir = Path(data_dir) / "metrics"

    def recent_series(self, service, minutes=60):
        path = self._dir / f"{_safe(service)}.csv"
        if not path.exists():
            return ""
        rows = list(csv.DictReader(path.read_text().splitlines()))
        return "\n".join(
            f"{r.get('ts','?')} error_rate={r.get('error_rate','?')} "
            f"p95_ms={r.get('p95_ms','?')} requests={r.get('requests','?')}"
            for r in rows
        )


class FileChangeSource(ChangeSource):
    def __init__(self, data_dir: str):
        self._dir = Path(data_dir)

    def recent_changes(self, service, minutes=240):
        lines = []
        deploys = self._dir / "deploys.json"
        flags = self._dir / "flags.json"
        if deploys.exists():
            for d in json.loads(deploys.read_text()).get("deploys", []):
                if d.get("service") == service:
                    lines.append(
                        f"DEPLOY {d.get('ts','?')} {d.get('version','?')} "
                        f"by {d.get('author','?')}: {d.get('summary','')}"
                    )
        if flags.exists():
            for f in json.loads(flags.read_text()).get("flag_changes", []):
                if f.get("service") == service:
                    lines.append(
                        f"FLAG {f.get('ts','?')} {f.get('flag','?')} -> "
                        f"{f.get('state','?')} ({f.get('rollout','')})"
                    )
        return "\n".join(lines)


class FileHistoryStore(HistoryStore):
    # dumb token overlap. good enough at this scale, swap for pgvector later
    def __init__(self, data_dir: str):
        path = Path(data_dir) / "history.json"
        self._resolved = (
            json.loads(path.read_text()).get("resolved", []) if path.exists() else []
        )

    def similar(self, text, service, k=3):
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        scored = []
        for item in self._resolved:
            if item.get("service") != service:
                continue
            overlap = len(
                words & set(re.findall(r"[a-z0-9]+", item.get("summary", "").lower()))
            )
            if overlap:
                scored.append((overlap, item))
        scored.sort(key=lambda pair: -pair[0])
        return [{**item, "similarity": score} for score, item in scored[:k]]


class FileFixPublisher(FixPublisher):
    def __init__(self, output_dir: str, source_dir: str):
        self._out = Path(output_dir)
        self._src = Path(source_dir)
        self._out.mkdir(parents=True, exist_ok=True)

    def publish(self, incident, fix_hint, narrative):
        number = incident.get("number", "INC")
        safe_file = re.sub(r"[^A-Za-z0-9./_-]", "", fix_hint["file"]).lstrip("/")
        if ".." in safe_file.split("/"):
            safe_file = safe_file.replace("..", "")
        source_path = self._src / safe_file
        snippet = source_path.read_text() if source_path.exists() else "(source not found)"
        body = (
            f"# Fix suggestion for {number}\n\n"
            f"**File:** `{fix_hint['file']}`\n\n"
            f"**Problem:** {fix_hint['problem']}\n\n"
            f"**Suggested patch (illustrative, needs human review):**\n\n"
            f"```\n{fix_hint['suggested_patch']}\n```\n\n"
            f"**Diagnosis narrative:** {narrative}\n\n"
            f"**Current source:**\n\n```java\n{snippet}\n```\n"
        )
        out = self._out / f"fix_{number}.md"
        out.write_text(body)
        return str(out)
