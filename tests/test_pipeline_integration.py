"""End to end over the file adapters."""
import json
import shutil
from pathlib import Path

import pytest

from triage_agent.config import AppConfig
from triage_agent.llm import MockLLM
from triage_agent.pipeline import TriagePipeline
from triage_agent.taxonomy import Diagnosis, parse_diagnosis
from triage_agent.adapters.file_adapters import (
    FileChangeSource, FileFixPublisher, FileHistoryStore, FileLogSource,
    FileMetricSource, FileTicketSystem,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def workspace(tmp_path):
    """Copy the sample data into an isolated tmp dir per test."""
    data = tmp_path / "data"
    shutil.copytree(REPO / "data", data)
    (tmp_path / "sample_service").mkdir()
    shutil.copytree(
        REPO / "sample_service", tmp_path / "sample_service", dirs_exist_ok=True
    )
    cfg = AppConfig(
        data_dir=str(data),
        output_dir=str(tmp_path / "out"),
        source_dir=str(tmp_path / "sample_service"),
    )
    return cfg


def build(cfg: AppConfig, llm=None) -> TriagePipeline:
    return TriagePipeline(
        cfg,
        FileTicketSystem(cfg.data_dir, cfg.output_dir),
        FileLogSource(cfg.data_dir),
        FileMetricSource(cfg.data_dir),
        FileChangeSource(cfg.data_dir),
        FileHistoryStore(cfg.data_dir),
        FileFixPublisher(cfg.output_dir, cfg.source_dir),
        llm or MockLLM(),
    )


def test_full_run_matches_expected_scenarios(workspace):
    pipeline = build(workspace)
    results = {r.incident_number: r for r in pipeline.run()}

    assert results["INC1001"].category == "code-defect"
    assert results["INC1001"].confidence == pytest.approx(0.9)
    assert results["INC1001"].duplicates == ["INC1002"]
    assert results["INC1001"].fix_ref and Path(results["INC1001"].fix_ref).exists()

    assert results["INC1003"].category == "dependency-api-failure"
    assert results["INC1003"].fix_ref is None

    after = json.loads(
        (Path(workspace.output_dir) / "incidents_after.json").read_text()
    )
    by_number = {i["number"]: i for i in after["incidents"]}
    assert by_number["INC1001"]["u_agent_category"] == "code-defect"
    assert by_number["INC1002"]["u_related_incident"] == "INC1001"
    assert any("duplicate" in n.lower() for n in by_number["INC1002"]["work_notes"])


def test_pii_never_reaches_outputs(workspace):
    pipeline = build(workspace)
    results = pipeline.run()
    assert any(r.redactions >= 2 for r in results)  # card + email in checkout log
    out_dir = Path(workspace.output_dir)
    dumped = "".join(p.read_text() for p in out_dir.rglob("*") if p.is_file())
    assert "4111111111111111" not in dumped.replace(" ", "").replace("-", "")
    assert "jane.doe@example.com" not in dumped


def test_sev1_assist_only(workspace):
    pipeline = build(workspace)
    inc = pipeline.tickets.open_incidents()[0]
    inc["severity"] = "1"
    result = pipeline.triage_cluster([inc])
    assert result.assist_only is True and result.category == ""
    stored = pipeline.tickets.get_incident(inc["sys_id"])
    assert "assist-only" in stored["work_notes"][-1]
    assert "u_agent_category" not in stored


def test_unknown_gets_note_but_no_fields(workspace):
    pipeline = build(workspace)
    inc = dict(pipeline.tickets.open_incidents()[0])
    inc["cmdb_ci"] = "no-such-service"   # kills log+metric evidence
    result = pipeline.triage_cluster([inc])
    assert result.category == "unknown"
    stored = pipeline.tickets.get_incident(inc["sys_id"])
    assert stored["work_notes"], "work note must still be written"
    assert "u_agent_category" not in stored


class FixedLLM(MockLLM):
    def __init__(self, payload):
        super().__init__()
        self._payload = payload

    def diagnose(self, incident, summaries, history) -> Diagnosis:
        return parse_diagnosis(self._payload)


HINT = {"file": "com/shop/checkout/GiftMessageMapper.java",
        "problem": "p", "suggested_patch": "x"}


@pytest.mark.parametrize(
    "category,confidence,hint,expect_fix",
    [
        ("code-defect", 0.80, HINT, True),    # all three conditions met
        ("code-defect", 0.79, HINT, False),   # confidence below gate
        ("config", 0.95, HINT, False),        # wrong category
        ("code-defect", 0.95, None, False),   # missing fix hint
    ],
)
def test_fix_gate_truth_table(workspace, category, confidence, hint, expect_fix):
    payload = {
        "category": category, "confidence": confidence, "narrative": "n",
        "evidence": [], "next_step": "s", "fix_hint": hint,
    }
    pipeline = build(workspace, llm=FixedLLM(payload))
    inc = pipeline.tickets.open_incidents()[0]
    result = pipeline.triage_cluster([inc])
    assert (result.fix_ref is not None) is expect_fix


def test_label_gate_below_half_forces_unknown(workspace):
    payload = {"category": "config", "confidence": 0.49, "narrative": "n",
               "evidence": [], "next_step": "s", "fix_hint": None}
    pipeline = build(workspace, llm=FixedLLM(payload))
    inc = pipeline.tickets.open_incidents()[0]
    result = pipeline.triage_cluster([inc])
    assert result.category == "unknown"


def test_label_gate_exactly_half_passes(workspace):
    payload = {"category": "config", "confidence": 0.5, "narrative": "n",
               "evidence": [], "next_step": "s", "fix_hint": None}
    pipeline = build(workspace, llm=FixedLLM(payload))
    inc = pipeline.tickets.open_incidents()[0]
    assert pipeline.triage_cluster([inc]).category == "config"


def test_cross_day_duplicates_investigated_separately(workspace):
    db_path = Path(workspace.data_dir) / "incidents.json"
    db = json.loads(db_path.read_text())
    twin = dict(db["incidents"][0])
    twin.update(number="INC9001", sys_id="sid9001",
                opened_at="2026-07-10 09:14:00")  # exactly 24h earlier
    db["incidents"].append(twin)
    db_path.write_text(json.dumps(db))

    pipeline = build(workspace)
    results = {r.incident_number for r in pipeline.run()}
    assert "INC9001" in results  # investigated on its own, not absorbed


def test_hostile_service_name_cannot_escape_data_dir(workspace):
    logs = FileLogSource(workspace.data_dir)
    assert logs.recent_errors("../../etc/passwd") == ""
