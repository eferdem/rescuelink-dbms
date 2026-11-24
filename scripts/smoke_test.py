#!/usr/bin/env python3
"""
End-to-end smoke test for the RescueLink web application.

Exercises all four roles, the RBAC boundaries, donor CRUD, both multi-table
transactions (commit and rollback paths) and every analytics page.  Uses
Flask's test client, so no browser or running server is required.

Usage:  py -3.11 scripts/smoke_test.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

import app as flask_app  # noqa: E402
from db import dsn  # noqa: E402

PASSWORD = "Demo1234!"
results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((bool(ok), name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail and not ok else ""))


def login(client, email):
    return client.post("/login", data={"email": email, "password": PASSWORD})


def table_counts(cur) -> dict[str, int]:
    tables = ["donation_batches", "batch_allocations", "cold_chain_checks",
              "delivery_runs", "run_batches", "shift_assignments", "audit_log"]
    return {t: cur.execute(f"SELECT count(*) AS n FROM {t}").fetchone()["n"] for t in tables}


def main() -> int:
    conn = psycopg.connect(dsn(), autocommit=True, row_factory=dict_row)
    cur = conn.cursor()

    available = cur.execute("""
        SELECT batch_id, remaining_kg FROM v_batch_availability
        WHERE status = 'available' AND remaining_kg >= 5 AND expires_at > now()
        ORDER BY expires_at LIMIT 1
    """).fetchone()
    coordinator = cur.execute("""
        SELECT u.user_id, u.org_id FROM users u
        JOIN roles r ON r.role_id = u.role_id
        WHERE r.role_name = 'Recipient Coordinator' AND u.is_active
        ORDER BY u.user_id LIMIT 1
    """).fetchone()
    volunteer = cur.execute("""
        SELECT u.user_id FROM users u
        JOIN roles r ON r.role_id = u.role_id
        JOIN volunteer_skills vs ON vs.user_id = u.user_id AND vs.skill_id = 2
        WHERE r.role_name = 'Volunteer Courier'
          AND vs.expires_on > current_date AND u.is_active
        ORDER BY u.user_id LIMIT 1
    """).fetchone()
    vehicle = cur.execute("""
        SELECT vehicle_id FROM vehicles WHERE is_active ORDER BY vehicle_id LIMIT 1
    """).fetchone()
    if not all([available, coordinator, volunteer, vehicle]):
        print("Seed data missing; run scripts/reset_db.ps1 first.")
        return 2

    print("\n== Public surface ==")
    client = flask_app.app.test_client()
    r = client.get("/")
    check("anonymous / redirects to login", r.status_code == 302 and "/login" in r.headers["Location"])
    check("login page renders", client.get("/login").status_code == 200)

    print("\n== Admin role ==")
    check("admin login", login(client, "admin@rescuelink.org").status_code == 302)
    for path in ("/", "/batches", "/admin/organizations", "/admin/categories",
                 "/admin/users", "/admin/vehicles", "/audit", "/analytics",
                 "/performance", "/runs", "/transaction"):
        r = client.get(path)
        check(f"GET {path}", r.status_code == 200, f"status {r.status_code}")

    org_name = "Smoke Test Bakery"
    r = client.post("/admin/organizations", data={
        "name": org_name, "org_type": "donor", "tax_id": "9999999999",
        "address": "Test 1", "district": "Kadikoy",
        "contact_email": "test@smoke.org", "phone": "+90 555 000 00 00",
        "cold_storage_kg": "10",
    }, follow_redirects=True)
    created = cur.execute("SELECT org_id FROM organizations WHERE name = %s", (org_name,)).fetchone()
    check("admin creates an organisation", created is not None and r.status_code == 200)
    if created:
        r = client.post(f"/admin/organizations/{created['org_id']}/delete", follow_redirects=True)
        gone = cur.execute("SELECT 1 FROM organizations WHERE name = %s", (org_name,)).fetchone()
        check("admin deletes an unused organisation", gone is None)
    r = client.post("/admin/organizations", data={
        "name": "Dup Partner", "org_type": "donor",
        "tax_id": cur.execute("SELECT tax_id FROM organizations ORDER BY org_id LIMIT 1").fetchone()["tax_id"],
        "address": "x", "district": "x", "contact_email": "d@x.org", "phone": "1",
        "cold_storage_kg": "0",
    }, follow_redirects=True)
    check("unique tax id is enforced", "already exists" in r.get_data(as_text=True))

    print("\n== Donor role ==")
    donor = flask_app.app.test_client()
    check("donor login", login(donor, "donor@rescuelink.org").status_code == 302)
    for path in ("/", "/batches", "/batches/new"):
        check(f"GET {path}", donor.get(path).status_code == 200)
    r = donor.get("/analytics")
    check("donor is denied /analytics (RBAC)", r.status_code == 403)
    r = donor.get("/transaction")
    check("donor is denied /transaction (RBAC)", r.status_code == 403)

    category = cur.execute("SELECT category_id FROM food_categories ORDER BY category_id LIMIT 1").fetchone()
    now = datetime.now(timezone.utc)
    r = donor.post("/batches/new", data={
        "item_description": "Smoke test loaves",
        "category_id": category["category_id"],
        "quantity_kg": "12.5",
        "prepared_at": now.strftime("%Y-%m-%dT%H:%M"),
        "expires_at": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
        "storage_temp_c": "18",
    }, follow_redirects=True)
    batch = cur.execute("""
        SELECT batch_id, status FROM donation_batches
        WHERE item_description = 'Smoke test loaves' ORDER BY batch_id DESC LIMIT 1
    """).fetchone()
    check("donor creates a batch", batch is not None and r.status_code == 200)

    if batch:
        r = donor.post(f"/batches/{batch['batch_id']}/edit", data={
            "item_description": "Smoke test loaves (edited)",
            "category_id": category["category_id"],
            "quantity_kg": "14",
            "prepared_at": now.strftime("%Y-%m-%dT%H:%M"),
            "expires_at": (now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M"),
            "storage_temp_c": "18",
        }, follow_redirects=True)
        updated = cur.execute("""
            SELECT quantity_kg, item_description FROM donation_batches WHERE batch_id = %s
        """, (batch["batch_id"],)).fetchone()
        check("donor updates the batch", float(updated["quantity_kg"]) == 14.0)
        r = donor.post(f"/batches/{batch['batch_id']}/withdraw", follow_redirects=True)
        withdrawn = cur.execute("""
            SELECT status FROM donation_batches WHERE batch_id = %s
        """, (batch["batch_id"],)).fetchone()
        check("donor withdraws (soft delete) the batch", withdrawn["status"] == "withdrawn")
        admin_client = flask_app.app.test_client()
        login(admin_client, "admin@rescuelink.org")
        check("withdrawn batch stays visible in admin list",
              "Smoke test loaves" in admin_client.get("/batches").get_data(as_text=True))

    print("\n== Coordinator + ACID transaction ==")
    coord = flask_app.app.test_client()
    check("coordinator login", login(coord, "coordinator@rescuelink.org").status_code == 302)
    check("GET /claims", coord.get("/claims").status_code == 200)

    base = {
        "batch_id": available["batch_id"],
        "kg": "5",
        "volunteer_id": volunteer["user_id"],
        "vehicle_id": vehicle["vehicle_id"],
        "temp_c": "3.5",
    }

    before = table_counts(cur)
    r = coord.post("/transaction", data={**base, "mode": "none"})
    text = r.get_data(as_text=True)
    after = table_counts(cur)
    check("successful dispatch returns 200", r.status_code == 200)
    check("successful dispatch commits", "Committed" in text)
    check("allocation row written", after["batch_allocations"] == before["batch_allocations"] + 1)
    check("run + shift + audit rows written",
          after["delivery_runs"] == before["delivery_runs"] + 1
          and after["shift_assignments"] == before["shift_assignments"] + 1
          and after["audit_log"] == before["audit_log"] + 1)
    check("batch status flipped to claimed",
          cur.execute("SELECT status FROM donation_batches WHERE batch_id = %s",
                      (available["batch_id"],)).fetchone()["status"] == "claimed")

    second = cur.execute("""
        SELECT batch_id, remaining_kg FROM v_batch_availability
        WHERE status = 'available' AND remaining_kg >= 5 AND expires_at > now()
        ORDER BY expires_at LIMIT 1
    """).fetchone()
    cold_batch = cur.execute("""
        SELECT batch_id FROM v_batch_availability
        WHERE status = 'available' AND remaining_kg >= 5 AND expires_at > now()
          AND needs_refrigeration
        ORDER BY expires_at LIMIT 1
    """).fetchone()
    for mode, label, batch_row in [
        ("over_allocate", "over-allocation rolls back", second),
        ("cold_chain", "cold-chain breach rolls back", cold_batch),
        ("late_failure", "late constraint violation rolls back", second),
        ("expired", "expired batch is rejected", second),
    ]:
        before = table_counts(cur)
        r = coord.post("/transaction", data={**base, "batch_id": batch_row["batch_id"], "mode": mode})
        text = r.get_data(as_text=True)
        after = table_counts(cur)
        check(label, "Rolled back" in text and before == after,
              f"counts changed: {before} -> {after}")

    before = table_counts(cur)
    r = coord.post("/transaction", data={**base, "batch_id": second["batch_id"],
                                        "mode": "no_certified_volunteer"})
    check("uncertified courier is rejected",
          "Rolled back" in r.get_data(as_text=True))
    check("rejected transaction wrote nothing", table_counts(cur) == before)

    allocation = cur.execute("""
        SELECT a.allocation_id, a.allocated_kg
        FROM batch_allocations a
        WHERE a.recipient_org_id = %s AND a.status = 'reserved'
        ORDER BY a.allocation_id DESC LIMIT 1
    """, (coordinator["org_id"],)).fetchone()
    if allocation:
        r = coord.post(f"/handovers/{allocation['allocation_id']}", data={
            "received_kg": str(allocation["allocated_kg"]),
            "condition": "good",
            "received_by_name": "Smoke Tester",
        }, follow_redirects=True)
        status = cur.execute("""
            SELECT status FROM batch_allocations WHERE allocation_id = %s
        """, (allocation["allocation_id"],)).fetchone()["status"]
        check("handover transaction commits", status == "handed_over")
        check("handover receipt exists", cur.execute("""
            SELECT 1 FROM handovers WHERE allocation_id = %s
        """, (allocation["allocation_id"],)).fetchone() is not None)
        before = table_counts(cur)
        r = coord.post(f"/handovers/{allocation['allocation_id']}", data={
            "received_kg": "0", "condition": "good"}, follow_redirects=True)
        check("double handover is rejected",
              "rolled back" in r.get_data(as_text=True).lower()
              and table_counts(cur)["batch_allocations"] == before["batch_allocations"])

    check("GET /analytics renders 9 results",
          len(flask_app.ANALYTICS_ORDER) == 9
          and coord.get("/analytics").get_data(as_text=True).count("Show the SQL") == 9)

    print("\n== Courier role ==")
    courier = flask_app.app.test_client()
    check("courier login", login(courier, "courier@rescuelink.org").status_code == 302)
    check("GET /", courier.get("/").status_code == 200)
    check("GET /runs", courier.get("/runs").status_code == 200)
    check("courier is denied /batches (RBAC)", courier.get("/batches").status_code == 403)
    check("courier is denied /audit (RBAC)", courier.get("/audit").status_code == 403)
    check("courier is denied /admin/users (RBAC)", courier.get("/admin/users").status_code == 403)

    print("\n== Summary ==")
    failed = [r for r in results if not r[0]]
    print(f"  {len(results) - len(failed)} passed, {len(failed)} failed of {len(results)}")
    conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
