"""Config layering + both LLM clients."""
import json

import pytest

from triage_agent.config import AppConfig, load_config
from triage_agent.llm import MockLLM, RealLLM, make_llm


def test_defaults_run_with_no_files_or_env():
    cfg = load_config(None)
    assert cfg.llm.mode == "mock" and cfg.gates.fix_min_confidence == 0.8


def test_yaml_file_applies(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("llm:\n  mode: real\ngates:\n  fix_min_confidence: 0.9\n")
    cfg = load_config(p)
    assert cfg.llm.mode == "real" and cfg.gates.fix_min_confidence == 0.9


def test_unknown_yaml_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("gates:\n  totally_bogus: 1\n")
    with pytest.raises(ValueError, match="totally_bogus"):
        load_config(p)


def test_missing_config_file_raises():
    with pytest.raises(FileNotFoundError):
        load_config("/no/such/file.yaml")


def test_env_overrides_with_type_coercion(monkeypatch):
    monkeypatch.setenv("TRIAGE__llm__mode", "real")
    monkeypatch.setenv("TRIAGE__cluster__window_minutes", "45")
    monkeypatch.setenv("TRIAGE__gates__fix_min_confidence", "0.85")
    monkeypatch.setenv("TRIAGE__gates__sev1_assist_only", "false")
    monkeypatch.setenv("TRIAGE__cluster__signature_tokens", "500, timeout ,oom")
    cfg = load_config(None)
    assert cfg.llm.mode == "real"
    assert cfg.cluster.window_minutes == 45
    assert cfg.gates.fix_min_confidence == 0.85
    assert cfg.gates.sev1_assist_only is False
    assert cfg.cluster.signature_tokens == ["500", "timeout", "oom"]


def test_mock_llm_is_deterministic_and_guardrailed():
    llm = MockLLM()
    summaries = {"logs": "NullPointerException at X", "changes": "DEPLOY v2"}
    d1 = llm.diagnose({}, summaries, [])
    d2 = llm.diagnose({}, summaries, [])
    assert d1.category == d2.category == "code-defect"
    assert d1.fix_hint is not None
    # unknown path is guardrail-clean too
    d3 = llm.diagnose({}, {"logs": "nothing interesting"}, [])
    assert d3.category == "unknown" and d3.confidence == 0.3


def test_make_llm_factory_selects_mode(monkeypatch):
    cfg = AppConfig().llm
    assert isinstance(make_llm(cfg), MockLLM)
    cfg.mode = "real"
    monkeypatch.setenv(cfg.api_key_env, "sk-ant-test")
    assert isinstance(make_llm(cfg), RealLLM)


def test_real_llm_requires_key(monkeypatch):
    cfg = AppConfig().llm
    cfg.mode = "real"
    monkeypatch.delenv(cfg.api_key_env, raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        RealLLM(cfg)


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def _ok_payload(text):
    return {"usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "text", "text": text}]}


def test_real_llm_request_shape_and_parsing(monkeypatch):
    import requests as requests_mod
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, body=json)
        return FakeResp(200, _ok_payload(json_dump_valid()))

    def json_dump_valid():
        return json.dumps({"category": "config", "confidence": 0.7,
                           "narrative": "n", "evidence": [], "next_step": "s",
                           "fix_hint": None})

    monkeypatch.setattr(requests_mod, "post", fake_post)
    cfg = AppConfig().llm
    cfg.mode = "real"
    monkeypatch.setenv(cfg.api_key_env, "sk-ant-test")
    llm = RealLLM(cfg)
    d = llm.diagnose({"number": "INC1"}, {"logs": "x"}, [])

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["body"]["model"] == cfg.diagnosis_model
    assert captured["body"]["max_tokens"] == cfg.diagnosis_max_tokens
    assert d.category == "config" and d.confidence == 0.7
    assert llm.usage.calls == 1 and llm.usage.input_tokens == 10


def test_real_llm_retries_on_429_then_succeeds(monkeypatch):
    import requests as requests_mod
    attempts = []

    def fake_post(url, **kw):
        attempts.append(1)
        if len(attempts) == 1:
            return FakeResp(429)
        return FakeResp(200, _ok_payload("summary text"))

    monkeypatch.setattr(requests_mod, "post", fake_post)
    import triage_agent.llm as llm_mod
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)  # fast test
    cfg = AppConfig().llm
    cfg.mode = "real"
    monkeypatch.setenv(cfg.api_key_env, "sk-ant-test")
    out = RealLLM(cfg).summarize("logs", "some text")
    assert out == "summary text" and len(attempts) == 2


def test_real_llm_diagnose_degrades_to_unknown_when_api_down(monkeypatch):
    import requests as requests_mod
    monkeypatch.setattr(requests_mod, "post", lambda *a, **k: FakeResp(503))
    import triage_agent.llm as llm_mod
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    cfg = AppConfig().llm
    cfg.mode = "real"
    cfg.max_retries = 1
    monkeypatch.setenv(cfg.api_key_env, "sk-ant-test")
    d = RealLLM(cfg).diagnose({"number": "INC1"}, {}, [])
    assert d.category == "unknown" and "llm-unavailable" in d.guardrail_notes


def test_real_llm_summarize_skips_empty_input(monkeypatch):
    cfg = AppConfig().llm
    cfg.mode = "real"
    monkeypatch.setenv(cfg.api_key_env, "sk-ant-test")
    assert RealLLM(cfg).summarize("logs", "   ") == "(no data)"  # no network call
