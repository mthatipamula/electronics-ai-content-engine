"""Keeps personal data out of prompts and logs. Deliberately conservative: false positives are cheap here."""

import re

_PATTERNS: dict[str, re.Pattern[str]] = {
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "PHONE": re.compile(r"(?<!\w)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\w)"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "CARD": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
}


def find(text: str) -> list[str]:
    return [kind for kind, rx in _PATTERNS.items() if rx.search(text)]


def redact(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    for kind, rx in _PATTERNS.items():
        text, n = rx.subn(f"[{kind}]", text)
        if n:
            found.append(kind)
    return text, found
