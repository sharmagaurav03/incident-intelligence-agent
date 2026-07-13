"""Strip PII/creds from evidence before it hits the LLM or disk.

Over-redacting is fine, under-redacting isn't. Exception: card-shaped
numbers that fail Luhn are kept, they're usually order ids and we want
them in the diagnosis.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


_PAN = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(
    r"(?<![\w.])(?:\+?\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]\d{3}[ .-]\d{4}\b"
)
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# the usual suspects. add patterns here as we find new leaks in logs
_TOKENS = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{8,}"
    r"|sk-[A-Za-z0-9]{20,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|gho_[A-Za-z0-9]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,})"
)
_BEARER = re.compile(r"(?i)\b(Bearer|Authorization:?)[ \t]+[A-Za-z0-9._~+/=-]{16,}")


@dataclass
class ScrubResult:
    text: str
    total: int
    by_class: dict[str, int] = field(default_factory=dict)


def _redact_pans(text, counts):
    def repl(m):
        digits = re.sub(r"[ -]", "", m.group(0))
        if luhn_ok(digits):
            counts["pan"] = counts.get("pan", 0) + 1
            return f"[PAN-REDACTED-...{digits[-4:]}]"
        return m.group(0)
    return _PAN.sub(repl, text)


def scrub(text: str) -> ScrubResult:
    counts: dict[str, int] = {}

    def sub(pattern, label, replacement, s):
        def repl(m):
            counts[label] = counts.get(label, 0) + 1
            return replacement
        return pattern.sub(repl, s)

    out = _redact_pans(text, counts)
    out = sub(_EMAIL, "email", "[EMAIL-REDACTED]", out)
    out = sub(_TOKENS, "token", "[TOKEN-REDACTED]", out)
    out = sub(_BEARER, "bearer", "[BEARER-REDACTED]", out)
    out = sub(_SSN, "ssn", "[SSN-REDACTED]", out)
    out = sub(_PHONE, "phone", "[PHONE-REDACTED]", out)
    return ScrubResult(text=out, total=sum(counts.values()), by_class=counts)
