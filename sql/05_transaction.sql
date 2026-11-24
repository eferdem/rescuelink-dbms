-- =====================================================================
--  RescueLink :: 05_transaction.sql
-- ---------------------------------------------------------------------
--  The core ACID workflow, scripted for psql:
--  "reserve a batch and dispatch it" -- seven tables, one unit of work.
--
--  Scenario A commits a real dispatch.
--  Scenario B repeats it and then trips the CHECK constraint on
--  donation_batches.status at the very last step, so every write from
--  Scenario B must vanish after ROLLBACK.
--
--  Usage:
--      psql -h 127.0.0.1 -p 5433 -U rescue_admin -d rescuelink \
--           -f sql/05_transaction.sql
--
--  The interactive version of exactly this workflow, with a live
--  statement trace, is the /transaction page of the Flask app.
-- =====================================================================

\pset pager off
\set ON_ERROR_STOP 1

\echo ''
\echo '=============================================================='
\echo ' Scenario A -- reserve and dispatch (expected: COMMIT)'
\echo '=============================================================='

SELECT count(*) AS allocations_before,
       (SELECT count(*) FROM delivery_runs)  AS runs_before,
       (SELECT count(*) FROM shift_assignments) AS shifts_before,
       (SELECT count(*) FROM audit_log)      AS audit_before
FROM batch_allocations;

-- Choose a claimable batch and a recipient that can actually store it.
SELECT v.batch_id        AS batch_id,
       c.org_id          AS recipient_id,
       v.remaining_kg    AS remaining_kg
FROM v_batch_availability v
JOIN org_capabilities c ON c.category_id = v.category_id
JOIN organizations    o ON o.org_id = c.org_id AND o.is_active
WHERE v.status = 'available'
  AND v.remaining_kg >= 10
  AND v.expires_at > now()
ORDER BY v.expires_at
LIMIT 1
\gset

SELECT user_id AS courier_id
FROM volunteer_skills
WHERE skill_id = 2 AND expires_on > current_date
ORDER BY user_id LIMIT 1
\gset

SELECT vehicle_id FROM vehicles WHERE is_active ORDER BY vehicle_id LIMIT 1
\gset

\echo 'Batch:' :batch_id ' recipient:' :recipient_id ' courier:' :courier_id ' vehicle:' :vehicle_id

BEGIN;

-- 1. Lock the row.  A second session would block here until we finish.
SELECT 'row locked' AS step, batch_id, status, quantity_kg
FROM donation_batches
WHERE batch_id = :batch_id
FOR UPDATE;

-- 2. Books the food for this recipient.
INSERT INTO batch_allocations (batch_id, recipient_org_id, allocated_kg, status, note)
VALUES (:batch_id, :recipient_id, 10, 'reserved', 'sql/05_transaction.sql demo')
RETURNING allocation_id AS alloc_id
\gset

-- 3. Records the cold-chain probe taken at the donor's door.
INSERT INTO cold_chain_checks (batch_id, checked_by, temp_c, passed, notes)
VALUES (:batch_id, :courier_id, 3.5, true, 'Checked at pickup');

-- 4. Creates the run and links the batch to it.
INSERT INTO delivery_runs (run_date, vehicle_id, lead_user_id, status, started_at, notes)
VALUES (current_date, :vehicle_id, :courier_id, 'planned', now(), 'psql demo run')
RETURNING run_id AS run_id
\gset

INSERT INTO run_batches (run_id, batch_id, sequence_no)
VALUES (:run_id, :batch_id, 1);

-- 5. Assigns the certified driver.
INSERT INTO shift_assignments (run_id, user_id, role_on_run, hours_logged)
VALUES (:run_id, :courier_id, 'driver', 2.0);

-- 6. Takes the batch out of the open pool.
UPDATE donation_batches
   SET status = 'claimed'
 WHERE batch_id = :batch_id AND status = 'available';

-- 7. Leaves a fingerprint.
INSERT INTO audit_log (actor_user_id, action, entity, entity_id, details)
VALUES (:courier_id, 'batch.dispatch', 'batch_allocations', :'alloc_id',
        jsonb_build_object('source', 'sql/05_transaction.sql',
                           'batch_id', :batch_id,
                           'kg', 10,
                           'run_id', :run_id));

COMMIT;

\echo 'Committed -- counts should each have grown by one:'

SELECT count(*) AS allocations_after,
       (SELECT count(*) FROM delivery_runs)     AS runs_after,
       (SELECT count(*) FROM shift_assignments) AS shifts_after,
       (SELECT count(*) FROM audit_log)         AS audit_after
FROM batch_allocations;

\echo ''
\echo '=============================================================='
\echo ' Scenario B -- same workflow, deliberate failure (expected: ROLLBACK)'
\echo '=============================================================='

SELECT v.batch_id, c.org_id AS recipient_id, v.remaining_kg
FROM v_batch_availability v
JOIN org_capabilities c ON c.category_id = v.category_id
WHERE v.status = 'available' AND v.remaining_kg >= 10 AND v.expires_at > now()
  AND v.batch_id <> :batch_id
ORDER BY v.expires_at LIMIT 1
\gset

SELECT count(*) AS allocations_before_failure,
       (SELECT count(*) FROM delivery_runs)  AS runs_before_failure,
       (SELECT count(*) FROM cold_chain_checks) AS checks_before_failure
FROM batch_allocations;

-- let psql continue past the constraint violation below
\set ON_ERROR_STOP 0

BEGIN;

SELECT 'row locked' AS step, status
FROM donation_batches WHERE batch_id = :batch_id FOR UPDATE;

INSERT INTO batch_allocations (batch_id, recipient_org_id, allocated_kg, status)
VALUES (:batch_id, :recipient_id, 10, 'reserved')
RETURNING allocation_id AS alloc_id_b \gset

INSERT INTO cold_chain_checks (batch_id, checked_by, temp_c, passed)
VALUES (:batch_id, :courier_id, 3.5, true);

INSERT INTO delivery_runs (run_date, vehicle_id, lead_user_id, status)
VALUES (current_date, :vehicle_id, :courier_id, 'planned')
RETURNING run_id AS run_id_b \gset

INSERT INTO shift_assignments (run_id, user_id, role_on_run, hours_logged)
VALUES (:run_id_b, :courier_id, 'driver', 2.0);

-- The typo that must not survive.  CHECK (status IN (...)) rejects it.
UPDATE donation_batches
   SET status = 'teleported'
 WHERE batch_id = :batch_id;

ROLLBACK;
\set ON_ERROR_STOP 1

\echo 'Rolled back -- counts must be identical to before the failure:'

SELECT count(*) AS allocations_after_failure,
       (SELECT count(*) FROM delivery_runs)  AS runs_after_failure,
       (SELECT count(*) FROM cold_chain_checks) AS checks_after_failure
FROM batch_allocations;

\echo ''
\echo 'Scenario B rows are gone: no allocation, no run, no shift, no cold-chain check.'
\echo 'The batch is still available:'
SELECT batch_id, status FROM donation_batches WHERE batch_id = :batch_id;
