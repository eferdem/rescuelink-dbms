# RescueLink — Surplus Food Rescue & Redistribution Network

A DBMS course project that models the full life of a surplus food batch: a donor
publishes it, a capable recipient claims it, a certified volunteer collects it
with a recorded cold-chain check, and the handover is signed. Built with
**PostgreSQL**, **Flask** and **parameterised raw SQL only** (no ORM).

![Admin dashboard](docs/screenshots/02_admin_dashboard.png)

## What is inside

| Requirement | Implementation |
| :--- | :--- |
| 8+ tables | **16 tables + 2 views** (`sql/01_schema.sql`) |
| 2+ M:N relationships | **5 junction tables**: `org_capabilities`, `batch_allocations`, `run_batches`, `shift_assignments`, `volunteer_skills` |
| 3+ roles | **4 roles** with route-level and row-level RBAC (`app/auth.py`) |
| ACID transaction | **7-table dispatch** with `SELECT … FOR UPDATE`, `BEGIN`/`COMMIT`/`ROLLBACK`, 7 injectable failure modes (`app/dispatch.py`) |
| 3NF | Documented in `docs/report.md`; deliberately **no cached remaining kg** (derived in `v_batch_availability`) |
| Realistic seed | **73,645 rows**, deterministic (`sql/03_seed.py`, seed `20261210`) |
| EXPLAIN ANALYZE | 14 indexes, before/after median timings (`sql/06_perf_test.py`) |
| Window functions | `RANK`, `ROW_NUMBER`, `LAG`, `SUM() OVER` |
| CTEs | Three-stage at-risk pipeline, leaderboard, growth |
| Subqueries | Correlated `HAVING`, `NOT EXISTS`, subquery in `FROM` |
| `GROUP BY … HAVING` | Spoilage threshold and volunteer leaderboard |
| Plain HTML UI | Flask + Jinja templates, one CSS file |
| Report | `docs/report.md`, exported to a single PDF |

## Quick start (Windows, verified path)

Prerequisites: PostgreSQL binaries (the project was built with 18.6), Python
3.11, and PowerShell.

```powershell
# 1. create a project-local cluster in .pgdata on port 5433
powershell -ExecutionPolicy Bypass -File scripts\init_cluster.ps1

# 2. load schema, seed 73,645 rows, apply indexes
powershell -ExecutionPolicy Bypass -File scripts\reset_db.ps1

# 3. verify the whole stack (50 assertions)
py -3.11 scripts\smoke_test.py

# 4. run the web app
cd app
py -3.11 app.py                  # http://127.0.0.1:5000
```

The local cluster is independent of any system-wide PostgreSQL installation and
listens only on `127.0.0.1:5433`. Connection defaults:

```
postgresql://rescue_admin:rescue_dev_pw@127.0.0.1:5433/rescuelink
```

Override them with `DATABASE_URL` or the usual `PGHOST` / `PGPORT` / `PGUSER` /
`PGPASSWORD` / `PGDATABASE` variables. See `.env.example`.

### Demo accounts

Password for all four: `Demo1234!`

| Role | E-mail |
| :--- | :--- |
| Platform Admin | `admin@rescuelink.org` |
| Donor Staff | `donor@rescuelink.org` |
| Recipient Coordinator | `coordinator@rescuelink.org` |
| Volunteer Courier | `courier@rescuelink.org` |

The login page also has one-click buttons for each account, and the header has a
role switcher for the demo.

## Where to look first

* **`/transaction`** — the ACID demo. Pick a batch, then choose a failure mode
  (`over_allocate`, `expired`, `cold_chain`, `no_certified_volunteer`,
  `late_failure`, `slow_lock`). The page shows the statement trace and
  before/after row counts. `slow_lock` holds the row lock for six seconds; open
  two tabs to watch the second claim block.
* **`/analytics`** — all nine queries with technique labels, timings and
  collapsible SQL. The page parses `sql/04_queries.sql`, the same file used by
  the report.
* **`/performance`** — before/after `EXPLAIN (ANALYZE, BUFFERS)` summary and
  full plans.
* **`docs/report.md`** — the complete report: design, 3NF argument, RBAC matrix,
  transaction walkthrough, query suite, benchmarks, screenshots, checklist.

## SQL files

| File | Purpose |
| :--- | :--- |
| `sql/01_schema.sql` | Tables, constraints, `v_batch_availability`, `v_run_summary` |
| `sql/02_indexes.sql` | 14 secondary indexes (the "after" state) |
| `sql/03_seed.py` | Deterministic 73,645-row seeder |
| `sql/04_queries.sql` | Nine analytical queries with metadata markers |
| `sql/05_transaction.sql` | psql commit/rollback demonstration |
| `sql/06_perf_test.py` | Benchmark; writes `docs/explain_*.txt` and the summary |

