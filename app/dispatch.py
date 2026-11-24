"""
The multi-step ACID transaction -- "reserve and dispatch a batch".

This module owns the one workflow the project is really about: a recipient
claims surplus food, the platform books a certified courier and a suitable
vehicle, records the cold-chain check and writes an audit row.  If ANY step
fails, every write is rolled back.

Implementation notes
--------------------
* A dedicated connection with ``autocommit=True`` is used so that the
  BEGIN / COMMIT / ROLLBACK statements we show in the UI are the literal
  statements sent to PostgreSQL, not hidden inside a driver context manager.
* ``SELECT ... FOR UPDATE`` holds a row lock on the batch for the whole
  transaction.  Two coordinators claiming the same batch are serialised:
  the second one waits, then sees ``status = 'claimed'`` and is rejected.
* The batch's remaining quantity is always derived from
  ``batch_allocations``; nothing is cached, so there is no drift.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from db import dsn

AFFECTED_TABLES = (
    "donation_batches",
    "batch_allocations",
    "cold_chain_checks",
    "delivery_runs",
    "run_batches",
    "shift_assignments",
    "audit_log",
)

FAILURE_MODES = {
    "none": "Successful dispatch (control case)",
    "over_allocate": "Ask for more kg than the batch still has",
    "expired": "Batch is already past its expiry time",
    "cold_chain": "Cold-chain probe reads 12.5 C",
    "no_certified_volunteer": "Courier has no valid C1 licence",
    "late_failure": "Constraint violation at the very last step",
    "slow_lock": "Hold the row lock for 6 seconds (two-tab demo)",
}


class DispatchError(Exception):
    """A business rule rejected the transaction."""


def _counts(cur) -> dict[str, int]:
    out = {}
    for table in AFFECTED_TABLES:
        out[table] = cur.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]
    return out


def run_dispatch(
    *,
    batch_id: int,
    recipient_org_id: int,
    kg: float,
    volunteer_id: int | None,
    vehicle_id: int,
    temp_c: float,
    mode: str = "none",
    actor_id: int,
    actor_label: str,
) -> dict:
    trace: list[dict] = []
    conn = psycopg.connect(dsn(), autocommit=True, row_factory=dict_row)
    cur = conn.cursor()

    def step(label: str, sql: str, params: tuple | dict | None = None):
        started = time.perf_counter()
        try:
            cur.execute(sql, params)
            entry = {
                "step": label,
                "sql": " ".join(sql.split()),
                "params": params,
                "status": "ok",
                "ms": round((time.perf_counter() - started) * 1000, 2),
            }
            # never fetch here: INSERT ... RETURNING still needs its row
            entry["rows"] = cur.rowcount
            trace.append(entry)
            return cur
        except Exception as exc:                       # noqa: BLE001 - surfaced in UI
            trace.append({
                "step": label,
                "sql": " ".join(sql.split()),
                "params": params,
                "status": "error",
                "error": str(exc).strip().splitlines()[0],
                "ms": round((time.perf_counter() - started) * 1000, 2),
            })
            raise

    def reject(label: str, message: str):
        trace.append({"step": label, "sql": "", "status": "error", "error": message,
                      "rows": None, "params": None, "detail": None})
        raise DispatchError(message)

    counts_before = _counts(cur)
    summary: dict = {}
    error: str | None = None
    ok = False

    try:
        step("BEGIN", "BEGIN")

        batch = cur.execute("""
            SELECT b.batch_id, b.donor_org_id, b.quantity_kg, b.status,
                   b.expires_at, b.storage_temp_c, c.name AS category_name,
                   c.needs_refrigeration, o.name AS donor_name
            FROM donation_batches b
            JOIN food_categories c ON c.category_id = b.category_id
            JOIN organizations   o ON o.org_id = b.donor_org_id
            WHERE b.batch_id = %s
            FOR UPDATE OF b
        """, (batch_id,)).fetchone()
        trace.append({
            "step": "Lock batch row (SELECT ... FOR UPDATE)",
            "sql": "SELECT ... FROM donation_batches b WHERE b.batch_id = %s FOR UPDATE OF b",
            "params": (batch_id,),
            "status": "ok" if batch else "error",
            "rows": 1 if batch else 0,
            "ms": None,
        })
        if not batch:
            reject("Lock batch row", f"Batch #{batch_id} does not exist.")

        allocated = cur.execute("""
            SELECT COALESCE(sum(allocated_kg), 0) AS kg
            FROM batch_allocations
            WHERE batch_id = %s AND status <> 'cancelled'
        """, (batch_id,)).fetchone()["kg"]
        remaining = float(batch["quantity_kg"]) - float(allocated)
        trace.append({
            "step": "Read committed quantity",
            "sql": "SELECT COALESCE(sum(allocated_kg),0) FROM batch_allocations "
                   "WHERE batch_id = %s AND status <> 'cancelled'",
            "params": (batch_id,),
            "status": "ok",
            "rows": None,
            "detail": f"{allocated} kg already committed",
            "ms": None,
        })

        # ---- business validations -------------------------------------
        if mode == "slow_lock":
            step("Hold the lock (pg_sleep 6)", "SELECT pg_sleep(6)")

        if batch["status"] != "available":
            reject("Validate status",
                   f"Batch is '{batch['status']}', not available for claiming.")

        expired = batch["expires_at"] <= datetime.now(timezone.utc)
        if expired or mode == "expired":
            reject("Validate expiry",
                   f"Batch expired at {batch['expires_at']:%Y-%m-%d %H:%M} UTC.")

        requested = float(kg)
        if mode == "over_allocate":
            requested = round(remaining + 25.0, 1)
        if requested <= 0:
            reject("Validate quantity", "Requested quantity must be positive.")
        if requested > remaining + 1e-9:
            reject("Validate quantity",
                   f"Only {remaining:.1f} kg remains, {requested:.1f} kg requested.")

        # ---- 1. allocation --------------------------------------------
        allocation_id = step("INSERT batch_allocations", """
            INSERT INTO batch_allocations (batch_id, recipient_org_id,
                                           allocated_kg, status, note)
            VALUES (%s, %s, %s, 'reserved', %s)
            RETURNING allocation_id
        """, (batch_id, recipient_org_id, requested,
              f"Dispatched by {actor_label}")).fetchone()["allocation_id"]
        summary["allocation_id"] = allocation_id

        # ---- 2. cold chain --------------------------------------------
        probe = 12.5 if mode == "cold_chain" else float(temp_c)
        cold_passed = probe <= 8.0 if batch["needs_refrigeration"] else probe <= 25.0
        step("INSERT cold_chain_checks", """
            INSERT INTO cold_chain_checks (batch_id, checked_by, temp_c, passed, notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (batch_id, actor_id, probe, cold_passed,
              "Within limits" if cold_passed else "Cold chain broken -- dispatch refused"))
        if not cold_passed:
            reject("Cold-chain guard",
                   f"Probe read {probe} C; handover would be unsafe. Rolling back.")

        # ---- 3. certified courier -------------------------------------
        if mode == "no_certified_volunteer":
            row = cur.execute("""
                SELECT u.user_id, u.full_name
                FROM users u
                JOIN roles r ON r.role_id = u.role_id
                WHERE r.role_name = 'Volunteer Courier'
                  AND NOT EXISTS (SELECT 1 FROM volunteer_skills vs
                                  WHERE vs.user_id = u.user_id
                                    AND vs.skill_id = 2
                                    AND vs.expires_on > current_date)
                ORDER BY u.user_id LIMIT 1
            """).fetchone()
            if row:
                volunteer_id = row["user_id"]

        licence = cur.execute("""
            SELECT vs.expires_on
            FROM volunteer_skills vs
            WHERE vs.user_id = %s AND vs.skill_id = 2
        """, (volunteer_id,)).fetchone()
        trace.append({
            "step": "Check courier licence",
            "sql": "SELECT expires_on FROM volunteer_skills WHERE user_id = %s AND skill_id = 2",
            "params": (volunteer_id,),
            "status": "ok" if licence and licence["expires_on"] > datetime.now(timezone.utc).date() else "error",
            "rows": None,
            "detail": f"valid until {licence['expires_on']}" if licence else "no licence on file",
            "ms": None,
        })
        if not licence or licence["expires_on"] <= datetime.now(timezone.utc).date():
            reject("Check courier licence",
                   "Selected courier has no valid C1 licence -- cannot drive a refrigerated van.")

        # ---- 4. delivery run ------------------------------------------
        run_id = step("INSERT delivery_runs", """
            INSERT INTO delivery_runs (run_date, vehicle_id, lead_user_id,
                                       status, started_at, notes)
            VALUES (current_date, %s, %s, 'planned', now(), %s)
            RETURNING run_id
        """, (vehicle_id, actor_id, f"Dispatch for allocation {allocation_id}")
        ).fetchone()["run_id"]
        summary["run_id"] = run_id

        step("INSERT run_batches", """
            INSERT INTO run_batches (run_id, batch_id, sequence_no)
            SELECT %s, %s, COALESCE(max(sequence_no), 0) + 1
            FROM run_batches WHERE run_id = %s
        """, (run_id, batch_id, run_id))

        # ---- 5. crew ---------------------------------------------------
        step("INSERT shift_assignments", """
            INSERT INTO shift_assignments (run_id, user_id, role_on_run, hours_logged)
            VALUES (%s, %s, 'driver', 2.0)
        """, (run_id, volunteer_id))

        # ---- 6. batch status ------------------------------------------
        new_status = "teleported" if mode == "late_failure" else "claimed"
        step("UPDATE donation_batches", """
            UPDATE donation_batches SET status = %s
            WHERE batch_id = %s AND status = 'available'
        """, (new_status, batch_id))

        # ---- 7. audit --------------------------------------------------
        step("INSERT audit_log", """
            INSERT INTO audit_log (actor_user_id, action, entity, entity_id, details)
            VALUES (%s, 'batch.dispatch', 'batch_allocations', %s, %s::jsonb)
        """, (actor_id, str(allocation_id),
              f'{{"batch_id": {batch_id}, "kg": {requested}, '
              f'"recipient_org_id": {recipient_org_id}, "run_id": {run_id}}}'))

        step("COMMIT", "COMMIT")
        ok = True
        summary["remaining_kg"] = round(remaining - requested, 2)
        summary["requested_kg"] = requested
        summary["batch_id"] = batch_id
        summary["mode"] = mode
    except (DispatchError, psycopg.Error) as exc:
        error = str(exc).strip().splitlines()[0]
        try:
            started = time.perf_counter()
            cur.execute("ROLLBACK")
            trace.append({
                "step": "ROLLBACK",
                "sql": "ROLLBACK",
                "status": "ok",
                "rows": 0,
                "ms": round((time.perf_counter() - started) * 1000, 2),
            })
        except psycopg.Error as rollback_exc:
            trace.append({"step": "ROLLBACK", "status": "error",
                          "error": str(rollback_exc), "sql": "ROLLBACK"})
    finally:
        counts_after = _counts(cur)
        conn.close()

    return {
        "ok": ok,
        "error": error,
        "trace": trace,
        "summary": summary,
        "counts": {
            table: (counts_before[table], counts_after[table])
            for table in AFFECTED_TABLES
        },
        "mode": mode,
        "mode_label": FAILURE_MODES.get(mode, mode),
    }
