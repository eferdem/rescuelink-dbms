# RescueLink — Presentation Notes

**DBMS Final Project · December 2026**
Suggested length: 12–15 minutes + questions. All demo steps run against the
local database on `http://127.0.0.1:5000`.

---

## Slide 1 — Title and problem (45 s)

> "A bakery throws away 80 kg of bread while a community kitchen two kilometres
> away has no bread for tonight. The food exists; the coordination does not."

* Surplus is perishable, recipients differ in storage capability, and matching
  must happen before expiry.
* RescueLink is the database that answers *what is still safe and who can take
  it now*.

## Slide 2 — Scope at a glance (45 s)

| Number | What |
|---|---|
| 16 tables + 2 views | 5 of them are M:N junctions |
| 4 roles | Admin, Donor Staff, Recipient Coordinator, Courier |
| 7-table transaction | row lock + full rollback proof |
| 73,645 rows | deterministic seed (`20261210`) |
| 9 analytical queries | window, CTE, subquery, HAVING |
| 14 indexes | before/after `EXPLAIN` with medians |

## Slide 3 — Data model (90 s)

Show the entity catalogue from the report (§2.2) or a hand-drawn diagram.

Talking points:

* Donors and recipients share `organizations` with `org_type`; both are
  organisations, so splitting them would duplicate address/contact attributes.
* Events are separate rows: allocation, cold-chain probe, handover, run.
* Remaining kilograms are **derived** in `v_batch_availability` — the key design
  decision.
* Five M:N junctions, each with relationship attributes (weekly kg, claimed kg,
  sequence, crew role, certification expiry).

## Slide 4 — Normalization (60 s)

* 3NF by construction: names/refrigeration rules are reached through FKs, never
  copied.
* Composite-key attributes depend on the pair (`max_weekly_kg` depends on
  recipient *and* category).
* We deliberately **reject** caching `remaining_kg`: correctness beats
  microseconds, and the measured view costs ~1.8 ms.
* Only JSON is in `audit_log.details`, and it is never used for a business
  decision.

## Slide 5 — Roles and RBAC (60 s)

* Route-level `@roles_required` (403) **and** row-level `org_id = %s` from the
  session.
* Actor for the audit trail comes from the session, not a hidden field.
* Demo: sign in as donor, try `/analytics` → denied; as coordinator, only own
  claims are visible.

## Slide 6 — The ACID transaction (2 min)

Walk the seven steps from the UI panel:

1. `BEGIN`
2. `SELECT … FOR UPDATE` on the batch
3. validate status, expiry, derived remaining
4. insert allocation
5. insert cold-chain probe, guard > 8 °C
6. insert run + pickup + driver shift (licence check)
7. update batch, insert audit, `COMMIT`

Emphasise: **seven tables, one unit of work**. Any failure → `ROLLBACK`.

## Slide 7 — Live demo: commit (2 min)

1. Log in as coordinator (`coordinator@rescuelink.org`, `Demo1234!`).
2. Open **Dispatch (ACID)**; pick a batch; quantity 8 kg; mode *Successful
   dispatch*.
3. Point at the banner: allocation id, run id, remaining kg.
4. Scroll the statement trace: each SQL statement with params and row count.
5. Show the proof table: allocation, probe, run, pickup, shift, audit each +1;
   batch unchanged in **row count** because step 7 is an UPDATE.

## Slide 8 — Live demo: rollback (2 min)

1. Choose mode *Constraint violation at the very last step* (`late_failure`).
2. Run. Point out where the red FAILED step appears — after five successful
   inserts.
3. Show `ROLLBACK`.
4. Show the proof table: every row count is back to its pre-transaction value.
5. Optional: mode *Cold chain* shows the domain guard, not just a DB error.

> "This is atomicity: the allocation, probe, run, pickup and shift existed for a
> moment, and then they did not."

## Slide 9 — Concurrency (60 s, optional live)

* `slow_lock` holds the row lock for 6 s.
* Open two coordinator tabs on the same batch, submit both.
* Second request blocks on `SELECT … FOR UPDATE`; after the first commits, the
  second sees `status = 'claimed'` and is rejected.
* Isolation is `READ COMMITTED`; the row lock is the serialisation point.

## Slide 10 — Analytical queries (90 s)

Show `/analytics`; open one card's SQL:

* **Q1** `RANK()` + running `SUM() OVER` per donor.
* **Q2** three-stage CTE: expiring batches → 7-day recipient load → top three
  capable recipients per batch.
