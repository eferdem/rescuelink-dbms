# RescueLink — Project Proposal

**Course:** Database Management Systems
**Project title:** RescueLink — A Surplus Food Rescue and Redistribution Network
**Student:** `<name surname>` — `<student number>`
**Date:** 24 November 2026

---

## 1. Project title and problem statement

Every day, bakeries, hotels, supermarkets and corporate kitchens throw away food
that is still safe to eat, while food banks and community kitchens a few
kilometres away run short. The two sides rarely find each other in time,
because surplus is perishable: a batch of cooked meals is worthless if it is
matched to a recipient that cannot refrigerate it, or if the handover happens
after the expiry hour. RescueLink is a database-backed coordination platform for
this problem. It records each surplus batch together with its category, expiry
time and storage temperature; it knows which recipient organisations are
equipped to accept which categories and how much they can take per week; and it
assigns a certified volunteer courier and a suitable vehicle to every pickup.
The database is the single source of truth for availability, so two
coordinators can never promise the same kilograms twice, and every privileged
action leaves an audit trail.

## 2. User roles (4)

| Role | Core actions |
| :--- | :--- |
| **Platform Admin** | Manage partner organisations, food categories, users and vehicles; view audit log, analytics and performance pages; dispatch on behalf of any recipient. |
| **Donor Staff** | Publish a surplus batch (item, kg, category, preparation and expiry time); edit or withdraw it while it is still available; follow claims and handovers on their own food. |
| **Recipient Coordinator** | Browse food that matches the organisation's storage capabilities; claim kg through the ACID dispatch form; sign handovers; plan delivery runs and assign crew. |
| **Volunteer Courier** | See assigned shifts and upcoming routes; open run details with pickup sequence and crew; log hours through completed runs. |

## 3. Entity list (16 tables + 2 views)

| Table | One-line description |
| :--- | :--- |
| `roles` | The four access levels; referenced by every user. |
| `organizations` | Donors and recipients in one table, discriminated by `org_type`, with address, district and cold-storage capacity. |
| `users` | Staff, coordinators, couriers and admins; role FK plus optional organisation FK. |
| `food_categories` | Food taxonomy with refrigeration flag, shelf life and kg-per-meal factor. |
| `org_capabilities` | **M:N** — which recipient accepts which category, and its weekly kg limit. |
| `donation_batches` | The perishable inventory: donor, category, kg, prepared/expiry timestamps, storage temperature, status. |
| `batch_allocations` | **M:N** — which recipient claimed how many kg of which batch, and when. |
| `cold_chain_checks` | Temperature probes taken at pickup, with pass/fail and notes. |
| `handovers` | Signed receipt per allocation: received kg, condition, receiver name. |
| `vehicles` | Fleet with plate, capacity, refrigeration flag and owner. |
| `delivery_runs` | One trip: date, vehicle, lead user, status, distance. |
| `run_batches` | **M:N** — ordered pickup list linking runs and batches. |
| `shift_assignments` | **M:N** — crew members on a run with their role and hours. |
| `skills` | Certification catalogue (e.g. C1 driving licence, food hygiene). |
| `volunteer_skills` | **M:N** — certifications held by volunteers, with validity dates. |
| `audit_log` | Append-only record of every privileged write (actor, action, entity, JSON details). |
| `v_batch_availability` *(view)* | Derives allocated and remaining kg per batch from the allocation ledger. |
| `v_run_summary` *(view)* | Pre-aggregated run summary without join fan-out. |

## 4. Many-to-many relationships (5 junction tables)

1. `org_capabilities (org_id, category_id)` — recipient ↔ food category, with `max_weekly_kg`.
2. `batch_allocations (batch_id, recipient_org_id)` — batch ↔ recipient claim, with kg and status.
3. `run_batches (run_id, batch_id)` — delivery run ↔ batch, with `sequence_no`.
4. `shift_assignments (run_id, user_id)` — run ↔ volunteer, with `role_on_run` and hours.
5. `volunteer_skills (user_id, skill_id)` — volunteer ↔ certification, with issue and expiry dates.

## 5. Multi-step ACID transaction

**“Reserve and dispatch a batch”** touches seven tables in one unit of work:

1. `SELECT … FOR UPDATE` locks the batch row.
2. Validate status, expiry and derived remaining kg.
3. `INSERT batch_allocations` reserves the requested kilograms.
4. `INSERT cold_chain_checks` records the probe and rejects an unsafe temperature.
5. Check the courier's valid C1 licence in `volunteer_skills`.
6. `INSERT delivery_runs`, `run_batches` and `shift_assignments`.
7. `UPDATE donation_batches`, `INSERT audit_log`, `COMMIT`.

Any failure issues `ROLLBACK`; the web page then shows a statement-by-statement
trace and before/after row counts proving that no partial write survived.

## 6. Planned analytical queries

| Question | Technique |
| :--- | :--- |
| Who rescued the most food each month, and how is each donor trending? | `RANK()`, running `SUM() OVER (PARTITION BY …)` |
| Which expiring batches should be offered to which capable recipients first? | Multi-stage CTE, `ROW_NUMBER()`, `LEFT JOIN` aggregation |
| Is rescued volume growing month over month? | CTE + `LAG()` |
| Which recipients receive less than the category average? | Correlated subquery in `HAVING` |
| Which donors have gone dormant? | Correlated `NOT EXISTS` anti-join |
| What share of each batch actually reaches a recipient? | Subquery in `FROM`, filtered aggregate |
| Which categories spoil above a quality threshold? | `GROUP BY … HAVING`, `COUNT(*) FILTER` |
| Which couriers contribute most, with at least five runs? | `GROUP BY … HAVING` |
| Who are the top two donors per district? | `ROW_NUMBER() OVER (PARTITION BY district)` |

## 7. Scope of the web interface

A minimal Flask application (plain HTML/CSS, no ORM, all SQL parameterised)
provides: login with a one-click role switcher, role-specific dashboards,
CRUD for batches and administrative entities, the interactive transaction demo
with injectable failure modes, an analytics page rendering the query suite, an
`EXPLAIN ANALYZE` page, and an audit view.
