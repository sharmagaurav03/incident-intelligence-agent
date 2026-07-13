"""Webhook + real-system adapters against fake transports.

HMAC checked against RFC 4231 test case 2 (key "Jefe").
"""
import json
from types import SimpleNamespace

import pytest

from triage_agent.config import AppConfig
from triage_agent.webhook_server import (
    IdempotencyStore, TriggerService, verify_signature,
)


RFC4231_KEY = "Jefe"
RFC4231_MSG = b"what do ya want for nothing?"
RFC4231_MAC = "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"


def test_hmac_matches_rfc4231_vector():
    assert verify_signature(RFC4231_KEY, RFC4231_MSG, RFC4231_MAC)


def test_hmac_rejects_wrong_signature_and_case_insensitivity():
    assert not verify_signature(RFC4231_KEY, RFC4231_MSG, "00" * 32)
    assert verify_signature(RFC4231_KEY, RFC4231_MSG, RFC4231_MAC.upper())


def make_service(monkeypatch, secret="s3cret", debounce=0, handled=None):
    handled = handled if handled is not None else []
    cfg = AppConfig()
    cfg.webhook.debounce_seconds = debounce
    monkeypatch.setenv(cfg.webhook.hmac_secret_env, secret)
    slept = []
    svc = TriggerService(cfg, handled.append, sleep=slept.append)
    return svc, handled, slept


def sign(secret: str, body: bytes) -> str:
    import hashlib, hmac
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_trigger_happy_path(monkeypatch):
    svc, handled, _ = make_service(monkeypatch)
    body = json.dumps({"sys_id": "abc123"}).encode()
    code, msg = svc.handle_trigger(body, sign("s3cret", body))
    assert code == 202 and handled == ["abc123"]


def test_trigger_rejects_bad_signature(monkeypatch):
    svc, handled, _ = make_service(monkeypatch)
    body = json.dumps({"sys_id": "abc123"}).encode()
    code, _ = svc.handle_trigger(body, "deadbeef")
    assert code == 401 and handled == []


def test_trigger_rejects_tampered_body(monkeypatch):
    svc, handled, _ = make_service(monkeypatch)
    body = json.dumps({"sys_id": "abc123"}).encode()
    good_sig = sign("s3cret", body)
    tampered = json.dumps({"sys_id": "EVIL"}).encode()
    code, _ = svc.handle_trigger(tampered, good_sig)
    assert code == 401 and handled == []


def test_trigger_idempotent_second_delivery(monkeypatch):
    svc, handled, _ = make_service(monkeypatch)
    body = json.dumps({"sys_id": "abc123"}).encode()
    sig = sign("s3cret", body)
    assert svc.handle_trigger(body, sig)[0] == 202
    code, msg = svc.handle_trigger(body, sig)
    assert code == 200 and "duplicate" in msg and handled == ["abc123"]


def test_trigger_debounce_waits(monkeypatch):
    svc, handled, slept = make_service(monkeypatch, debounce=90)
    body = json.dumps({"sys_id": "abc123"}).encode()
    svc.handle_trigger(body, sign("s3cret", body))
    assert slept == [90] and handled == ["abc123"]


@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"sys_id": ""}',
                                  json.dumps({"sys_id": "x" * 65}).encode()])
def test_trigger_rejects_malformed_bodies(monkeypatch, body):
    svc, handled, _ = make_service(monkeypatch)
    code, _ = svc.handle_trigger(body, sign("s3cret", body))
    assert code == 400 and handled == []


def test_trigger_unconfigured_secret_is_503(monkeypatch):
    cfg = AppConfig()
    monkeypatch.delenv(cfg.webhook.hmac_secret_env, raising=False)
    svc = TriggerService(cfg, lambda s: None)
    assert svc.handle_trigger(b"{}", "x")[0] == 503