* **Q4** correlated subquery in `HAVING` (below category average).
* **Q5** `NOT EXISTS` instead of `NOT IN` (NULL-safe anti-join).
* **Q8** `GROUP BY … HAVING` with `FILTER` for driver counts.

Mention that the page parses `sql/04_queries.sql`, so the report SQL and the
running SQL are the same file.

## Slide 11 — Indexing and `EXPLAIN` (90 s)

Show `/performance` or the table in the report.

| Query | Before | After |
|---|---:|---:|
| at-risk pipeline | 4.51 ms | 1.83 ms (**−59 %**) |
| dormant donors | 2.52 ms | 1.07 ms (**−58 %**) |
| donor dashboard | 0.53 ms | 0.04 ms (**−92 %**) |
| recipient claims | 0.59 ms | 0.15 ms (**−75 %**) |

Key honest point: the two whole-table aggregates **did not improve** and keep
their sequential scans — an index is only useful when it changes the plan for a
selective predicate. Mention the covering index (`INCLUDE`) and the partial
index (`WHERE status = 'available'`).

## Slide 12 — Engineering quality (45 s)

* 50 end-to-end assertions, including every rollback path and RBAC denial.
* Parameterised raw SQL everywhere (show the org insert); only two safe
  exceptions: read-only query file and a static table-name tuple.
* Deterministic seed → reproducible benchmarks.
* `psql` script (`05_transaction.sql`) reproduces commit/rollback without the UI.

## Slide 13 — Limitations and next steps (30 s)

* Single node; no pooling/replica.
* Derived kg is fine at 12k batches; would need materialization at millions.
* No PostGIS routing.
* Production hardening: revoke UPDATE/DELETE on `audit_log`, add password reset.

## Slide 14 — Close (15 s)

> "RescueLink shows that the hard part of food rescue is not storing food — it
> is coordinating perishable promises. The schema, the transaction and the
> queries are built around keeping those promises consistent."

---

## Demo checklist (before walking in)

```powershell
# 1. cluster up
powershell -ExecutionPolicy Bypass -File scripts\start_db.ps1

# 2. clean, seeded, indexed database
powershell -ExecutionPolicy Bypass -File scripts\reset_db.ps1

# 3. sanity: 50/50
py -3.11 scripts\smoke_test.py

# 4. app up
cd app
py -3.11 app.py
```

* Verify login for all four accounts.
* Keep `/transaction`, `/analytics` and `/performance` open in tabs.
* Keep a second coordinator tab ready for `slow_lock` (optional).
* Have the PDF report open on the query/index pages as a fallback.
* If the projector crops the page, use browser zoom 110 %; the layout is
  responsive down to 900 px.

---

## Likely questions and short answers

**Why not cache remaining kg?**
It is a fact about the allocation ledger. A cached column must be updated on
every insert/cancel/handover/withdraw; one missed path double-books food. The
derived view is correct by construction and costs ~1.8 ms at 12k batches.

**Why is `batch_allocations` a junction table if it has its own id?**
It resolves a genuine M:N (a batch feeds many recipients; a recipient claims
many batches). The surrogate key is there because handovers and audit rows
reference the claim; `UNIQUE (batch_id, recipient_org_id)` still protects the
pair.

**What happens if two coordinators claim at the same time?**
The first takes the row lock in `SELECT … FOR UPDATE`. The second blocks; after
the first commits, it re-reads the row, sees `status = 'claimed'`, and is
rejected. Demonstrated by `slow_lock`.

**Why did some queries not get faster?**
They aggregate every row (`GROUP BY` over 12k batches). A sequential scan is
optimal; an index would add random I/O. We report this instead of pretending
every index helps.

**Where is the evidence for rollback?**
Two places: the UI proof table (before/after row counts) and
`sql/05_transaction.sql`, which deliberately violates a CHECK at the last step
and prints identical counts after `ROLLBACK`.

**Is the SQL injection-safe?**
All values are bound parameters. The only non-parameterised SQL is read-only
analytics loaded from a shipped file, and a table name chosen from a fixed
7-element tuple for the row-count proof.

**How is 3NF satisfied with `audit_log.details` JSON?**
The audit log is an append-only event record, not a normalized business entity;
JSON keeps heterogeneous payloads without nullable-column sprawl. It is never
read to make a business decision. This is documented as the single deliberate
exception.

**What would you change at 10 million rows?**
Materialize `v_batch_availability` or maintain a ledger with triggers; partition
`cold_chain_checks` and `audit_log` by month; add PgBouncer; consider PostGIS
for routing.
