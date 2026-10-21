"""End-to-end check against the running stack: generate, review, security controls, cost tracking."""

import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

env = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines()
           if "=" in line and not line.startswith("#"))
ENGINE = os.getenv("ENGINE_URL", "http://localhost:8002")
web = httpx.Client(base_url=ENGINE, timeout=120, headers={"Authorization": f"Bearer {env['ENGINE_TOKEN_WEB']}"})
ok = True


def check(name: str, cond: bool, detail: str = "") -> None:
    global ok
    ok &= cond
    print(f"{'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")


async def mcp_call(token: str | None, tool: str, args: dict):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    http = httpx2.AsyncClient(headers=headers, timeout=30)
    async with http, Client(streamable_http_client("http://localhost:8001/mcp", http_client=http)) as c:
        return await c.call_tool(tool, args)


products = web.get("/api/products").json()
check("catalog reachable through MCP", len(products["products"]) >= 20, f"{len(products['products'])} products")

jobs = web.post("/api/jobs", json={"skus": ["ARF-SMA-2040", "ARF-SMA-2041", "HXC-CAT6A-SFTP-03M-GRA"],
                                   "channel": "web"}).json()["jobs"]
deadline = time.time() + 120
while time.time() < deadline:
    states = [web.get(f"/api/jobs/{j['job_id']}").json() for j in jobs]
    if all(s["status"] not in ("QUEUED", "RUNNING") for s in states):
        break
    time.sleep(1)
for s in states:
    check(f"job {s['sku']} finished", s["status"] in ("PENDING_REVIEW", "FAILED"),
          f"{s['status']}, attempts={s['attempts']}, cost=${s['cost_usd']}, error={s['error']}")

poisoned = next(s for s in states if s["sku"] == "ARF-SMA-2041")
check("injected datasheet chunk withheld from the model", len(poisoned["sources"]["withheld"]) >= 1)
check("withheld text never reached the prompt",
      all("NOTE TO AI" not in p["text"] for p in poisoned["sources"]["passages"]))

target = next((s for s in states if s["status"] == "PENDING_REVIEW"), None)
if target:
    r = web.post(f"/api/jobs/{target['id']}/decision", json={"reviewer": "Smoke Test", "decision": "approve"})
    check("approve saves a draft via MCP", r.status_code == 200 and r.json()["draft_version"] >= 1,
          f"HTTP {r.status_code}")
    r = web.post(f"/api/jobs/{target['id']}/decision", json={"reviewer": "Second", "decision": "approve"})
    check("a decided job can't be decided again", r.status_code == 409)

r = asyncio.run(mcp_call(env["CATALOG_TOKEN_READONLY"], "save_draft",
                         {"sku": "ARF-SMA-2040", "content": {}, "job_id": "x", "approved_by": "x"}))
check("read-only agent cannot write drafts", r.is_error)
try:
    asyncio.run(mcp_call(None, "get_product", {"sku": "ARF-SMA-2040"}))
    check("MCP rejects calls without a token", False)
except Exception:  # noqa: BLE001
    check("MCP rejects calls without a token", True)

check("engine API rejects missing token", httpx.get(f"{ENGINE}/api/jobs").status_code == 401)
usage = web.get("/api/usage", params={"group_by": "feature"}).json()
check("gateway attributes cost by feature", any(u["key"] == "product-content" for u in usage),
      ", ".join(f"{u['key']}={u['requests']} calls" for u in usage))
print("metrics:", web.get("/api/metrics").json())
sys.exit(0 if ok else 1)
