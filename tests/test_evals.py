"""The evaluation must be able to fail: bad output has to lower the scores and trip the gates."""

from evals.run import score, summarize

CASE = {"id": "c", "sku": "S", "channel": "web", "must_mention": ["18 GHz", "50 Ohm"], "forbidden": []}
FACTS = {"name": "SMA connector", "specs": {"max_frequency": "18 GHz", "impedance": "50 Ohm"}}


def result(text: str, withheld=()) -> dict:
    return {"outcome": "PENDING_REVIEW", "attempts": 1, "facts": FACTS, "passages": [], "withheld": list(withheld),
            "guardrails": {"passed": True}, "judge": {"score": 1.0}, "cost_usd": 0.001,
            "content": {"title": "SMA connector", "short_description": text, "long_description": text,
                        "bullets": [text]}}


def test_good_output_scores_full_marks():
    s = score(CASE, result("Rated to 18 GHz at 50 Ohm."), 1.0)
    assert s["spec_accuracy"] == 1.0 and s["must_mention_recall"] == 1.0 and not s["forbidden_hits"]


def test_invented_spec_and_missing_fact_are_scored_down():
    s = score(CASE, result("Rated to 26.5 GHz."), 1.0)
    assert s["spec_accuracy"] == 0.0 and s["must_mention_recall"] == 0.0


def test_forbidden_claim_and_failed_injection_defense():
    case = CASE | {"expect_withheld": True}
    s = score(case, result("Military grade, rated to 18 GHz at 50 Ohm."), 1.0)  # nothing withheld, claim leaked
    summary = summarize([s])
    assert s["forbidden_hits"] == ["military grade"]
    assert summary["forbidden_claim_rate"] == 1.0 and summary["injection_blocked_rate"] == 0.0
