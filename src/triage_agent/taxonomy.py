"""Fixed root-cause taxonomy + defensive parsing of LLM diagnosis output."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

TAXONOMY = [
    "infrastructure",
    "code-defect",
    "dependency-api-failure",
    "change-induced",
    "config",
    "data",
    "capacity",
    "unknown",
]

_REQUIRED = ("category", "confidence", "narrative", "evidence", "next_step")


@dataclass
class Diagnosis:
    category: str = "unknown"
    confidence: float = 0.0
    narrative: str = ""
    evidence: list[str] = field(default_factory=list)
    next_step: str = ""
    fix_hint: dict[str, Any] | None = None
    guardrail_notes: list[str] = field(default_factory=list)


def parse_diagnosis(raw: str | dict[str, Any]) -> Diagnosis:
    """Never raises. Anything off-menu degrades to unknown."""
    notes: list[str] = []
    if isinstance(raw, dict):
        data: Any = raw
    else:
        text = raw.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)  # model loves fences
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return Diagnosis(guardrail_notes=["unparseable-json"])
    if not isinstance(data, dict):
        return Diagnosis(guardrail_notes=["not-an-object"])

    d = Diagnosis()
    for key in _REQUIRED:
        if key not in data:
            notes.append(f"missing-field:{key}")

    d.category = str(data.get("category", "unknown"))
    try:
        d.confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        d.confidence = 0.0
        notes.append("bad-confidence-type")
    d.confidence = max(0.0, min(1.0, d.confidence))

    if d.category not in TAXONOMY:
        notes.append(f"invalid-category:{d.category}")
        d.category = "unknown"
        d.confidence = min(d.confidence, 0.3)

    d.narrative = str(data.get("narrative", ""))[:4000]
    ev = data.get("evidence", [])
    d.evidence = [str(e)[:500] for e in ev][:20] if isinstance(ev, list) else []
    d.next_step = str(data.get("next_step", ""))[:1000]

    hint = data.get("fix_hint")
    if isinstance(hint, dict) and {"file", "problem", "suggested_patch"} <= set(hint):
        d.fix_hint = {k: str(hint[k]) for k in ("file", "problem", "suggested_patch")}
    elif hint not in (None, {}, ""):
        notes.append("malformed-fix-hint")

    d.guardrail_notes = notes
    return d
