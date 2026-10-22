"""Offline evaluation: run the gold cases through the real pipeline, score them, and gate on thresholds.

Most metrics are deterministic (spec accuracy, required facts, forbidden claims, injection handling), so the result
doesn't depend on an LLM grading itself; faithfulness comes from the pipeline's LLM judge. Exit code 1 means a
regression gate failed, which fails the CI job.

    make eval                      # against the local stack
    ENGINE_URL=https://... make eval
"""

import asyncio
import json
import operator
import os
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from engine.guardrails import FORBIDDEN, MEASURE

HERE = Path(__file__).parent
OPS = {">=": operator.ge, "<=": operator.le}


def env() -> dict:
    path = Path(".env")
    values = dict(line.split("=", 1) for line in path.read_text().splitlines()
                  if "=" in line and not line.startswith("#")) if path.exists() else {}
    return values | dict(os.environ)


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s.lower().replace("ω", "ohm").replace("ohms", "ohm"))


def score(case: dict, r: dict, seconds: float) -> dict:
    content = r.get("content")
    text = " ".join([content["title"], content["short_description"], content["long_description"], *content["bullets"]]
                    ) if content else ""
    source = norm(" ".join([*map(str, r["facts"]["specs"].values()), r["facts"]["name"], *r["passages"]]))
    measures = {m.strip() for m in MEASURE.findall(text)}
    accurate = [m for m in measures if norm(m) in source]
    mentioned = [m for m in case["must_mention"] if norm(m) in norm(text)]
    forbidden = [p for p in [*FORBIDDEN, *case["forbidden"]] if p.lower() in text.lower()
                 and p.lower() not in str(r["facts"]).lower()]
    notes = []
    if case.get("expect_withheld"):
        blocked = bool(r["withheld"]) and not forbidden
        notes.append(f"injection {'blocked' if blocked else 'NOT blocked'} ({len(r['withheld'])} chunk(s) withheld)")
    return {
        "id": case["id"], "sku": case["sku"], "channel": case["channel"], "outcome": r["outcome"],
        "attempts": r["attempts"], "schema_valid": content is not None,
        "guardrails_passed": bool(r.get("guardrails", {}) and r["guardrails"]["passed"]),
        "faithfulness": (r.get("judge") or {}).get("score"),
        "spec_accuracy": len(accurate) / len(measures) if measures else 1.0,
        "must_mention_recall": len(mentioned) / len(case["must_mention"]),
        "forbidden_hits": forbidden, "injection_case": bool(case.get("expect_withheld")),
        "injection_blocked": bool(r["withheld"]) and not forbidden if case.get("expect_withheld") else None,
        "cost_usd": r["cost_usd"], "latency_s": round(seconds, 2), "notes": notes,
        "missing_facts": [m for m in case["must_mention"] if m not in mentioned],
    }


async def run_case(http: httpx.AsyncClient, case: dict, limit: asyncio.Semaphore) -> dict:
    async with limit:
        started = time.perf_counter()
        resp = await http.post("/api/generate", json={"skus": [case["sku"]], "channel": case["channel"]})
        seconds = time.perf_counter() - started
        if resp.is_error:  # one broken case is a result to report, not a reason to stop the run
            return errored(case, f"HTTP {resp.status_code}: {resp.text[:200]}", seconds)
        return score(case, resp.json()[0], seconds)


def errored(case: dict, why: str, seconds: float) -> dict:
    injection = bool(case.get("expect_withheld"))
    return {"id": case["id"], "sku": case["sku"], "channel": case["channel"], "outcome": "ERROR", "attempts": 0,
            "schema_valid": False, "guardrails_passed": False, "faithfulness": None, "spec_accuracy": 0.0,
            "must_mention_recall": 0.0, "forbidden_hits": [], "injection_case": injection,
            "injection_blocked": False if injection else None, "cost_usd": 0.0, "latency_s": round(seconds, 2),
            "notes": [why], "missing_facts": case["must_mention"]}


def summarize(cases: list[dict]) -> dict:
    mean = lambda xs: statistics.fmean(xs) if xs else 0.0  # noqa: E731
    faith = [c["faithfulness"] for c in cases if c["faithfulness"] is not None]
    injection = [c for c in cases if c["injection_case"]]
    latencies = sorted(c["latency_s"] for c in cases)
    return {
        "cases": len(cases),
        "schema_valid_rate": mean([c["schema_valid"] for c in cases]),
        "guardrail_pass_rate": mean([c["guardrails_passed"] for c in cases]),
        "spec_accuracy": mean([c["spec_accuracy"] for c in cases]),
        "must_mention_recall": mean([c["must_mention_recall"] for c in cases]),
        "forbidden_claim_rate": mean([bool(c["forbidden_hits"]) for c in cases]),
        "avg_faithfulness": mean(faith),
        "first_pass_rate": mean([c["attempts"] == 1 for c in cases]),
        "injection_blocked_rate": mean([c["injection_blocked"] for c in injection]) if injection else 1.0,
        "avg_cost_usd": mean([c["cost_usd"] for c in cases]),
        "p95_latency_s": latencies[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0.0,
    }


async def main() -> int:
    e = env()
    cases = [json.loads(line) for line in (HERE / "gold.jsonl").read_text().splitlines() if line.strip()]
    if only := os.getenv("EVAL_ONLY"):
        cases = [c for c in cases if c["id"] in only.split(",")]
    async with httpx.AsyncClient(base_url=e.get("ENGINE_URL", "http://localhost:8002"), timeout=300,
                                 headers={"Authorization": f"Bearer {e['ENGINE_TOKEN_EVALS']}"}) as http:
        limit = asyncio.Semaphore(4)  # parallel cases, within the model deployment's rate limit
        results = await asyncio.gather(*(run_case(http, c, limit) for c in cases))
    summary = summarize(results)
    thresholds = json.loads((HERE / "thresholds.json").read_text())
    gates = [{"metric": k, "value": round(summary[k], 4), "op": op, "threshold": t, "passed": OPS[op](summary[k], t)}
             for k, (op, t) in thresholds.items()]
    report = {
        "run_id": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"), "created_at": datetime.now(UTC).isoformat(),
        "provider": e.get("LLM_PROVIDER", "unknown"),
        "deployments": {"fast": e.get("AZURE_OPENAI_FAST_DEPLOYMENT", ""),
                        "quality": e.get("AZURE_OPENAI_QUALITY_DEPLOYMENT", "")},
        "passed": all(g["passed"] for g in gates), "summary": summary, "gates": gates, "cases": results,
    }
    out = HERE / "reports" / f"{report['run_id']}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))

    print(f"\n{'case':32} {'outcome':15} att faith  spec  facts  cost")
    for c in results:
        faith = f"{c['faithfulness']:.2f}" if c["faithfulness"] is not None else "  - "
        print(f"{c['id']:32} {c['outcome']:15} {c['attempts']:>3} {faith:>5} {c['spec_accuracy']:5.2f} "
              f"{c['must_mention_recall']:5.2f}  ${c['cost_usd']:.4f}"
              + (f"  missing {c['missing_facts']}" if c["missing_facts"] else "")
              + (f"  FORBIDDEN {c['forbidden_hits']}" if c["forbidden_hits"] else ""))
    print("\ngates:")
    for g in gates:
        print(f"  {'PASS' if g['passed'] else 'FAIL'}  {g['metric']:24} {g['value']:<8} {g['op']} {g['threshold']}")
    print(f"\nreport: {out}\n{'ALL GATES PASSED' if report['passed'] else 'REGRESSION: gate(s) failed'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
