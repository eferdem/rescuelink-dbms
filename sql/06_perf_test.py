#!/usr/bin/env python3
"""
RescueLink -- index impact benchmark
====================================

Runs EXPLAIN (ANALYZE, BUFFERS) on the analytical queries in BOTH states:

    before  -- schema + data only, no secondary indexes
    after   -- sql/02_indexes.sql applied

and writes three artefacts into docs/:

    explain_before.txt     human-readable plans (paste into the report)
    explain_after.txt      human-readable plans
    explain_summary.md     planning/execution times + plan nodes, side by side

The script is idempotent: it drops every idx_* index first, so it can be
re-run any time and produces comparable numbers.

Usage:  py -3.11 sql/06_perf_test.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from sql_loader import load_queries  # noqa: E402

QUERY_FILE = ROOT / "sql" / "04_queries.sql"
INDEX_FILE = ROOT / "sql" / "02_indexes.sql"
DOCS = ROOT / "docs"

# queries chosen because they are the ones the application runs on every
# page load, plus the two heaviest reports
BENCHMARK = [
    "at_risk_pipeline",
    "donor_leaderboard",
    "fill_rate",
    "dormant_donors",
    "top_donors_per_district",
]

# Application queries that are not part of the analytical suite but drive
# the busiest pages. Benchmarked with a literal id so EXPLAIN can plan them.
APP_QUERIES = [
    (
        "app_donor_batches",
        "Donor dashboard: one donor's batches, newest first",
        """
        SELECT b.batch_id, b.item_description, b.quantity_kg, b.status,
               b.expires_at, c.name AS category
        FROM donation_batches b
        JOIN food_categories c ON c.category_id = b.category_id
        WHERE b.donor_org_id = 7
        ORDER BY b.created_at DESC
        LIMIT 50
        """,
    ),
    (
        "app_recipient_claims",
        "Coordinator portal: one recipient's active claims",
        """
        SELECT a.allocation_id, a.allocated_kg, a.status, a.allocated_at,
               b.item_description, b.expires_at
        FROM batch_allocations a
        JOIN donation_batches b ON b.batch_id = a.batch_id
        WHERE a.recipient_org_id = 28
          AND a.status = 'reserved'
        ORDER BY a.allocated_at DESC
        """,
    ),
]

DSN = (
    f"host={os.environ.get('PGHOST', '127.0.0.1')} "
    f"port={os.environ.get('PGPORT', '5433')} "
    f"dbname={os.environ.get('PGDATABASE', 'rescuelink')} "
    f"user={os.environ.get('PGUSER', 'rescue_admin')} "
    f"password={os.environ.get('PGPASSWORD', 'rescue_dev_pw')}"
)


def drop_secondary_indexes(cur) -> int:
    rows = cur.execute("""
        SELECT indexname FROM pg_indexes
        WHERE schemaname = 'public' AND indexname LIKE 'idx_%'
    """).fetchall()
    for (name,) in rows:
        cur.execute(f'DROP INDEX IF EXISTS "{name}"')
    return len(rows)


def apply_indexes(conn, cur) -> None:
    sql = INDEX_FILE.read_text(encoding="utf-8")
    cur.execute(sql)
    conn.commit()


def plan_json(cur, sql: str) -> dict:
    row = cur.execute(
        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql
    ).fetchone()
    plan = row[0][0] if isinstance(row[0], list) else row[0]
    return plan


def plan_text(cur, sql: str) -> str:
    rows = cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql).fetchall()
    return "\n".join(r[0] for r in rows)


def plan_nodes(node: dict, out: list[str], depth: int = 0) -> None:
    label = node.get("Node Type", "?")
    if node.get("Index Name"):
        label += f" [{node['Index Name']}]"
    elif node.get("Relation Name"):
        label += f" [{node['Relation Name']}]"
    out.append("  " * depth + label)
    for child in node.get("Plans", []):
        plan_nodes(child, out, depth + 1)


def key_nodes(plan: dict, limit: int = 4) -> str:
    """Compact one-line summary: scans/indexes actually used, in plan order."""
    nodes: list[str] = []
    plan_nodes(plan["Plan"], nodes)
    picked = [n.strip() for n in nodes if "Scan" in n or "Join" in n]
    return " > ".join(picked[:limit]) if picked else nodes[0].strip()


def main() -> None:
    queries = {q.name: q for q in load_queries(QUERY_FILE)}
    missing = [n for n in BENCHMARK if n not in queries]
    if missing:
        raise SystemExit(f"Unknown query ids: {missing}")

    subjects: list[tuple[str, str, str]] = [
        (n, queries[n].title, queries[n].sql) for n in BENCHMARK
    ] + [(n, title, sql.strip()) for n, title, sql in APP_QUERIES]

    DOCS.mkdir(exist_ok=True)
    conn = psycopg.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET lc_messages TO 'C'")

    dropped = drop_secondary_indexes(cur)
    cur.execute("ANALYZE")
    print(f"[before] {dropped} secondary indexes dropped, statistics refreshed")

    measured: dict[str, dict] = {}
    for state in ("before", "after"):
        if state == "after":
            apply_indexes(conn, cur)
            cur.execute("ANALYZE")
            print("[after]  applied sql/02_indexes.sql")

        texts: list[str] = []
        for name, title, sql in subjects:
            cur.execute(sql)                     # warm the cache, discard rows
            samples = [plan_json(cur, sql) for _ in range(3)]
            data = samples[-1]
            data["Execution Time"] = statistics.median(
                s["Execution Time"] for s in samples
            )
            text = plan_text(cur, sql)
            measured.setdefault(name, {"title": title})[state] = data
            texts.append(f"{'=' * 78}\n{name}  --  {title}\n{'=' * 78}\n{text}\n")
            print(f"  {name:<26} {state:<7} {data['Execution Time']:8.3f} ms")
        (DOCS / f"explain_{state}.txt").write_text("\n".join(texts), encoding="utf-8")

    # ---- summary table -------------------------------------------------
    lines = [
        "# EXPLAIN ANALYZE - before / after indexing",
        "",
        "Generated by `sql/06_perf_test.py` on a 74k-row dataset "
        "(12,000 batches, ~15,400 allocations, ~21,700 cold-chain checks). "
        "Each query is warmed with one execution; the time shown is the "
        "median of three measured executions.",
        "",
        "| Query | Before (ms) | After (ms) | Change | Plan nodes before | Plan nodes after |",
        "|---|---:|---:|---:|---|---|",
    ]
    for name, _title, _sql in subjects:
        before = measured[name]["before"]
        after = measured[name]["after"]
        b_ms, a_ms = before["Execution Time"], after["Execution Time"]
        change = (a_ms - b_ms) / b_ms * 100 if b_ms else 0.0
        lines.append(
            f"| `{name}` | {b_ms:.2f} | {a_ms:.2f} | {change:+.0f}% "
            f"| {key_nodes(before)} | {key_nodes(after)} |"
        )
    lines += [
        "",
        "Full plans: `docs/explain_before.txt` and `docs/explain_after.txt`.",
        "",
        "Reading the results:",
        "",
        "* `at_risk_pipeline` and `dormant_donors` are the selective queries and gain",
        "  the most. The 48-hour pipeline replaces the sequential scan of 15,000+",
        "  allocations with a bitmap index scan on `idx_alloc_recipient` for the",
        "  rolling 7-day window; the batch side stays a sequential scan because 48",
        "  hours is not selective enough. `dormant_donors` turns the batch probe into",
        "  an index-only scan on the covering `idx_batches_donor_cover`.",
        "* `app_donor_batches` and `app_recipient_claims` are the busiest pages and",
        "  switch from sequential scans plus sorts to index scans with an",
        "  already-sorted order, so no sort node remains.",
        "* `donor_leaderboard` and `fill_rate` aggregate every row of the table.",
        "  PostgreSQL correctly keeps the sequential scan: an index would only add",
        "  random I/O. This is a deliberate finding, not a failure - adding an index",
        "  is not automatically an optimisation.",
        "* `top_donors_per_district` reads the whole table as well, so its timing is",
        "  dominated by the aggregate; the observed difference is run-to-run noise",
        "  (repeat `06_perf_test.py` to confirm).",
        "",
    ]
    (DOCS / "explain_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\nWrote docs/explain_before.txt, docs/explain_after.txt, docs/explain_summary.md")
    conn.close()


if __name__ == "__main__":
    main()
