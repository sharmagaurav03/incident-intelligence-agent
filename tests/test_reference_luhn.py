"""Second, independently-written Luhn to cross-check the real one."""
def reference_luhn(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    nums = [int(c) for c in digits][::-1]
    doubled = [n if i % 2 == 0 else (n * 2 - 9 if n * 2 > 9 else n * 2)
               for i, n in enumerate(nums)]
    return sum(doubled) % 10 == 0


def test_cross_implementation_agreement():
    import random
    from triage_agent.scrubber import luhn_ok

    known = {
        "4111111111111111": True,
        "4242424242424242": True,
        "378282246310005": True,
        "5555555555554444": True,   # Mastercard test PAN
        "6011111111111117": True,   # Discover test PAN
        "1234567890123456": False,
        "4111111111111112": False,
    }
    for pan, expected in known.items():
        assert luhn_ok(pan) == reference_luhn(pan) == expected, pan

    rng = random.Random(42)  # deterministic corpus
    for _ in range(2000):
        candidate = "".join(str(rng.randint(0, 9)) for _ in range(rng.randint(12, 20)))
        assert luhn_ok(candidate) == reference_luhn(candidate), candidate
