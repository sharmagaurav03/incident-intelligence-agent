"""Clustering tests, incl. the cross-day case that bit the old HH:MM parsing."""
import pytest

from triage_agent.clustering import cluster, parse_ts, signature
from triage_agent.config import ClusterConfig

CFG = ClusterConfig()


def inc(number, service, desc, opened):
    return {
        "number": number,
        "sys_id": f"sid-{number}",
        "cmdb_ci": service,
        "short_description": desc,
        "description": desc,
        "opened_at": opened,
    }


def test_same_signature_within_window_clusters():
    a = inc("A", "checkout-api", "500 errors on orders", "2026-07-11 09:14:00")
    # identical error-token set (500, orders) => same signature by design
    b = inc("B", "checkout-api", "more 500 errors on orders", "2026-07-11 09:19:00")
    groups = cluster([a, b], CFG)
    assert len(groups) == 1 and len(groups[0]) == 2


def test_three_way_storm_clusters_to_one_group():
    incidents = [
        inc(f"I{n}", "checkout-api", "500 on orders", f"2026-07-11 09:{10+n}:00")
        for n in range(3)
    ]
    groups = cluster(incidents, CFG)
    assert [len(g) for g in groups] == [3]


def test_cross_day_same_time_does_NOT_cluster():
    """Regression: demo parsed only HH:MM so these would wrongly merge."""
    a = inc("A", "checkout-api", "500 errors on orders", "2026-07-10 09:14:00")
    b = inc("B", "checkout-api", "500 errors on orders", "2026-07-11 09:14:00")
    groups = cluster([a, b], CFG)
    assert len(groups) == 2


def test_different_service_never_clusters():
    a = inc("A", "checkout-api", "500 errors", "2026-07-11 09:14:00")
    b = inc("B", "payments-api", "500 errors", "2026-07-11 09:15:00")
    assert len(cluster([a, b], CFG)) == 2


def test_different_error_signature_never_clusters():
    a = inc("A", "checkout-api", "500 errors on orders", "2026-07-11 09:14:00")
    b = inc("B", "checkout-api", "timeout contacting gateway", "2026-07-11 09:15:00")
    assert len(cluster([a, b], CFG)) == 2


def test_exactly_at_window_boundary_clusters():
    a = inc("A", "svc", "500 errors", "2026-07-11 09:00:00")
    b = inc("B", "svc", "500 errors", "2026-07-11 09:30:00")  # exactly 30 min
    assert len(cluster([a, b], CFG)) == 1


def test_one_second_past_window_does_not_cluster():
    a = inc("A", "svc", "500 errors", "2026-07-11 09:00:00")
    b = inc("B", "svc", "500 errors", "2026-07-11 09:30:01")
    assert len(cluster([a, b], CFG)) == 2


def test_unparseable_timestamp_isolated_not_crashed():
    a = inc("A", "svc", "500 errors", "not-a-date")
    b = inc("B", "svc", "500 errors", "2026-07-11 09:00:00")
    groups = cluster([a, b], CFG)
    assert len(groups) == 2  # never merged, never raised


def test_empty_input():
    assert cluster([], CFG) == []


def test_signature_token_word_boundaries():
    # '500' must not match inside '1500'; 'orders' not inside 'borders'
    i = inc("A", "svc", "1500 borders", "2026-07-11 09:00:00")
    assert signature(i, CFG.signature_tokens) == "svc|"


def test_signature_is_configurable():
    i = inc("A", "svc", "wombat failure", "2026-07-11 09:00:00")
    assert signature(i, ["wombat"]) == "svc|wombat"


def test_parse_ts_formats_and_utc_normalization():
    assert parse_ts("2026-07-11 09:14:00").hour == 9
    assert parse_ts("2026-07-11T09:14:00").minute == 14
    # +02:00 offset normalizes to 07:14 UTC
    assert parse_ts("2026-07-11T09:14:00+02:00").hour == 7


def test_parse_ts_garbage_raises():
    with pytest.raises(ValueError):
        parse_ts("yesterday-ish")
