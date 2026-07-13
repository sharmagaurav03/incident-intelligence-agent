"""The triage flow: cluster -> gates -> evidence -> scrub -> summarize ->
diagnose -> write back -> maybe suggest a fix."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .clustering import cluster
from .scrubber import scrub
from .taxonomy import Diagnosis

@dataclass
class TriageResult:
    incident_number: str
    duplicates: list[str]
    category: str
    confidence: float
    assist_only: bool = False
    fix_ref: str | None = None
    redactions: int = 0
    elapsed_s: float = 0.0
    guardrail_notes: list[str] = field(default_factory=list)

class TriagePipeline:
    def __init__(self, cfg, tickets, logs, metrics, changes, history, fixes, llm):
        self.cfg = cfg
        self.tickets = tickets
        self.logs = logs
        self.metrics = metrics
        self.changes = changes
        self.history = history
        self.fixes = fixes
        self.llm = llm

    def run(self, only_number=None):
        incidents = self.tickets.open_incidents()
        if only_number:
            incidents = [i for i in incidents if i.get("number") == only_number]
        return [self.triage_cluster(g) for g in cluster(incidents, self.cfg.cluster)]

    def triage_cluster(self, group) -> TriageResult:
        start = time.time()
        primary, siblings = group[0], group[1:]
        number = primary.get("number", "?")

        # sev1: humans own it, we just offer to help
        if self.cfg.gates.sev1_assist_only and str(primary.get("severity")) == "1":
            self.tickets.add_work_note(
                primary["sys_id"],
                "[triage-agent] Severity-1 incident: assist-only mode. "
                "Evidence gathering available on request; no automated "
                "classification will be applied.",
            )
            return TriageResult(
                incident_number=number,
                duplicates=[s.get("number", "?") for s in siblings],
                category="",
                confidence=0.0,
                assist_only=True,
                elapsed_s=round(time.time() - start, 2),
            )

        service = primary.get("cmdb_ci", "")

        raw = {
            "logs": self.logs.recent_errors(service),
            "metrics": self.metrics.recent_series(service),
            "changes": self.changes.recent_changes(service),
        }
        # scrub everything, not just logs -- deploy summaries leak emails too
        redactions = 0
        cleaned = {}
        for key, text in raw.items():
            r = scrub(text)
            cleaned[key] = r.text
            redactions += r.total

        summaries = {k: self.llm.summarize(k, t) for k, t in cleaned.items() if t}
        past = self.history.similar(
            primary.get("short_description", "") + " " + primary.get("description", ""),
            service,
        )
        diagnosis: Diagnosis = self.llm.diagnose(primary, summaries, past)

        # low confidence never gets a label
        if diagnosis.confidence < self.cfg.gates.label_min_confidence:
            if diagnosis.category != "unknown":
                diagnosis.guardrail_notes.append(
                    f"low-confidence-relabel:{diagnosis.category}"
                )
            diagnosis.category = "unknown"

        self.tickets.add_work_note(
            primary["sys_id"],
            self._render_note(diagnosis, summaries, redactions, siblings),
        )

        if diagnosis.category != "unknown":
            self.tickets.set_fields(
                primary["sys_id"],
                {
                    "u_agent_category": diagnosis.category,
                    "u_agent_confidence": f"{diagnosis.confidence:.2f}",
                },
            )

        for sib in siblings:
            self.tickets.add_work_note(
                sib["sys_id"],
                f"[triage-agent] Probable duplicate of {number} "
                f"(same service + error signature within "
                f"{self.cfg.cluster.window_minutes} min). Investigation "
                f"consolidated there.",
            )
            self.tickets.set_fields(sib["sys_id"], {"u_related_incident": number})

        # fix suggestions: right category, confident enough, concrete hint
        fix_ref = None
        if (
            diagnosis.fix_hint is not None
            and diagnosis.category == self.cfg.gates.fix_category
            and diagnosis.confidence >= self.cfg.gates.fix_min_confidence
        ):
            fix_ref = self.fixes.publish(primary, diagnosis.fix_hint, diagnosis.narrative)
            self.tickets.add_work_note(
                primary["sys_id"], f"[triage-agent] Fix suggestion: {fix_ref}"
            )

        return TriageResult(
            incident_number=number,
            duplicates=[s.get("number", "?") for s in siblings],
            category=diagnosis.category,
            confidence=diagnosis.confidence,
            fix_ref=fix_ref,
            redactions=redactions,
            elapsed_s=round(time.time() - start, 2),
            guardrail_notes=diagnosis.guardrail_notes,
        )

    @staticmethod
    def _render_note(diagnosis, summaries, redactions, siblings) -> str:
        lines = [
            "[triage-agent] Automated triage",
            f"Category: {diagnosis.category} (confidence {diagnosis.confidence:.2f})",
            f"Narrative: {diagnosis.narrative}",
            f"Evidence sources: {sorted(summaries)}",
            f"Evidence refs: {diagnosis.evidence}",
            f"Suggested next step: {diagnosis.next_step}",
            f"PII redactions applied to evidence: {redactions}",
        ]
        if siblings:
            lines.append(
                "Duplicates consolidated: "
                + ", ".join(s.get("number", "?") for s in siblings)
            )
        if diagnosis.guardrail_notes:
            lines.append(f"Guardrail notes: {diagnosis.guardrail_notes}")
        if diagnosis.category == "unknown":
            lines.append("Routed to human triage (low confidence or no evidence).")
        return "\n".join(lines)
