-- =====================================================================
--  RescueLink :: 02_indexes.sql
-- ---------------------------------------------------------------------
--  Applying this file is the "after" state of the performance section.
--  To reproduce the "before" numbers, run scripts/reset_db.ps1 -NoIndexes
--  and then sql/06_perf_test.py, which measures both states itself.
--
--  Rationale for each index is in the comment above it; the measured
--  before/after plans live in docs/report.md, section "Performance".
-- =====================================================================

-- FK lookups and RBAC scoping -------------------------------------------------
CREATE INDEX idx_users_org          ON users (org_id);
CREATE INDEX idx_capabilities_cat   ON org_capabilities (category_id);

-- Donor dashboard: "my batches, newest first".  Covering index so the
-- leaderboard aggregate never has to touch the heap.
CREATE INDEX idx_batches_donor_cover
    ON donation_batches (donor_org_id, created_at DESC)
    INCLUDE (quantity_kg, category_id, status);

-- The hottest query on the platform: "what is available and expiring?"
-- A partial index keeps it tiny (only open batches qualify, a small
-- fraction of the 12,000 rows) and lets the planner scan expires_at
-- instead of filtering the whole heap.
CREATE INDEX idx_batches_available_exp
    ON donation_batches (expires_at)
    WHERE status = 'available';

CREATE INDEX idx_batches_category   ON donation_batches (category_id);

-- Allocation fan-out joins (view v_batch_availability, fill-rate query)
CREATE INDEX idx_alloc_batch        ON batch_allocations (batch_id);
CREATE INDEX idx_alloc_recipient    ON batch_allocations (recipient_org_id, allocated_at DESC);

-- Logistics joins
CREATE INDEX idx_runs_date          ON delivery_runs (run_date DESC);
CREATE INDEX idx_shifts_user        ON shift_assignments (user_id);
CREATE INDEX idx_cold_checks_batch  ON cold_chain_checks (batch_id, checked_at DESC);

-- Volunteer certification expiry scan
CREATE INDEX idx_volskills_skill    ON volunteer_skills (skill_id);
CREATE INDEX idx_volskills_expiry   ON volunteer_skills (expires_on);

-- Audit trail paging
CREATE INDEX idx_audit_actor_time   ON audit_log (actor_user_id, created_at DESC);
CREATE INDEX idx_audit_entity       ON audit_log (entity, entity_id);

-- Refresh planner statistics so the "after" plans are honest.
ANALYZE;
