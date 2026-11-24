#!/usr/bin/env python3
"""
Captures the report screenshots with headless Microsoft Edge.

The Flask app must be running on http://127.0.0.1:5000.  The script logs in
through the real /login form (cookie jar per role), fetches the rendered
HTML, and lets Edge render it against the live CSS.  Nothing is mocked: the
pages shown are the actual application.

Usage:  py -3.11 scripts/screenshots.py
"""

from __future__ import annotations

import http.cookiejar
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from db import dsn  # noqa: E402

BASE = os.environ.get("APP_URL", "http://127.0.0.1:5000")
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
OUT = ROOT / "docs" / "screenshots"
TMP = Path(tempfile.mkdtemp(prefix="rescuelink_shots_"))
PASSWORD = "Demo1234!"


def session(email: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({"email": email, "password": PASSWORD}).encode()
    opener.open(f"{BASE}/login", data, timeout=20).read()
    return opener


def fetch(opener, path: str, data: dict | None = None) -> str:
    body = urllib.parse.urlencode(data).encode() if data else None
    return opener.open(f"{BASE}{path}", body, timeout=60).read().decode("utf-8")


def shoot(name: str, html: str, size: str = "1600,1050") -> None:
    page = TMP / f"{name}.html"
    page.write_text(html.replace("<head>", f'<head>\n<base href="{BASE}/">', 1),
                    encoding="utf-8")
    target = OUT / f"{name}.png"
    cmd = [
        str(EDGE), "--headless=new", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--disable-extensions", "--force-device-scale-factor=1",
        f"--window-size={size}", "--virtual-time-budget=3000",
        f"--screenshot={target}", page.as_uri(),
    ]
    subprocess.run(cmd, capture_output=True, timeout=120)
    status = "ok" if target.exists() else "MISSING"
    print(f"  [{status:>7}] {target.name}")


def main() -> int:
    if not EDGE.exists():
        print(f"Edge not found at {EDGE}")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)

    conn = psycopg.connect(dsn(), autocommit=True, row_factory=dict_row)
    rows = conn.execute("""
        SELECT batch_id FROM v_batch_availability
        WHERE status = 'available' AND remaining_kg >= 10 AND expires_at > now()
        ORDER BY expires_at LIMIT 2
    """).fetchall()
    batch, second_batch = rows[0], rows[-1]
    courier = conn.execute("""
        SELECT u.user_id FROM users u
        JOIN roles r ON r.role_id = u.role_id
        JOIN volunteer_skills vs ON vs.user_id = u.user_id AND vs.skill_id = 2
        WHERE r.role_name = 'Volunteer Courier'
          AND vs.expires_on > current_date AND u.is_active
        ORDER BY u.user_id LIMIT 1
    """).fetchone()
    vehicle = conn.execute(
        "SELECT vehicle_id FROM vehicles WHERE is_active ORDER BY vehicle_id LIMIT 1"
    ).fetchone()

    print("Capturing screenshots...")
    anon = urllib.request.build_opener()
    shoot("01_login", anon.open(f"{BASE}/login", timeout=20).read().decode(), "1600,1050")

    admin = session("admin@rescuelink.org")
    shoot("02_admin_dashboard", fetch(admin, "/"), "1600,1150")
    shoot("03_admin_partners", fetch(admin, "/admin/organizations"), "1600,1300")
    shoot("04_audit_log", fetch(admin, "/audit"), "1600,1200")
    shoot("05_analytics", fetch(admin, "/analytics"), "1600,1500")

    donor = session("donor@rescuelink.org")
    shoot("06_donor_batches", fetch(donor, "/batches"), "1600,1150")
    shoot("07_batch_form", fetch(donor, "/batches/new"), "1600,1050")

    coord = session("coordinator@rescuelink.org")
    shoot("08_claims", fetch(coord, "/claims"), "1600,1250")
    shoot("09_transaction_form", fetch(coord, f"/transaction?batch_id={batch['batch_id']}"),
          "1600,1150")

    payload = {
        "batch_id": batch["batch_id"],
        "kg": "8",
        "volunteer_id": courier["user_id"],
        "vehicle_id": vehicle["vehicle_id"],
        "temp_c": "3.5",
        "mode": "none",
    }
    shoot("10_transaction_commit", fetch(coord, "/transaction", payload), "1600,3100")
    shoot("11_transaction_rollback",
          fetch(coord, "/transaction",
                {**payload, "batch_id": second_batch["batch_id"], "mode": "late_failure"}),
          "1600,3100")
    shoot("12_coordinator_runs", fetch(coord, "/runs"), "1600,1150")

    courier_app = session("courier@rescuelink.org")
    shoot("13_courier_dashboard", fetch(courier_app, "/"), "1600,1050")
    shoot("14_run_detail", fetch(admin, "/runs/5"), "1600,1250")
    shoot("15_performance", fetch(admin, "/performance"), "1600,1400")

    print(f"\nScreenshots written to {OUT}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
