"""Scrubber tests. The 4111... etc numbers are the standard public test PANs."""
from triage_agent.scrubber import luhn_ok, scrub


def test_luhn_valid_known_test_cards():
    assert luhn_ok("4111111111111111")        # Visa test PAN
    assert luhn_ok("4242424242424242")        # Visa test PAN (Stripe docs)
    assert luhn_ok("378282246310005")         # Amex test PAN (15 digits)


def test_luhn_invalid_cases():
    assert not luhn_ok("4111111111111112")    # last digit off by one
    assert not luhn_ok("1234567890123456")    # arbitrary digits
    assert not luhn_ok("411111111111")        # 12 digits: too short
    assert not luhn_ok("41111111111111111111")  # 20 digits: too long
    assert not luhn_ok("")                    # empty
    assert not luhn_ok("4111-1111")           # non-digits


def test_pan_redacted_with_spaces_dashes_and_plain():
    for text in (
        "card=4111111111111111 ok",
        "card=4111 1111 1111 1111 ok",
        "card=4111-1111-1111-1111 ok",
    ):
        result = scrub(text)
        assert "[PAN-REDACTED-...1111]" in result.text
        assert "4111" not in result.text.replace("...1111", "")
        assert result.by_class.get("pan") == 1


def test_pan_spacing_preserved_after_redaction():
    # Regression test for the demo bug that glued the next token on.
    result = scrub("card=4111 1111 1111 1111 email=x")
    assert "[PAN-REDACTED-...1111] email=x" in result.text


def test_luhn_invalid_number_is_kept_as_diagnostic_signal():
    result = scrub("order ref 1234 5678 9012 3456 failed")
    assert "1234 5678 9012 3456" in result.text
    assert result.total == 0


def test_amex_15_digit_redacted():
    result = scrub("pan=378282246310005")
    assert "[PAN-REDACTED-...0005]" in result.text


def test_email_redacted():
    result = scrub("contact jane.doe+test@example.co.uk now")
    assert "[EMAIL-REDACTED]" in result.text
    assert "jane" not in result.text


def test_api_tokens_redacted():
    result = scrub(
        "key sk-ant-abc123def456ghi789 and ghp_ABCDEFGHIJKLMNOPQRST12 "
        "and AKIAIOSFODNN7EXAMPLE and xoxb-1234567890-abcdef"
    )
    assert result.by_class.get("token") == 4
    assert "sk-ant" not in result.text and "AKIA" not in result.text


def test_bearer_header_redacted():
    result = scrub("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9")
    assert "[BEARER-REDACTED]" in result.text
    assert "eyJ" not in result.text


def test_ssn_and_phone_redacted():
    result = scrub("ssn 123-45-6789 phone +1 415-555-2671")
    assert "[SSN-REDACTED]" in result.text
    assert "[PHONE-REDACTED]" in result.text


def test_clean_text_untouched():
    text = "2026-07-11 09:11:02 ERROR OrderService http_status=500 path=/orders"
    result = scrub(text)
    assert result.text == text
    assert result.total == 0


def test_empty_string():
    result = scrub("")
    assert result.text == "" and result.total == 0


def test_version_numbers_and_timestamps_not_redacted():
    text = "v2026.07.11.2 at 09:02, requests=1180, error_rate=0.24"
    assert scrub(text).text == text


def test_multiple_pans_counted_separately():
    result = scrub("a=4111111111111111 b=4242424242424242")
    assert result.by_class["pan"] == 2
