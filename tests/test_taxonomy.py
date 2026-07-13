"""parse_diagnosis should survive whatever the model throws at it."""
from triage_agent.taxonomy import TAXONOMY, parse_diagnosis


def valid_payload(**overrides):
    base = {
        "category": "code-defect",
        "confidence": 0.9,
        "narrative": "n",
        "evidence": ["e1"],
        "next_step": "s",
        "fix_hint": None,
    }
    base.update(overrides)
    return base


def test_valid_json_string_parses():
    import json
    d = parse_diagnosis(json.dumps(valid_payload()))
    assert d.category == "code-defect" and d.confidence == 0.9
    assert d.guardrail_notes == []


def test_json_with_markdown_fences_tolerated():
    import json
    d = parse_diagnosis("```json\n" + json.dumps(valid_payload()) + "\n```")
    assert d.category == "code-defect"


def test_valid_fix_hint_accepted():
    d = parse_diagnosis(valid_payload(fix_hint={
        "file": "a/B.java", "problem": "p", "suggested_patch": "x"}))
    assert d.fix_hint == {"file": "a/B.java", "problem": "p", "suggested_patch": "x"}


def test_garbage_text_degrades_to_unknown():
    d = parse_diagnosis("I think it's probably the database???")
    assert d.category == "unknown" and d.confidence == 0.0
    assert "unparseable-json" in d.guardrail_notes


def test_json_array_not_object_rejected():
    d = parse_diagnosis("[1,2,3]")
    assert d.category == "unknown" and "not-an-object" in d.guardrail_notes


def test_invented_category_clamped():
    d = parse_diagnosis(valid_payload(category="alien-invasion", confidence=0.99))
    assert d.category == "unknown"
    assert d.confidence <= 0.3
    assert any(n.startswith("invalid-category") for n in d.guardrail_notes)


def test_malformed_fix_hint_dropped_and_noted():
    d = parse_diagnosis(valid_payload(fix_hint={"file": "only-file-key"}))
    assert d.fix_hint is None
    assert "malformed-fix-hint" in d.guardrail_notes


def test_missing_fields_noted():
    d = parse_diagnosis({"category": "config"})
    assert "missing-field:confidence" in d.guardrail_notes


def test_confidence_clamped_to_unit_interval():
    assert parse_diagnosis(valid_payload(confidence=1.7)).confidence == 1.0
    assert parse_diagnosis(valid_payload(confidence=-0.5)).confidence == 0.0


def test_confidence_wrong_type_becomes_zero():
    d = parse_diagnosis(valid_payload(confidence="very high"))
    assert d.confidence == 0.0 and "bad-confidence-type" in d.guardrail_notes


def test_every_taxonomy_value_roundtrips():
    for cat in TAXONOMY:
        assert parse_diagnosis(valid_payload(category=cat)).category == cat


def test_oversized_fields_truncated():
    d = parse_diagnosis(valid_payload(narrative="x" * 10000,
                                      evidence=["y" * 1000] * 50))
    assert len(d.narrative) == 4000
    assert len(d.evidence) == 20 and len(d.evidence[0]) == 500
