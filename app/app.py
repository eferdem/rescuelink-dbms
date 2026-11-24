"""
RescueLink web application (Flask + psycopg, plain HTML, no ORM).

Every database call in this file is a parameterised raw SQL statement.
Routes are grouped by the role that may use them:

    /login /switch /logout                 session + demo role switcher
    /                                      role-specific dashboard
    /batches /batches/new /batches/<id>    donor CRUD (create/read/update/withdraw)
    /claims                                coordinator view of claimable batches
    /transaction                           interactive ACID transaction demo
    /runs /runs/<id>                       dispatch planning
    /handovers/<allocation_id>             signed delivery receipt (transaction)
    /admin/...                             organisations, users, vehicles, taxonomy
    /analytics                             the SQL query suite
    /performance                           EXPLAIN ANALYZE before/after report
    /audit                                 audit trail
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
import markdown
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from psycopg.types.json import Json
from psycopg.rows import dict_row
from werkzeug.security import check_password_hash, generate_password_hash

import auth
import db
from auth import ROLE_LABELS, login_required, roles_required
from dispatch import FAILURE_MODES, run_dispatch
from sql_loader import load_queries

ROOT = Path(__file__).resolve().parents[1]
QUERY_FILE = ROOT / "sql" / "04_queries.sql"
PERF_SUMMARY = ROOT / "docs" / "explain_summary.md"

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "rescuelink-dev-secret-key"),
    DEMO_MODE=os.environ.get("DEMO_MODE", "1") == "1",
)

db.init_app(app)
auth.init_app(app)

QUERIES = {q.name: q for q in load_queries(QUERY_FILE)}
ANALYTICS_ORDER = [
    "at_risk_pipeline",
    "donor_leaderboard",
    "monthly_growth",
    "underserved_recipients",
    "dormant_donors",
    "fill_rate",
    "spoilage_by_category",
    "volunteer_leaderboard",
    "top_donors_per_district",
]

DEMO_USERS = [
    ("admin@rescuelink.org", "Platform Admin"),
    ("donor@rescuelink.org", "Donor Staff"),
    ("coordinator@rescuelink.org", "Recipient Coordinator"),
    ("courier@rescuelink.org", "Volunteer Courier"),
]

# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------


@app.template_filter("kg")
def fmt_kg(value):
    if value is None:
        return "-"
    return f"{float(value):,.1f}"


@app.template_filter("num")
def fmt_num(value):
    if value is None:
        return "-"
    return f"{value:,}"


@app.template_filter("dt")
def fmt_dt(value):
    if value is None:
        return "-"
    return value.strftime("%d %b %Y, %H:%M")


@app.template_filter("d")
def fmt_date(value):
    if value is None:
        return "-"
    return value.strftime("%d %b %Y")


@app.template_filter("cell")
def fmt_cell(value):
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y %H:%M")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, Decimal):
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    return value


@app.context_processor
def inject_globals():
    return {
        "ROLE_LABELS": ROLE_LABELS,
        "DEMO_MODE": app.config["DEMO_MODE"],
        "DEMO_USERS": DEMO_USERS,
        "now": datetime.now(timezone.utc),
        "timedelta": timedelta,
    }


def audit(action: str, entity: str, entity_id, details: dict | None = None) -> None:
    db.execute("""
        INSERT INTO audit_log (actor_user_id, action, entity, entity_id, details)
        VALUES (%s, %s, %s, %s, %s)
    """, (g.user["user_id"], action, entity, str(entity_id), Json(details or {})))


def require_org() -> int:
    if g.user["org_id"] is None:
        abort(403)
    return g.user["org_id"]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@app.route("/login", methods=("GET", "POST"))
def login():
    if g.user is not None:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.query_one("""
            SELECT u.user_id, u.email, u.full_name, u.password_hash, u.is_active,
                   r.role_name
            FROM users u JOIN roles r ON r.role_id = u.role_id
            WHERE lower(u.email) = %s
        """, (email,))
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect e-mail or password.", "error")
        elif not user["is_active"]:
            flash("This account has been deactivated.", "error")
        else:
            session.clear()
            session["user_id"] = user["user_id"]
            db.execute("""
                INSERT INTO audit_log (actor_user_id, action, entity, entity_id, details)
                VALUES (%s, 'login', 'users', %s, %s)
            """, (user["user_id"], str(user["user_id"]),
                  Json({"role": user["role_name"], "via": "password"})))
            return redirect(request.args.get("next") or url_for("dashboard"))

    return render_template("login.html",
                           demo_accounts=db.query("""
                               SELECT u.email, u.full_name, r.role_name,
                                      o.name AS org_name, r.role_id
                               FROM users u
                               JOIN roles r ON r.role_id = u.role_id
                               LEFT JOIN organizations o ON o.org_id = u.org_id
                               WHERE u.email IN (
                                   'admin@rescuelink.org', 'donor@rescuelink.org',
                                   'coordinator@rescuelink.org', 'courier@rescuelink.org')
                               ORDER BY r.role_id
                           """))


@app.route("/switch", methods=("POST",))
def switch_role():
    """Demo-only role switcher -- genuine RBAC still applies afterwards."""
    if not app.config["DEMO_MODE"]:
        abort(404)
    email = request.form.get("email", "")
    user = db.query_one("""
        SELECT u.user_id, u.is_active, r.role_name
        FROM users u JOIN roles r ON r.role_id = u.role_id
        WHERE u.email = %s
    """, (email,))
    if user is None or not user["is_active"]:
        flash("Cannot switch to that account.", "error")
        return redirect(request.referrer or url_for("dashboard"))
    session.clear()
    session["user_id"] = user["user_id"]
    db.execute("""
        INSERT INTO audit_log (actor_user_id, action, entity, entity_id, details)
        VALUES (%s, 'login', 'users', %s, %s)
    """, (user["user_id"], str(user["user_id"]),
          Json({"role": user["role_name"], "via": "demo switcher"})))
    flash(f"Now acting as {user['role_name']}.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=("POST",))
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.route("/")
@login_required
def dashboard():
    role = g.user["role_name"]
    context: dict = {"role": role}

    if role == "Admin":
        context["kpis"] = db.query_one("""
            SELECT count(*)                                        AS batches,
                   count(*) FILTER (WHERE status = 'available')     AS available,
                   count(*) FILTER (WHERE status = 'delivered')     AS delivered,
                   count(*) FILTER (WHERE status = 'spoiled')       AS spoiled,
                   round(sum(quantity_kg) FILTER (WHERE status IN ('delivered','claimed')), 1) AS rescued_kg
            FROM donation_batches
        """)
        context["network"] = db.query_one("""
            SELECT (SELECT count(*) FROM organizations WHERE org_type = 'donor')     AS donors,
                   (SELECT count(*) FROM organizations WHERE org_type = 'recipient') AS recipients,
                   (SELECT count(*) FROM users WHERE is_active)                       AS users,
                   (SELECT count(*) FROM cold_chain_checks
                     WHERE NOT passed AND checked_at > now() - interval '7 days')    AS cold_fails,
                   (SELECT count(*) FROM v_batch_availability
                     WHERE status = 'available' AND remaining_kg > 0
                       AND expires_at < now() + interval '48 hours')                  AS at_risk
        """)
        context["recent_audit"] = db.query("""
            SELECT a.created_at, a.action, a.entity, a.entity_id,
                   u.full_name, r.role_name
            FROM audit_log a
            LEFT JOIN users u ON u.user_id = a.actor_user_id
            LEFT JOIN roles r ON r.role_id = u.role_id
            ORDER BY a.created_at DESC LIMIT 10
        """)

    elif role == "Donor Staff":
        org = require_org()
        context["kpis"] = db.query_one("""
            SELECT count(*)                                              AS batches,
                   count(*) FILTER (WHERE status = 'available')          AS available,
                   count(*) FILTER (WHERE status = 'delivered')          AS delivered,
                   count(*) FILTER (WHERE status = 'spoiled')            AS spoiled,
                   round(sum(quantity_kg) FILTER (WHERE status IN ('delivered','claimed')), 1) AS rescued_kg
            FROM donation_batches WHERE donor_org_id = %s
        """, (org,))
        context["expiring"] = db.query("""
            SELECT * FROM v_batch_availability
            WHERE donor_org_id = %s AND status = 'available' AND remaining_kg > 0
            ORDER BY expires_at LIMIT 6
        """, (org,))
        context["recent_claims"] = db.query("""
            SELECT a.allocation_id, a.allocated_kg, a.status, a.allocated_at,
                   b.item_description, o.name AS recipient
            FROM batch_allocations a
            JOIN donation_batches b ON b.batch_id = a.batch_id
            JOIN organizations o ON o.org_id = a.recipient_org_id
            WHERE b.donor_org_id = %s
            ORDER BY a.allocated_at DESC LIMIT 8
        """, (org,))

    elif role == "Recipient Coordinator":
        org = require_org()
        context["kpis"] = db.query_one("""
            SELECT (SELECT count(*) FROM batch_allocations
                     WHERE recipient_org_id = %s AND status = 'reserved')          AS open_claims,
                   (SELECT round(sum(allocated_kg), 1) FROM batch_allocations
                     WHERE recipient_org_id = %s AND status = 'handed_over')       AS received_kg,
                   (SELECT count(*) FROM delivery_runs
                     WHERE lead_user_id = %s AND status <> 'cancelled')            AS my_runs,
                   (SELECT count(*) FROM v_batch_availability v
                     JOIN org_capabilities c ON c.category_id = v.category_id
                                            AND c.org_id = %s
                     WHERE v.status = 'available' AND v.remaining_kg > 0
                       AND v.expires_at > now())                                   AS claimable
        """, (org, org, g.user["user_id"], org))
        context["claims"] = db.query("""
            SELECT a.allocation_id, a.allocated_kg, a.allocated_at, a.status,
                   b.batch_id, b.item_description, b.expires_at, b.status AS batch_status,
                   d.name AS donor, c.name AS category
            FROM batch_allocations a
            JOIN donation_batches b ON b.batch_id = a.batch_id
            JOIN organizations d ON d.org_id = b.donor_org_id
            JOIN food_categories c ON c.category_id = b.category_id
            WHERE a.recipient_org_id = %s
            ORDER BY a.allocated_at DESC LIMIT 10
        """, (org,))

    elif role == "Volunteer Courier":
        context["kpis"] = db.query_one("""
            SELECT (SELECT count(*) FROM shift_assignments s
                     JOIN delivery_runs r ON r.run_id = s.run_id
                     WHERE s.user_id = %s AND r.status IN ('planned','in_progress')) AS upcoming,
                   (SELECT round(sum(hours_logged), 1) FROM shift_assignments s
                     JOIN delivery_runs r ON r.run_id = s.run_id
                     WHERE s.user_id = %s AND r.status = 'completed'
                       AND r.run_date >= date_trunc('month', current_date))         AS hours_this_month,
                   (SELECT count(*) FROM shift_assignments s
                     JOIN delivery_runs r ON r.run_id = s.run_id
                     WHERE s.user_id = %s AND r.status = 'completed')                AS completed_runs
        """, (g.user["user_id"], g.user["user_id"], g.user["user_id"]))
        context["shifts"] = db.query("""
            SELECT r.run_id, r.run_date, r.status, s.role_on_run, s.hours_logged,
                   v.plate, v.is_refrigerated, u.full_name AS lead
            FROM shift_assignments s
            JOIN delivery_runs r ON r.run_id = s.run_id
            JOIN vehicles v ON v.vehicle_id = r.vehicle_id
            JOIN users u ON u.user_id = r.lead_user_id
            WHERE s.user_id = %s AND r.status IN ('planned','in_progress')
            ORDER BY r.run_date LIMIT 8
        """, (g.user["user_id"],))

    return render_template("dashboard.html", **context)


# ---------------------------------------------------------------------------
# Donor CRUD: batches
# ---------------------------------------------------------------------------


def _batch_scope() -> int | None:
    """Admin sees everything; everybody else only their own organisation."""
    return None if g.user["role_name"] == "Admin" else g.user["org_id"]


@app.route("/batches")
@roles_required("Donor Staff", "Admin")
def batches():
    scope = _batch_scope()
    donor_filter = request.args.get("donor", type=int)
    effective = scope if scope is not None else donor_filter
    rows = db.query("""
        SELECT v.*, d.district,
               (SELECT count(*) FROM batch_allocations a
                 WHERE a.batch_id = v.batch_id AND a.status <> 'cancelled') AS allocations
        FROM v_batch_availability v
        JOIN organizations d ON d.org_id = v.donor_org_id
        WHERE (%s::int IS NULL OR v.donor_org_id = %s)
        ORDER BY v.created_at DESC
        LIMIT 200
    """, (effective, effective))
    donors = db.query("""
        SELECT org_id, name FROM organizations
        WHERE org_type = 'donor' AND is_active ORDER BY name
    """) if g.user["role_name"] == "Admin" else []
    return render_template("batches.html", rows=rows, donors=donors,
                           donor_filter=donor_filter)


@app.route("/batches/new", methods=("GET", "POST"))
@roles_required("Donor Staff")
def batch_new():
    org = require_org()
    if request.method == "POST":
        try:
            params = _batch_form_values(org)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("batch_new"))
        batch_id = db.query_one("""
            INSERT INTO donation_batches
                (donor_org_id, category_id, item_description, quantity_kg,
                 prepared_at, expires_at, storage_temp_c, status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'available', %s)
            RETURNING batch_id
        """, params + (g.user["user_id"],))["batch_id"]
        audit("batch.create", "donation_batches", batch_id,
              {"donor_org_id": org, "kg": float(request.form["quantity_kg"])})
        flash("Batch published. Coordinators can now claim it.", "ok")
        return redirect(url_for("batches"))
    return render_template("batch_form.html", batch=None,
                           categories=_categories())


@app.route("/batches/<int:batch_id>/edit", methods=("GET", "POST"))
@roles_required("Donor Staff", "Admin")
def batch_edit(batch_id: int):
    batch = db.query_one("""
        SELECT * FROM donation_batches WHERE batch_id = %s
    """, (batch_id,))
    if batch is None:
        abort(404)
    scope = _batch_scope()
    if scope is not None and batch["donor_org_id"] != scope:
        abort(403)

    if request.method == "POST":
        if batch["status"] != "available":
            flash("Only batches that are still available can be edited.", "error")
            return redirect(url_for("batches"))
        org = batch["donor_org_id"]
        try:
            values = _batch_form_values(org)
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("batch_edit", batch_id=batch_id))
        allocated = db.query_one("""
            SELECT COALESCE(sum(allocated_kg), 0) AS kg FROM batch_allocations
            WHERE batch_id = %s AND status <> 'cancelled'
        """, (batch_id,))["kg"]
        if values[3] < float(allocated):
            flash(f"Cannot shrink below the {allocated} kg already claimed.", "error")
            return redirect(url_for("batch_edit", batch_id=batch_id))
        db.execute("""
            UPDATE donation_batches
               SET category_id = %s, item_description = %s, quantity_kg = %s,
                   prepared_at = %s, expires_at = %s, storage_temp_c = %s
             WHERE batch_id = %s
        """, (values[1], values[2], values[3], values[4], values[5], values[6], batch_id))
        audit("batch.update", "donation_batches", batch_id,
              {"quantity_kg": float(values[3])})
        flash("Batch updated.", "ok")
        return redirect(url_for("batches"))

    return render_template("batch_form.html", batch=batch, categories=_categories())


@app.route("/batches/<int:batch_id>/withdraw", methods=("POST",))
@roles_required("Donor Staff", "Admin")
def batch_withdraw(batch_id: int):
    scope = _batch_scope()
    changed = db.execute("""
        UPDATE donation_batches
           SET status = 'withdrawn'
         WHERE batch_id = %s AND status = 'available'
           AND (%s::int IS NULL OR donor_org_id = %s)
    """, (batch_id, scope, scope))
    if changed:
        audit("batch.withdraw", "donation_batches", batch_id, {"reason": "donor withdrew"})
        flash("Batch withdrawn. It can no longer be claimed.", "ok")
    else:
        flash("Only available batches can be withdrawn.", "error")
    return redirect(url_for("batches"))


def _categories() -> list[dict]:
    return db.query("""
        SELECT category_id, name, needs_refrigeration, shelf_life_days, kg_per_meal
        FROM food_categories ORDER BY name
    """)


def _batch_form_values(org: int) -> tuple:
    description = request.form.get("item_description", "").strip()
    category_id = request.form.get("category_id", type=int)
    quantity = request.form.get("quantity_kg", type=float)
    prepared_raw = request.form.get("prepared_at", "")
    expires_raw = request.form.get("expires_at", "")
    temp = request.form.get("storage_temp_c", type=float)

    if not description or category_id is None or quantity is None or temp is None:
        raise ValueError("Description, category, quantity and temperature are required.")
    if quantity <= 0:
        raise ValueError("Quantity must be greater than zero.")
    try:
        prepared = datetime.fromisoformat(prepared_raw).replace(tzinfo=timezone.utc)
        expires = datetime.fromisoformat(expires_raw).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("Dates must be valid date-time values.") from exc
    if expires <= prepared:
        raise ValueError("Expiry must be after the preparation time.")
    return (org, category_id, description, quantity, prepared, expires, temp)


# ---------------------------------------------------------------------------
# Recipient coordinator: claims
# ---------------------------------------------------------------------------


@app.route("/claims")
@roles_required("Recipient Coordinator")
def claims():
    org = require_org()
    matching = db.query("""
        SELECT v.*, c.max_weekly_kg
        FROM v_batch_availability v
        JOIN org_capabilities c ON c.category_id = v.category_id
                               AND c.org_id = %s
        WHERE v.status = 'available' AND v.remaining_kg > 0
          AND v.expires_at > now()
        ORDER BY v.expires_at
        LIMIT 60
    """, (org,))
    mine = db.query("""
        SELECT a.allocation_id, a.allocated_kg, a.allocated_at, a.status,
               b.batch_id, b.item_description, b.expires_at,
               d.name AS donor, c.name AS category,
               h.condition
        FROM batch_allocations a
        JOIN donation_batches b ON b.batch_id = a.batch_id
        JOIN organizations d ON d.org_id = b.donor_org_id
        JOIN food_categories c ON c.category_id = b.category_id
        LEFT JOIN handovers h ON h.allocation_id = a.allocation_id
        WHERE a.recipient_org_id = %s
        ORDER BY a.allocated_at DESC
        LIMIT 40
    """, (org,))
    return render_template("claims.html", matching=matching, mine=mine)


# ---------------------------------------------------------------------------
# The ACID transaction demo
# ---------------------------------------------------------------------------


def _tx_lookups() -> dict:
    volunteers = db.query("""
        SELECT u.user_id, u.full_name, o.name AS org_name,
               vs.expires_on,
               (vs.expires_on > current_date) AS eligible
        FROM users u
        JOIN roles r ON r.role_id = u.role_id AND r.role_name = 'Volunteer Courier'
        LEFT JOIN organizations o ON o.org_id = u.org_id
        LEFT JOIN volunteer_skills vs ON vs.user_id = u.user_id AND vs.skill_id = 2
        WHERE u.is_active
        ORDER BY eligible DESC NULLS LAST, u.full_name
    """)
    vehicles = db.query("""
        SELECT vehicle_id, plate, capacity_kg, is_refrigerated
        FROM vehicles WHERE is_active ORDER BY is_refrigerated DESC, capacity_kg DESC
    """)
    recipients = db.query("""
        SELECT org_id, name FROM organizations
        WHERE org_type = 'recipient' AND is_active ORDER BY name
    """)
    batches = db.query("""
        SELECT batch_id, donor_name, category_name, remaining_kg, expires_at,
               needs_refrigeration
        FROM v_batch_availability
        WHERE status = 'available' AND remaining_kg > 0 AND expires_at > now()
        ORDER BY expires_at LIMIT 40
    """)
    return {"volunteers": volunteers, "vehicles": vehicles,
            "recipients": recipients, "batches": batches}


@app.route("/transaction", methods=("GET", "POST"))
@roles_required("Recipient Coordinator", "Admin")
def transaction():
    lookups = _tx_lookups()
    selected_batch = request.args.get("batch_id", type=int)

    if request.method == "POST":
        batch_id = request.form.get("batch_id", type=int)
        kg = request.form.get("kg", type=float, default=0)
        volunteer_id = request.form.get("volunteer_id", type=int)
        vehicle_id = request.form.get("vehicle_id", type=int)
        temp_c = request.form.get("temp_c", type=float, default=3.5)
        mode = request.form.get("mode", "none")

        if g.user["role_name"] == "Admin":
            recipient_org_id = request.form.get("recipient_org_id", type=int)
        else:
            recipient_org_id = g.user["org_id"]

        if not (batch_id and kg and volunteer_id and vehicle_id and recipient_org_id):
            flash("Batch, quantity, cook/courier and vehicle are all required.", "error")
            return redirect(url_for("transaction"))

        result = run_dispatch(
            batch_id=batch_id,
            recipient_org_id=recipient_org_id,
            kg=kg,
            volunteer_id=volunteer_id,
            vehicle_id=vehicle_id,
            temp_c=temp_c,
            mode=mode,
            actor_id=g.user["user_id"],
            actor_label=g.user["full_name"],
        )
        return render_template("transaction.html", lookups=lookups,
                               result=result, selected_batch=batch_id,
                               values=request.form,
                               failure_modes=FAILURE_MODES)

    return render_template("transaction.html", lookups=lookups, result=None,
                           selected_batch=selected_batch, values={},
                           failure_modes=FAILURE_MODES)


# ---------------------------------------------------------------------------
# Dispatch runs
# ---------------------------------------------------------------------------


@app.route("/runs")
@roles_required("Recipient Coordinator", "Admin", "Volunteer Courier")
def runs():
    if g.user["role_name"] == "Volunteer Courier":
        rows = db.query("""
            SELECT s.run_id, s.run_date, s.status, s.batch_count, s.crew_size,
                   s.carried_kg, s.plate, s.is_refrigerated, s.lead_name,
                   sa.role_on_run
            FROM v_run_summary s
            JOIN shift_assignments sa ON sa.run_id = s.run_id
            WHERE sa.user_id = %s
            ORDER BY s.run_date DESC LIMIT 60
        """, (g.user["user_id"],))
    elif g.user["role_name"] == "Admin":
        rows = db.query("""
            SELECT * FROM v_run_summary ORDER BY run_date DESC LIMIT 100
        """)
    else:
        rows = db.query("""
            SELECT * FROM v_run_summary
            WHERE lead_name IN (SELECT full_name FROM users WHERE org_id = %s)
            ORDER BY run_date DESC LIMIT 100
        """, (g.user["org_id"],))
    vehicles = db.query("""
        SELECT vehicle_id, plate, capacity_kg, is_refrigerated
        FROM vehicles WHERE is_active ORDER BY plate
    """) if g.user["role_name"] != "Volunteer Courier" else []
    return render_template("runs.html", rows=rows, vehicles=vehicles)


@app.route("/runs/new", methods=("POST",))
@roles_required("Recipient Coordinator", "Admin")
def run_new():
    vehicle_id = request.form.get("vehicle_id", type=int)
    run_date = request.form.get("run_date") or datetime.now(timezone.utc).date().isoformat()
    notes = request.form.get("notes", "").strip() or None
    if not vehicle_id:
        flash("Choose a vehicle.", "error")
        return redirect(url_for("runs"))
    run_id = db.query_one("""
        INSERT INTO delivery_runs (run_date, vehicle_id, lead_user_id, status, notes)
        VALUES (%s, %s, %s, 'planned', %s)
        RETURNING run_id
    """, (run_date, vehicle_id, g.user["user_id"], notes))["run_id"]
    audit("run.create", "delivery_runs", run_id, {"vehicle_id": vehicle_id})
    flash(f"Run #{run_id} created.", "ok")
    return redirect(url_for("run_detail", run_id=run_id))


@app.route("/runs/<int:run_id>")
@roles_required("Recipient Coordinator", "Admin", "Volunteer Courier")
def run_detail(run_id: int):
    run = db.query_one("""
        SELECT r.*, v.plate, v.capacity_kg, v.is_refrigerated,
               u.full_name AS lead_name, o.name AS lead_org
        FROM delivery_runs r
        JOIN vehicles v ON v.vehicle_id = r.vehicle_id
        JOIN users u ON u.user_id = r.lead_user_id
        LEFT JOIN organizations o ON o.org_id = u.org_id
        WHERE r.run_id = %s
    """, (run_id,))
    if run is None:
        abort(404)
    crew = db.query("""
        SELECT s.user_id, s.role_on_run, s.hours_logged, u.full_name, u.email
        FROM shift_assignments s JOIN users u ON u.user_id = s.user_id
        WHERE s.run_id = %s ORDER BY s.role_on_run, u.full_name
    """, (run_id,))
    batches = db.query("""
        SELECT rb.sequence_no, rb.batch_id, b.item_description, b.quantity_kg,
               b.status, b.expires_at, o.name AS donor, c.name AS category
        FROM run_batches rb
        JOIN donation_batches b ON b.batch_id = rb.batch_id
        JOIN organizations o ON o.org_id = b.donor_org_id
        JOIN food_categories c ON c.category_id = b.category_id
        WHERE rb.run_id = %s ORDER BY rb.sequence_no
    """, (run_id,))
    candidates = db.query("""
        SELECT batch_id, donor_name, item_description, remaining_kg, expires_at
        FROM v_batch_availability
        WHERE status = 'available' AND remaining_kg > 0
        ORDER BY expires_at LIMIT 25
    """)
    volunteers = db.query("""
        SELECT u.user_id, u.full_name
        FROM users u JOIN roles r ON r.role_id = u.role_id
        WHERE r.role_name = 'Volunteer Courier' AND u.is_active
          AND NOT EXISTS (SELECT 1 FROM shift_assignments s
                          WHERE s.run_id = %s AND s.user_id = u.user_id)
        ORDER BY u.full_name
    """, (run_id,))
    return render_template("run_detail.html", run=run, crew=crew, batches=batches,
                           candidates=candidates, volunteers=volunteers)


@app.route("/runs/<int:run_id>/batch", methods=("POST",))
@roles_required("Recipient Coordinator", "Admin")
def run_add_batch(run_id: int):
    batch_id = request.form.get("batch_id", type=int)
    try:
        db.execute("""
            INSERT INTO run_batches (run_id, batch_id, sequence_no)
            SELECT %s, %s, COALESCE(max(sequence_no), 0) + 1
            FROM run_batches WHERE run_id = %s
        """, (run_id, batch_id, run_id))
        audit("run.assign", "delivery_runs", run_id, {"batch_id": batch_id})
        flash("Batch added to the route.", "ok")
    except psycopg.errors.UniqueViolation:
        flash("That batch is already on this run.", "error")
    except psycopg.errors.ForeignKeyViolation:
        flash("Batch does not exist.", "error")
    return redirect(url_for("run_detail", run_id=run_id))


@app.route("/runs/<int:run_id>/crew", methods=("POST",))
@roles_required("Recipient Coordinator", "Admin")
def run_add_crew(run_id: int):
    user_id = request.form.get("user_id", type=int)
    role_on_run = request.form.get("role_on_run", "loader")
    try:
        db.execute("""
            INSERT INTO shift_assignments (run_id, user_id, role_on_run, hours_logged)
            VALUES (%s, %s, %s, 0)
            ON CONFLICT (run_id, user_id) DO NOTHING
        """, (run_id, user_id, role_on_run))
        audit("run.assign", "delivery_runs", run_id, {"user_id": user_id})
        flash("Crew member assigned.", "ok")
    except psycopg.errors.CheckViolation:
        flash("Invalid crew role.", "error")
    return redirect(url_for("run_detail", run_id=run_id))


@app.route("/runs/<int:run_id>/complete", methods=("POST",))
@roles_required("Recipient Coordinator", "Admin")
def run_complete(run_id: int):
    total_km = request.form.get("total_km", type=float, default=0)
    changed = db.execute("""
        UPDATE delivery_runs
           SET status = 'completed', completed_at = now(), total_km = %s
         WHERE run_id = %s AND status <> 'completed'
    """, (total_km, run_id))
    if changed:
        audit("run.complete", "delivery_runs", run_id, {"total_km": total_km})
        flash("Run marked completed.", "ok")
    else:
        flash("Run was already completed.", "error")
    return redirect(url_for("run_detail", run_id=run_id))


# ---------------------------------------------------------------------------
# Handover (a second, smaller multi-table transaction)
# ---------------------------------------------------------------------------


@app.route("/handovers/<int:allocation_id>", methods=("POST",))
@roles_required("Recipient Coordinator", "Admin")
def handover(allocation_id: int):
    received = request.form.get("received_kg", type=float, default=0)
    condition = request.form.get("condition", "good")
    signed_by = request.form.get("received_by_name", "").strip() or g.user["full_name"]

    conn = psycopg.connect(db.dsn(), autocommit=True, row_factory=dict_row)
    cur = conn.cursor()
    try:
        cur.execute("BEGIN")
        allocation = cur.execute("""
            SELECT a.allocation_id, a.batch_id, a.allocated_kg, a.recipient_org_id,
                   a.status, b.status AS batch_status
            FROM batch_allocations a
            JOIN donation_batches b ON b.batch_id = a.batch_id
            WHERE a.allocation_id = %s
            FOR UPDATE OF a
        """, (allocation_id,)).fetchone()
        if allocation is None:
            raise ValueError("Allocation not found.")
        if g.user["role_name"] != "Admin" and allocation["recipient_org_id"] != g.user["org_id"]:
            raise ValueError("This allocation belongs to another organisation.")
        if allocation["status"] != "reserved":
            raise ValueError("Only reserved allocations can be signed for.")
        if received < 0 or received > float(allocation["allocated_kg"]):
            raise ValueError("Received kg must be between 0 and the allocated amount.")

        cur.execute("""
            INSERT INTO handovers (allocation_id, received_by_name, received_kg,
                                   condition, notes)
            VALUES (%s, %s, %s, %s, %s)
        """, (allocation_id, signed_by, received, condition,
              request.form.get("notes", "").strip() or None))
        cur.execute("""
            UPDATE batch_allocations SET status = 'handed_over'
            WHERE allocation_id = %s
        """, (allocation_id,))
        cur.execute("""
            UPDATE donation_batches b
               SET status = 'delivered'
             WHERE b.batch_id = %s
               AND NOT EXISTS (SELECT 1 FROM batch_allocations a
                               WHERE a.batch_id = b.batch_id
                                 AND a.status = 'reserved')
        """, (allocation["batch_id"],))
        cur.execute("""
            INSERT INTO audit_log (actor_user_id, action, entity, entity_id, details)
            VALUES (%s, 'handover.sign', 'handovers', %s, %s)
        """, (g.user["user_id"], str(allocation_id),
              Json({"received_kg": received, "condition": condition})))
        cur.execute("COMMIT")
        flash(f"Handover signed: {received} kg received ({condition}).", "ok")
    except (ValueError, psycopg.Error) as exc:
        conn.execute("ROLLBACK")
        flash(f"Handover rolled back: {str(exc).strip().splitlines()[0]}", "error")
    finally:
        conn.close()
    return redirect(url_for("claims"))


# ---------------------------------------------------------------------------
# Admin: organisations, users, vehicles, taxonomy
# ---------------------------------------------------------------------------


@app.route("/admin/organizations", methods=("GET", "POST"))
@roles_required("Admin")
def admin_organizations():
    if request.method == "POST":
        form = request.form
        try:
            org_id = db.query_one("""
                INSERT INTO organizations (name, org_type, tax_id, address, district,
                                           contact_email, phone, cold_storage_kg, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
                RETURNING org_id
            """, (form["name"].strip(), form["org_type"], form["tax_id"].strip(),
                  form["address"].strip(), form["district"].strip(),
                  form["contact_email"].strip(), form["phone"].strip(),
                  form.get("cold_storage_kg", type=float, default=0)))["org_id"]
            audit("org.create", "organizations", org_id, {"name": form["name"]})
            flash("Organisation created.", "ok")
        except psycopg.errors.UniqueViolation:
            flash("An organisation with that name or tax id already exists.", "error")
        except (KeyError, psycopg.errors.NotNullViolation):
            flash("All fields except storage capacity are required.", "error")
        return redirect(url_for("admin_organizations"))

    orgs = db.query("""
        SELECT o.*,
               (SELECT count(*) FROM donation_batches b WHERE b.donor_org_id = o.org_id)      AS batches,
               (SELECT count(*) FROM batch_allocations a WHERE a.recipient_org_id = o.org_id) AS claims,
               (SELECT count(*) FROM users u WHERE u.org_id = o.org_id)                       AS staff
        FROM organizations o ORDER BY o.org_type, o.name
    """)
    return render_template("admin_organizations.html", orgs=orgs)


@app.route("/admin/organizations/<int:org_id>/edit", methods=("GET", "POST"))
@roles_required("Admin")
def admin_org_edit(org_id: int):
    org = db.query_one("SELECT * FROM organizations WHERE org_id = %s", (org_id,))
    if org is None:
        abort(404)
    if request.method == "POST":
        form = request.form
        try:
            db.execute("""
                UPDATE organizations
                   SET name = %s, org_type = %s, tax_id = %s, address = %s,
                       district = %s, contact_email = %s, phone = %s,
                       cold_storage_kg = %s, is_active = %s
                 WHERE org_id = %s
            """, (form["name"].strip(), form["org_type"], form["tax_id"].strip(),
                  form["address"].strip(), form["district"].strip(),
                  form["contact_email"].strip(), form["phone"].strip(),
                  form.get("cold_storage_kg", type=float, default=0),
                  form.get("is_active") == "on", org_id))
            audit("org.update", "organizations", org_id, {"name": form["name"]})
            flash("Organisation updated.", "ok")
            return redirect(url_for("admin_organizations"))
        except psycopg.errors.UniqueViolation:
            flash("Name and tax id must stay unique.", "error")
    return render_template("admin_org_form.html", org=org)


@app.route("/admin/organizations/<int:org_id>/delete", methods=("POST",))
@roles_required("Admin")
def admin_org_delete(org_id: int):
    try:
        changed = db.execute("DELETE FROM organizations WHERE org_id = %s", (org_id,))
        if changed:
            audit("org.delete", "organizations", org_id, {})
            flash("Organisation deleted.", "ok")
        else:
            flash("Organisation not found.", "error")
    except psycopg.errors.ForeignKeyViolation:
        flash("Cannot delete: this partner already has batches, claims or staff. "
              "Deactivate it instead.", "error")
    return redirect(url_for("admin_organizations"))


@app.route("/admin/categories", methods=("GET", "POST"))
@roles_required("Admin")
def admin_categories():
    if request.method == "POST":
        form = request.form
        try:
            category_id = db.query_one("""
                INSERT INTO food_categories (name, needs_refrigeration,
                                             shelf_life_days, kg_per_meal)
                VALUES (%s, %s, %s, %s)
                RETURNING category_id
            """, (form["name"].strip(), form.get("needs_refrigeration") == "on",
                  form.get("shelf_life_days", type=int, default=3),
                  form.get("kg_per_meal", type=float, default=0.4)))["category_id"]
            audit("category.create", "food_categories", category_id, {"name": form["name"]})
            flash("Category created.", "ok")
        except psycopg.errors.UniqueViolation:
            flash("That category already exists.", "error")
        except psycopg.errors.CheckViolation:
            flash("Shelf life and kg per meal must be positive.", "error")
        return redirect(url_for("admin_categories"))

    rows = db.query("""
        SELECT c.*,
               (SELECT count(*) FROM donation_batches b WHERE b.category_id = c.category_id) AS batches,
               (SELECT count(*) FROM org_capabilities g WHERE g.category_id = c.category_id) AS recipients
        FROM food_categories c ORDER BY c.name
    """)
    return render_template("admin_categories.html", rows=rows)


@app.route("/admin/users", methods=("GET", "POST"))
@roles_required("Admin")
def admin_users():
    if request.method == "POST":
        form = request.form
        role_id = form.get("role_id", type=int)
        org_id = form.get("org_id", type=int) or None
        try:
            user_id = db.query_one("""
                INSERT INTO users (email, password_hash, full_name, phone,
                                   role_id, org_id, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, true)
                RETURNING user_id
            """, (form["email"].strip().lower(),
                  generate_password_hash(form["password"]),
                  form["full_name"].strip(), form.get("phone", "").strip() or "-",
                  role_id, org_id))["user_id"]
            audit("user.create", "users", user_id, {"email": form["email"]})
            flash("User created.", "ok")
        except psycopg.errors.UniqueViolation:
            flash("E-mail already registered.", "error")
        except psycopg.errors.CheckViolation:
            flash("Partner roles must be attached to an organisation.", "error")
        return redirect(url_for("admin_users"))

    users = db.query("""
        SELECT u.user_id, u.email, u.full_name, u.is_active, r.role_name,
               COALESCE(o.name, '-') AS org_name
        FROM users u JOIN roles r ON r.role_id = u.role_id
        LEFT JOIN organizations o ON o.org_id = u.org_id
        ORDER BY r.role_id, u.full_name
    """)
    roles = db.query("SELECT role_id, role_name FROM roles ORDER BY role_id")
    orgs = db.query("SELECT org_id, name, org_type FROM organizations ORDER BY name")
    return render_template("admin_users.html", users=users, roles=roles, orgs=orgs)


@app.route("/admin/users/<int:user_id>/toggle", methods=("POST",))
@roles_required("Admin")
def admin_user_toggle(user_id: int):
    if user_id == g.user["user_id"]:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("admin_users"))
    changed = db.execute("""
        UPDATE users SET is_active = NOT is_active WHERE user_id = %s
    """, (user_id,))
    if changed:
        audit("user.toggle", "users", user_id, {})
        flash("Account status changed.", "ok")
    return redirect(url_for("admin_users"))


@app.route("/admin/vehicles", methods=("GET", "POST"))
@roles_required("Admin")
def admin_vehicles():
    if request.method == "POST":
        form = request.form
        try:
            vehicle_id = db.query_one("""
                INSERT INTO vehicles (plate, capacity_kg, is_refrigerated,
                                      owner_org_id, is_active)
                VALUES (%s, %s, %s, %s, true)
                RETURNING vehicle_id
            """, (form["plate"].strip().upper(),
                  form.get("capacity_kg", type=float, default=350),
                  form.get("is_refrigerated") == "on",
                  form.get("owner_org_id", type=int) or None))["vehicle_id"]
            audit("vehicle.create", "vehicles", vehicle_id, {"plate": form["plate"]})
            flash("Vehicle added.", "ok")
        except psycopg.errors.UniqueViolation:
            flash("That plate is already registered.", "error")
        except psycopg.errors.CheckViolation:
            flash("Capacity must be positive.", "error")
        return redirect(url_for("admin_vehicles"))

    rows = db.query("""
        SELECT v.*, o.name AS owner,
               (SELECT count(*) FROM delivery_runs r WHERE r.vehicle_id = v.vehicle_id) AS runs
        FROM vehicles v LEFT JOIN organizations o ON o.org_id = v.owner_org_id
        ORDER BY v.plate
    """)
    orgs = db.query("SELECT org_id, name FROM organizations WHERE is_active ORDER BY name")
    return render_template("admin_vehicles.html", rows=rows, orgs=orgs)


@app.route("/admin/vehicles/<int:vehicle_id>/toggle", methods=("POST",))
@roles_required("Admin")
def admin_vehicle_toggle(vehicle_id: int):
    db.execute("UPDATE vehicles SET is_active = NOT is_active WHERE vehicle_id = %s",
               (vehicle_id,))
    audit("vehicle.toggle", "vehicles", vehicle_id, {})
    flash("Vehicle availability changed.", "ok")
    return redirect(url_for("admin_vehicles"))


# ---------------------------------------------------------------------------
# Analytics + performance evidence
# ---------------------------------------------------------------------------


@app.route("/analytics")
@roles_required("Admin", "Recipient Coordinator")
def analytics():
    results = []
    for name in ANALYTICS_ORDER:
        q = QUERIES.get(name)
        if q is None:
            continue
        started = time.perf_counter()
        error = None
        rows: list[dict] = []
        columns: list[str] = []
        try:
            cur = db.get_db().execute(q.sql)
            rows = cur.fetchall()
            columns = [d.name for d in cur.description]
        except psycopg.Error as exc:
            error = str(exc).strip().splitlines()[0]
        elapsed = (time.perf_counter() - started) * 1000
        results.append({
            "name": name,
            "title": q.title,
            "techniques": q.techniques,
            "description": q.description,
            "sql": q.sql,
            "columns": columns,
            "rows": rows[:60],
            "row_count": len(rows),
            "ms": round(elapsed, 2),
            "error": error,
        })
    return render_template("analytics.html", results=results)


@app.route("/performance")
@login_required
def performance():
    raw = PERF_SUMMARY.read_text(encoding="utf-8") if PERF_SUMMARY.exists() else \
        "Run `py -3.11 sql/06_perf_test.py` to generate the benchmark."
    # drop the markdown title: the page already has its own heading
    if raw.startswith("# "):
        raw = raw.split("\n", 1)[-1]
    summary = markdown.markdown(raw, extensions=["tables"])
    before = (ROOT / "docs" / "explain_before.txt")
    after = (ROOT / "docs" / "explain_after.txt")
    return render_template(
        "performance.html",
        summary=summary,
        before=before.read_text(encoding="utf-8") if before.exists() else "",
        after=after.read_text(encoding="utf-8") if after.exists() else "",
    )


@app.route("/audit")
@roles_required("Admin")
def audit_log():
    rows = db.query("""
        SELECT a.audit_id, a.created_at, a.action, a.entity, a.entity_id,
               a.details, u.full_name, r.role_name
        FROM audit_log a
        LEFT JOIN users u ON u.user_id = a.actor_user_id
        LEFT JOIN roles r ON r.role_id = u.role_id
        ORDER BY a.created_at DESC, a.audit_id DESC
        LIMIT 150
    """)
    return render_template("audit.html", rows=rows)


# ---------------------------------------------------------------------------
# Error pages
# ---------------------------------------------------------------------------


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", code=403,
                           message="Your role does not have access to this page."), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404,
                           message="That record or page does not exist."), 404


if __name__ == "__main__":
    app.run(
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=int(os.environ.get("FLASK_PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "1") == "1",
    )