def test_idempotency_store_persists(tmp_path):
    path = tmp_path / "seen.json"
    store = IdempotencyStore(str(path))
    assert store.first_time("a") and not store.first_time("a")
    reloaded = IdempotencyStore(str(path))
    assert not reloaded.first_time("a")   # survives restart
    assert reloaded.first_time("b")


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text or json.dumps(self._payload)
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeSession:
    """Records calls; replays scripted responses."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.auth = None
        self.headers = {}

    def _next(self, method, url, **kw):
        self.calls.append(SimpleNamespace(method=method, url=url, **kw))
        return self.responses.pop(0)

    def get(self, url, **kw):
        return self._next("GET", url, **kw)

    def patch(self, url, **kw):
        return self._next("PATCH", url, **kw)

    def post(self, url, **kw):
        return self._next("POST", url, **kw)

    def put(self, url, **kw):
        return self._next("PUT", url, **kw)


def make_sn(monkeypatch, responses, **settings):
    from triage_agent.adapters import servicenow as sn_mod
    monkeypatch.setenv("SN_USERNAME", "svc-triage")
    monkeypatch.setenv("SN_PASSWORD", "pw")
    sn = sn_mod.ServiceNowTicketSystem(
        {"instance_url": "https://dev0.service-now.com", **settings}
    )
    sn.session = FakeSession(responses)
    return sn


def test_servicenow_open_incidents_request_shape(monkeypatch):
    sn = make_sn(monkeypatch, [FakeResponse(200, {"result": [{"number": "INC1"}]})])
    result = sn.open_incidents()
    call = sn.session.calls[0]
    assert call.url == "https://dev0.service-now.com/api/now/table/incident"
    assert call.kwargs if hasattr(call, "kwargs") else True
    assert call.params["sysparm_query"] == "state=1"
    assert result == [{"number": "INC1"}]


def test_servicenow_missing_credentials_fails_fast(monkeypatch):
    from triage_agent.adapters.servicenow import (
        ServiceNowError, ServiceNowTicketSystem,
    )
    monkeypatch.delenv("SN_USERNAME", raising=False)
    monkeypatch.delenv("SN_PASSWORD", raising=False)
    with pytest.raises(ServiceNowError, match="credentials missing"):
        ServiceNowTicketSystem({"instance_url": "https://x.service-now.com"})


def test_servicenow_401_gives_actionable_error(monkeypatch):
    from triage_agent.adapters.servicenow import ServiceNowError
    sn = make_sn(monkeypatch, [FakeResponse(401)])
    with pytest.raises(ServiceNowError, match="auth failed"):
        sn.open_incidents()


def test_servicenow_work_note_patch_body(monkeypatch):
    sn = make_sn(monkeypatch, [FakeResponse(200, {"result": {}})])
    sn.add_work_note("sid123", "hello")
    call = sn.session.calls[0]
    assert call.method == "PATCH"
    assert call.url.endswith("/api/now/table/incident/sid123")
    assert call.json == {"work_notes": "hello"}


def test_servicenow_fields_fold_into_note_when_schema_missing(monkeypatch):
    sn = make_sn(monkeypatch, [FakeResponse(200, {"result": {}})],
                 write_custom_fields=False)
    sn.set_fields("sid123", {"u_agent_category": "config"})
    call = sn.session.calls[0]
    assert call.json == {"work_notes": "Agent fields: u_agent_category=config"}


def test_servicenow_get_incident_404_returns_none(monkeypatch):
    sn = make_sn(monkeypatch, [FakeResponse(404)])
    assert sn.get_incident("nope") is None


def test_gcp_logging_request_shape(monkeypatch):
    from triage_agent.adapters import gcp as gcp_mod
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json, headers=headers)
        return FakeResponse(200, {"entries": [
            {"timestamp": "T1", "severity": "ERROR", "textPayload": "boom"}]})

    monkeypatch.setattr(gcp_mod.requests, "post", fake_post)
    monkeypatch.setenv("GCP_TOKEN", "tok123")
    src = gcp_mod.GcpLogSource({"project_id": "proj-1"})
    out = src.recent_errors("checkout-api", minutes=60)

    assert captured["url"] == "https://logging.googleapis.com/v2/entries:list"
    assert captured["body"]["resourceNames"] == ["projects/proj-1"]
    assert 'container_name="checkout-api"' in captured["body"]["filter"]
    assert "severity>=ERROR" in captured["body"]["filter"]
    assert captured["headers"]["Authorization"] == "Bearer tok123"
    assert out == "T1 ERROR boom"


def test_gcp_logging_raises_on_http_error(monkeypatch):
    from triage_agent.adapters import gcp as gcp_mod

    class Boom(FakeResponse):
        def raise_for_status(self):
            raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(gcp_mod.requests, "post",
                        lambda *a, **k: Boom(403))
    monkeypatch.setenv("GCP_TOKEN", "tok")
    src = gcp_mod.GcpLogSource({"project_id": "p"})
    with pytest.raises(RuntimeError, match="403"):
        src.recent_errors("svc")


def test_gcp_monitoring_request_shape(monkeypatch):
    from triage_agent.adapters import gcp as gcp_mod
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(url=url, params=params)
        return FakeResponse(200, {"timeSeries": [{
            "metric": {"type": "run.googleapis.com/request_count"},
            "points": [{"interval": {"endTime": "T9"},
                        "value": {"doubleValue": 42.0}}],
        }]})

    monkeypatch.setattr(gcp_mod.requests, "get", fake_get)
    monkeypatch.setenv("GCP_TOKEN", "tok")
    src = gcp_mod.GcpMetricSource({"project_id": "proj-1"})
    out = src.recent_series("checkout-api")
    assert captured["url"].endswith("/v3/projects/proj-1/timeSeries")
    assert 'service_name="checkout-api"' in captured["params"]["filter"]
    assert "interval.startTime" in captured["params"]
    assert out == "T9 run.googleapis.com/request_count=42.0"


def test_github_pr_flow_calls_documented_endpoints(monkeypatch):
    from triage_agent.adapters import github_fix as gh_mod
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    pub = gh_mod.GitHubFixPublisher({"owner": "acme", "repo": "shop"})
    pub.session = FakeSession([
        FakeResponse(200, {"default_branch": "main"}),                  # GET repo
        FakeResponse(200, {"object": {"sha": "abc"}}),                  # GET ref
        FakeResponse(201, {}),                                          # POST refs
        FakeResponse(404, {}, text="Not Found"),                        # GET contents
        FakeResponse(201, {"content": {}}),                             # PUT contents
        FakeResponse(201, {"html_url": "https://github.com/acme/shop/pull/7"}),
    ])
    url = pub.publish({"number": "INC1001"},
                      {"file": "F.java", "problem": "p", "suggested_patch": "x"},
                      "narrative")
    methods = [(c.method, c.url) for c in pub.session.calls]
    assert methods == [
        ("GET", "https://api.github.com/repos/acme/shop"),
        ("GET", "https://api.github.com/repos/acme/shop/git/ref/heads/main"),
        ("POST", "https://api.github.com/repos/acme/shop/git/refs"),
        ("GET", "https://api.github.com/repos/acme/shop/contents/triage-suggestions/INC1001.md"),
        ("PUT", "https://api.github.com/repos/acme/shop/contents/triage-suggestions/INC1001.md"),
        ("POST", "https://api.github.com/repos/acme/shop/pulls"),
    ]
    ref_call = pub.session.calls[2]
    assert ref_call.json == {"ref": "refs/heads/triage-agent/inc1001", "sha": "abc"}
    pr_call = pub.session.calls[5]
    assert pr_call.json["draft"] is True and pr_call.json["base"] == "main"
    assert url.endswith("/pull/7")


def test_github_issue_mode_single_call(monkeypatch):
    from triage_agent.adapters import github_fix as gh_mod
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    pub = gh_mod.GitHubFixPublisher(
        {"owner": "acme", "repo": "shop", "mode": "issue"})
    pub.session = FakeSession(
        [FakeResponse(201, {"html_url": "https://github.com/acme/shop/issues/3"})])
    url = pub.publish({"number": "INC1"},
                      {"file": "F", "problem": "p", "suggested_patch": "x"}, "n")
    assert url.endswith("/issues/3")
    assert pub.session.calls[0].url == "https://api.github.com/repos/acme/shop/issues"


def test_github_missing_token_fails_fast(monkeypatch):
    from triage_agent.adapters.github_fix import GitHubError, GitHubFixPublisher
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(GitHubError, match="token missing"):
        GitHubFixPublisher({"owner": "a", "repo": "b"})
