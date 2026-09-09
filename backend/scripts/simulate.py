#!/usr/bin/env python3
"""Seed simulator: a realistic banking agent scenario plus anomalies.

Story
-----
A bank runs three agents. Over two weeks of normal operation:

    analyst-1 (user)  --delegation-->  finance-agent
    finance-agent     --delegation-->  payment-agent
    payment-agent     --api_call---->  bank-api
    payment-agent     --data_access->  payments-db
    finance-agent     --tool_call--->  fx-rate-tool / ledger-tool

Vendor invoices run 800-15,000 USD. Everyone stays inside their lane. This
establishes the baselines the rules need in order to have an opinion.

Then, on the last day, three things go wrong — each one exercising a different
rule, and all three chained to a single legitimate-looking request so the
forensics timeline has a real incident to reconstruct:

    1. finance-agent delegates an 880,000 payment      -> value_excursion
    2. payment-agent delegates to external-agent-x,    -> unseen_counterparty
       an agent it has never contacted before             (+ new_permission)
    3. payment-agent bulk-reads the customer database  -> unseen_counterparty
                                                          (+ new_permission)
    4. external-agent-x ships the funds offshore

Run against a live stack:

    docker compose exec backend python scripts/simulate.py
    docker compose exec backend python scripts/simulate.py --reset
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

# The script lives in backend/scripts/ but imports the app package that sits
# beside it in backend/ (only --reset needs it, which talks to the stores
# directly since the API deliberately exposes no delete route).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")

# --- cast -------------------------------------------------------------------
ANALYST = ("user", "analyst-1")
FINANCE = ("agent", "finance-agent")
PAYMENT = ("agent", "payment-agent")
RECON = ("agent", "reconciliation-agent")

BANK_API = ("api", "bank-api")
FX_TOOL = ("tool", "fx-rate-tool")
LEDGER_TOOL = ("tool", "ledger-tool")
PAYMENTS_DB = ("database", "payments-db")

# The threat actors. Neither appears anywhere in the normal-operations phase.
EXTERNAL = ("agent", "external-agent-x")
OFFSHORE = ("api", "offshore-api")
CUSTOMER_DB = ("database", "customer-db")

VENDORS = [
    "Acme Office Supplies",
    "Northwind Logistics",
    "Globex Consulting",
    "Initech Software",
    "Umbrella Facilities",
    "Stark Industrial",
]

# Ordinary vendor invoices, in USD.
INVOICE_MIN, INVOICE_MAX = 2_000.0, 12_000.0

# Day one deliberately spans the range, near the top first. A baseline learned
# from a handful of samples has a low maximum, so a legitimate large invoice
# arriving on day four would trip value_excursion against it — a true property
# of the rule, not a bug, but noise that would bury the actual incident. Real
# deployments face the same thing and answer it the same way: give the agent a
# representative sample before trusting the range.
FIRST_DAY_AMOUNTS = [11_800.00, 2_400.00, 9_600.00]


# --- transport --------------------------------------------------------------
def _request(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        API_BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")
    except urllib.error.URLError as exc:
        sys.exit(f"cannot reach the API at {API_BASE}: {exc.reason}")


def emit(
    actor,
    target,
    action_type,
    *,
    permissions,
    at,
    metadata=None,
    parent=None,
    reported_status="allowed",
    requested=None,
):
    """Send one event and return its ingest response.

    ``requested`` declares the authority a delegate is asking to receive. The
    policy engine falls back to ``permissions_used`` when it is absent, but a
    delegation should say what it wants explicitly — the two are different
    facts, and only the explicit form can express asking for more (or less) than
    the delegator is exercising.
    """
    actor_type, actor_id = actor
    target_type, target_id = target
    metadata = dict(metadata or {})
    if requested is not None:
        metadata["requested_permissions"] = list(requested)
    payload = {
        "timestamp": at.isoformat(),
        "actor_type": actor_type,
        "actor_id": actor_id,
        "target_type": target_type,
        "target_id": target_id,
        "action_type": action_type,
        "permissions_used": permissions,
        "reported_status": reported_status,
        "metadata": metadata,
    }
    if parent:
        payload["parent_event_id"] = parent
    status, data = _request("POST", "/events", payload)
    if status != 201:
        sys.exit(f"ingest failed ({status}): {data}")
    return data


def event_id(response) -> str:
    return response["event"]["event_id"]


# --- phase 1: two weeks of normal operations --------------------------------
def seed_normal(start: datetime, days: int = 14) -> int:
    """Ordinary invoice runs. Nothing here should raise an alert."""
    rng = random.Random(20260908)  # deterministic: reruns tell the same story
    count = 0

    for day in range(days):
        base = start + timedelta(days=day, hours=9)
        # two to three invoice runs on a working day
        runs = len(FIRST_DAY_AMOUNTS) if day == 0 else rng.randint(2, 3)
        for run in range(runs):
            at = base + timedelta(hours=run * 3, minutes=rng.randint(0, 45))
            if day == 0:
                amount = FIRST_DAY_AMOUNTS[run]
            else:
                amount = round(rng.uniform(INVOICE_MIN, INVOICE_MAX), 2)
            vendor = rng.choice(VENDORS)
            invoice = f"INV-{2026}{day:02d}{run}"

            request = emit(
                ANALYST,
                FINANCE,
                "delegation",
                permissions=["finance:request_payment"],
                requested=["finance:request_payment"],
                at=at,
                metadata={"vendor": vendor, "invoice": invoice, "channel": "ap-portal"},
            )
            count += 1

            emit(
                FINANCE,
                FX_TOOL,
                "tool_call",
                permissions=["fx:read"],
                at=at + timedelta(seconds=20),
                metadata={"base": "USD", "quote": "EUR", "invoice": invoice},
                parent=event_id(request),
            )
            count += 1

            approve = emit(
                FINANCE,
                PAYMENT,
                "delegation",
                permissions=["payments:initiate"],
                requested=["payments:initiate"],
                at=at + timedelta(minutes=1),
                metadata={
                    "amount": amount,
                    "currency": "USD",
                    "vendor": vendor,
                    "invoice": invoice,
                },
                parent=event_id(request),
            )
            count += 1

            transfer = emit(
                PAYMENT,
                BANK_API,
                "api_call",
                permissions=["bank:transfer"],
                at=at + timedelta(minutes=2),
                metadata={
                    "amount": amount,
                    "currency": "USD",
                    "endpoint": "/v1/transfers",
                    "invoice": invoice,
                },
                parent=event_id(approve),
            )
            count += 1

            emit(
                PAYMENT,
                PAYMENTS_DB,
                "data_access",
                permissions=["db:write_payment"],
                at=at + timedelta(minutes=2, seconds=30),
                metadata={"rows": 1, "table": "payments", "invoice": invoice},
                parent=event_id(transfer),
            )
            count += 1

        # end-of-day reconciliation
        eod = base + timedelta(hours=9)
        recon = emit(
            RECON,
            PAYMENTS_DB,
            "data_access",
            permissions=["db:read_payment"],
            at=eod,
            metadata={"rows": rng.randint(20, 60), "table": "payments"},
        )
        count += 1
        emit(
            RECON,
            LEDGER_TOOL,
            "tool_call",
            permissions=["ledger:reconcile"],
            at=eod + timedelta(minutes=5),
            metadata={"period": (start + timedelta(days=day)).date().isoformat()},
            parent=event_id(recon),
        )
        count += 1

    return count


# --- phase 2: the incident --------------------------------------------------
def seed_incident(at: datetime) -> tuple[int, str]:
    """One chained incident that trips every rule. Returns (count, leaf id)."""
    count = 0

    # Looks like any other Tuesday morning invoice request.
    request = emit(
        ANALYST,
        FINANCE,
        "delegation",
        permissions=["finance:request_payment"],
        requested=["finance:request_payment"],
        at=at,
        metadata={
            "vendor": "Globex Consulting",
            "invoice": "INV-2026999",
            "channel": "ap-portal",
        },
    )
    count += 1

    # (1) value_excursion — 880k against a baseline that tops out near 15k.
    approve = emit(
        FINANCE,
        PAYMENT,
        "delegation",
        permissions=["payments:initiate"],
        requested=["payments:initiate"],
        at=at + timedelta(minutes=1),
        metadata={
            "amount": 880_000.00,
            "currency": "USD",
            "vendor": "Globex Consulting",
            "invoice": "INV-2026999",
            "note": "expedited settlement",
        },
        parent=event_id(request),
    )
    count += 1

    # (2) unseen_counterparty + new_permission — payment-agent hands off to an
    #     agent that has never appeared in its history, using rights it has
    #     never held.
    handoff = emit(
        PAYMENT,
        EXTERNAL,
        "delegation",
        permissions=["bank:transfer", "bank:admin"],
        # The handoff asks for more than it is exercising: the payout rights it
        # is using, plus administrative control and customer data it is not.
        # Only the explicit form can express that gap.
        requested=["bank:transfer", "bank:admin", "db:read_pii"],
        at=at + timedelta(minutes=2),
        metadata={
            "amount": 880_000.00,
            "currency": "USD",
            "reason": "counterparty settlement",
            "invoice": "INV-2026999",
        },
        parent=event_id(approve),
    )
    count += 1

    # (3) unseen_counterparty + new_permission — a database it has never
    #     touched, at a row count nothing like its usual single-row writes.
    emit(
        PAYMENT,
        CUSTOMER_DB,
        "data_access",
        permissions=["db:read_pii"],
        at=at + timedelta(minutes=3),
        metadata={
            "rows": 48_500,
            "table": "customers",
            "query": "SELECT * FROM customers",
        },
        parent=event_id(approve),
    )
    count += 1

    # (4) the payout leaves the building.
    exfil = emit(
        EXTERNAL,
        OFFSHORE,
        "api_call",
        permissions=["net:egress"],
        at=at + timedelta(minutes=4),
        metadata={
            "amount": 880_000.00,
            "currency": "USD",
            "endpoint": "https://offshore-api.example/v1/settle",
        },
        parent=event_id(handoff),
    )
    count += 1

    # A control that worked: the platform's own policy refused this one, and the
    # caller reported it as blocked. It should stay blocked, not be downgraded.
    emit(
        EXTERNAL,
        BANK_API,
        "api_call",
        permissions=["bank:admin"],
        at=at + timedelta(minutes=5),
        metadata={"endpoint": "/v1/accounts", "denied_by": "egress-policy"},
        parent=event_id(handoff),
        reported_status="blocked",
    )
    count += 1

    return count, event_id(exfil)


def reset() -> None:
    """Clear both stores so the simulation can be replayed cleanly."""
    from sqlalchemy import text

    from app.db.neo4j import get_driver
    from app.db.postgres import session_scope

    with session_scope() as session:
        session.execute(text("TRUNCATE agent_events, alerts CASCADE"))
    with get_driver().session() as neo:
        neo.run("MATCH (n) DETACH DELETE n")
    print("  cleared postgres and neo4j")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="wipe both stores before seeding"
    )
    parser.add_argument(
        "--days", type=int, default=14, help="days of normal operation to simulate"
    )
    args = parser.parse_args()

    status, _ = _request("GET", "/health")
    if status != 200:
        sys.exit(f"API at {API_BASE} is not healthy (status {status})")

    if args.reset:
        print("resetting stores")
        reset()

    start = datetime.now(UTC) - timedelta(days=args.days + 1)

    print(f"seeding {args.days} days of normal banking operations")
    normal = seed_normal(start, days=args.days)
    print(f"  {normal} events")

    print("seeding the anomalous incident")
    incident_at = start + timedelta(days=args.days, hours=10)
    incident, leaf = seed_incident(incident_at)
    print(f"  {incident} events")

    _, alerts = _request("GET", "/alerts?limit=1000")
    _, graph = _request("GET", "/graph")

    print(f"\ntotal events ingested : {normal + incident}")
    print(f"alerts raised         : {len(alerts)}")
    for a in alerts:
        ev = a["event"]
        print(
            f"  [{a['severity']:<6}] {a['rule_name']:<20} "
            f"{ev['actor_id']} -> {ev['target_id']}"
        )
    print(
        f"graph                 : {graph['stats']['node_count']} nodes, "
        f"{graph['stats']['edge_count']} edges, "
        f"{graph['stats']['suspicious_node_count']} suspicious nodes"
    )
    print("\ninvestigate the incident:")
    print(f"  curl {API_BASE}/forensics/timeline/{leaf}")


if __name__ == "__main__":
    main()