### Measured index impact

| Query | Before | After | Change |
| :---|---:|---:|---:|
| `at_risk_pipeline` | 4.51 ms | 1.83 ms | **−59 %** |
| `dormant_donors` | 2.52 ms | 1.07 ms | **−58 %** |
| `app_donor_batches` | 0.53 ms | 0.04 ms | **−92 %** |
| `app_recipient_claims` | 0.59 ms | 0.15 ms | **−75 %** |

Full-table aggregates (`donor_leaderboard`, `fill_rate`,
`top_donors_per_district`) correctly keep their sequential scans; the report
discusses this rather than hiding it.

## Useful commands

```powershell
# rebuild the database (schema -> seed -> indexes)
powershell -ExecutionPolicy Bypass -File scripts\reset_db.ps1

# baseline without secondary indexes (for the "before" plans)
powershell -ExecutionPolicy Bypass -File scripts\reset_db.ps1 -NoIndexes

# benchmark (drops indexes, measures, applies indexes, measures again)
py -3.11 sql\06_perf_test.py

# 50-assertion end-to-end test (Flask test client + real database)
py -3.11 scripts\smoke_test.py

# raw output of all nine analytical queries
$env:PGPASSWORD='rescue_dev_pw'
& 'C:\Program Files\PostgreSQL\18\bin\psql.exe' -h 127.0.0.1 -p 5433 `
    -U rescue_admin -d rescuelink -P footer=off -f sql\04_queries.sql

# regenerate screenshots (requires the app running on :5000)
py -3.11 scripts\screenshots.py
py -3.11 scripts\split_shots.py

# build the report PDF (Edge headless)
py -3.11 scripts\build_report_pdf.py --name "Name Surname" --number "b211210001"
```

Start/stop the cluster:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_db.ps1
powershell -ExecutionPolicy Bypass -File scripts\stop_db.ps1
```

## Docker alternative

```bash
docker compose up -d db          # schema + indexes initialise a fresh volume
docker compose run --rm seed     # one-shot deterministic seed
docker compose up -d app         # http://127.0.0.1:5000
```

The seed service is behind a profile so `docker compose up` never wipes an
existing database. The init scripts run only on a fresh `pgdata` volume; to
reseed from scratch use `docker compose down -v` first.

## Project layout

```
app/
  app.py            all Flask routes
  auth.py           session user + RBAC decorators
  db.py             psycopg helpers (no ORM)
  dispatch.py       seven-table ACID transaction and trace
  sql_loader.py     parser for sql/04_queries.sql
  templates/        Jinja templates for every screen
  static/style.css  single stylesheet
docs/
  proposal.md       1-page proposal
  report.md         full report (source of the PDF)
  query_output.txt  raw results of the nine queries
  explain_*.txt     full EXPLAIN plans
  screenshots/      live screenshots of the running app
scripts/
  init_cluster.ps1  create the local PostgreSQL cluster
  reset_db.ps1      schema -> seed -> indexes
  start_db.ps1      start the cluster
  stop_db.ps1       stop the cluster
  smoke_test.py     50 end-to-end assertions
  screenshots.py    headless Edge screenshot capture
  split_shots.py    split tall screenshots for the PDF
  build_report_pdf.py  Markdown -> styled HTML -> PDF
sql/                schema, indexes, seed, queries, transaction, benchmark
```

## Report PDF

The report is submitted as a **single PDF named `number_name.pdf`**. Build it
with:

```powershell
py -3.11 scripts\build_report_pdf.py --name "Name Surname" --number "b211210001" --out "b211210001_namesurname.pdf"
```

The script renders `docs/report.md` to `docs/report.html` with print CSS, then
uses headless Microsoft Edge to produce the PDF (A4, page numbers in the footer,
images and code blocks kept intact). If Edge is not installed, open
`docs/report.html` in any browser and print to PDF.

## Troubleshooting

* **`connection refused` on 5433** — start the cluster with
  `scripts\start_db.ps1`; the log is `.pgdata\server.log`.
* **`role "rescue_admin" does not exist`** — the local cluster was never
  initialised; run `scripts\init_cluster.ps1`.
* **Empty tables** — run `scripts\reset_db.ps1`; the schema has no seed data by
  itself.
* **Port 5000 already in use** — start with `FLASK_PORT=5001` (PowerShell:
  `$env:FLASK_PORT='5001'`).
* **Screenshots fail** — the app must be running on `http://127.0.0.1:5000`, and
  the script expects Edge at
  `C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`.
