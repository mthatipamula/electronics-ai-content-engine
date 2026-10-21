"""Deterministic checks on generated content. Cheap, explainable, and run on every draft before any human sees it."""

import re
from dataclasses import asdict, dataclass

from common import pii

from .models import CHANNEL_RULES, ProductContent

# Claims a product page must never make unless the source data says so (legal and brand-safety risk).
FORBIDDEN = {
    "military grade": "unverified military claim", "mil-spec": "unverified military claim",
    "lifetime warranty": "warranty terms come from legal, not marketing copy",
    "guaranteed": "absolute guarantee", "best in class": "unprovable superlative", "best-in-class":
    "unprovable superlative", "#1": "unprovable superlative", "unbreakable": "unprovable claim",
    "waterproof": "use the IP rating instead", "ul listed": "certification not in source data",
    "fda": "certification not in source data",
}

# Numbers with units that matter on a datasheet: frequencies, impedance, VSWR, power, length, temperature, speed.
MEASURE = re.compile(
    r"(?<![\w.])[-+±]?\d+(?:\.\d+)?\s*(?:ghz|mhz|khz|ohms?|Ω|db[im]?|w|mw|m|mm|cm|awg|gbps|mbps|°c|:1)(?![a-z])",
    re.I)


@dataclass
class Finding:
    rule: str
    severity: str  # error | warning
    detail: str


def _norm(s: str) -> str:
    s = s.lower().replace("ω", "ohm").replace("ohms", "ohm").replace("±", "")
    return re.sub(r"\s+", "", s)


def check(content: ProductContent, facts: dict, passages: list[str], channel: str) -> list[Finding]:
    findings: list[Finding] = []
    text = " ".join([content.title, content.short_description, content.long_description, *content.bullets])
    lower = text.lower()
    rules = CHANNEL_RULES[channel]

    if len(content.title) > rules["title_max"]:
        findings.append(Finding("title_length", "error", f"Title is {len(content.title)} chars; {channel} allows "
                                                         f"{rules['title_max']}"))
    lo, hi = rules["bullets"]
    if not lo <= len(content.bullets) <= hi:
        findings.append(Finding("bullet_count", "error", f"{len(content.bullets)} bullets; {channel} needs {lo}-{hi}"))
    if len(content.long_description) > rules["long_max"]:
        findings.append(Finding("long_description_length", "warning", "Long description exceeds channel guidance"))

    for phrase, why in FORBIDDEN.items():
        if phrase in lower and phrase not in str(facts).lower():
            findings.append(Finding("forbidden_claim", "error", f"'{phrase}': {why}"))

    if found := pii.find(text):
        findings.append(Finding("pii", "error", f"Output contains personal data: {', '.join(found)}"))

    if re.search(r"ignore (all )?(previous|prior) instructions|as an ai|system prompt", lower):
        findings.append(Finding("instruction_leak", "error", "Output echoes model instructions"))

    # Every measurement in the copy must exist in the specs or the retrieved datasheet text.
    source = _norm(" ".join([str(v) for v in facts.get("specs", {}).values()] + [facts.get("name", "")] + passages))
    for m in {x.strip() for x in MEASURE.findall(text)}:
        if _norm(m) not in source:
            findings.append(Finding("spec_mismatch", "error", f"'{m}' does not appear in the product data"))

    unknown = [k for k in content.cited_spec_keys if k not in facts.get("specs", {})]
    if unknown:
        findings.append(Finding("unknown_spec_key", "warning", f"Cited spec keys not in source: {unknown}"))
    return findings


def summarize(findings: list[Finding]) -> dict:
    return {"passed": not any(f.severity == "error" for f in findings), "findings": [asdict(f) for f in findings]}
