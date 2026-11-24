-- =====================================================================
--  RescueLink :: Surplus Food Rescue & Redistribution Network
--  DBMS Course Project -- Schema definition (PostgreSQL 16+)
-- ---------------------------------------------------------------------
--  Normal form: 3NF throughout.  No cached aggregates.  "Remaining kg"
--  of a batch is always derived (see views at the bottom), never stored,
--  so no update anomaly can occur when an allocation is cancelled.
--  The single deliberate exception is documented in docs/report.md
--  (section "Normalization"), where we discuss and reject the common
--  shortcut of caching donation_batches.remaining_kg.
-- =====================================================================

SET client_min_messages = warning;

DROP VIEW  IF EXISTS v_batch_availability     CASCADE;
DROP VIEW  IF EXISTS v_run_summary            CASCADE;
DROP TABLE IF EXISTS audit_log                CASCADE;
DROP TABLE IF EXISTS handovers                CASCADE;
DROP TABLE IF EXISTS cold_chain_checks        CASCADE;
DROP TABLE IF EXISTS shift_assignments        CASCADE;
DROP TABLE IF EXISTS volunteer_skills         CASCADE;
DROP TABLE IF EXISTS skills                   CASCADE;
DROP TABLE IF EXISTS run_batches              CASCADE;
DROP TABLE IF EXISTS delivery_runs            CASCADE;
DROP TABLE IF EXISTS vehicles                 CASCADE;
DROP TABLE IF EXISTS batch_allocations        CASCADE;
DROP TABLE IF EXISTS donation_batches         CASCADE;
DROP TABLE IF EXISTS org_capabilities         CASCADE;
DROP TABLE IF EXISTS food_categories          CASCADE;
DROP TABLE IF EXISTS users                    CASCADE;
DROP TABLE IF EXISTS organizations            CASCADE;
DROP TABLE IF EXISTS roles                    CASCADE;

-- ---------------------------------------------------------------------
-- 1. RBAC core
-- ---------------------------------------------------------------------
CREATE TABLE roles (
    role_id      smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_name    text NOT NULL UNIQUE,
    description  text NOT NULL
);

CREATE TABLE organizations (
    org_id          integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            text NOT NULL UNIQUE,
    org_type        text NOT NULL CHECK (org_type IN ('donor', 'recipient')),
    tax_id          text NOT NULL UNIQUE,
    address         text NOT NULL,
    district        text NOT NULL,
    contact_email   text NOT NULL,
    phone           text NOT NULL,
    -- storage capacity matters for recipients: a claimed batch can only
    -- be handed over if the recipient has room for it.
    cold_storage_kg numeric(8,2) NOT NULL DEFAULT 0 CHECK (cold_storage_kg >= 0),
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
    user_id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email         text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    full_name     text NOT NULL,
    phone         text NOT NULL,
    role_id       smallint NOT NULL REFERENCES roles (role_id),
    -- NULL for platform admins, who belong to no partner organisation
    org_id        integer REFERENCES organizations (org_id),
    is_active     boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT users_org_required_for_partners
        CHECK ( (role_id = 1) OR (org_id IS NOT NULL) )   -- role 1 = admin
);


-- ---------------------------------------------------------------------
-- 2. Food taxonomy + recipient capability (M:N #1)
-- ---------------------------------------------------------------------
CREATE TABLE food_categories (
    category_id        smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name               text NOT NULL UNIQUE,
    needs_refrigeration boolean NOT NULL DEFAULT false,
    shelf_life_days    smallint NOT NULL CHECK (shelf_life_days > 0),
    kg_per_meal        numeric(4,2) NOT NULL DEFAULT 0.40 CHECK (kg_per_meal > 0)
);

-- M:N between organizations and food_categories: which recipient can
-- accept which category, and how much of it per week.
CREATE TABLE org_capabilities (
    org_id        integer  NOT NULL REFERENCES organizations (org_id) ON DELETE CASCADE,
    category_id   smallint NOT NULL REFERENCES food_categories (category_id) ON DELETE CASCADE,
    max_weekly_kg numeric(8,2) NOT NULL CHECK (max_weekly_kg > 0),
    PRIMARY KEY (org_id, category_id)
);


-- ---------------------------------------------------------------------
-- 3. Donation batches -- the core inventory
-- ---------------------------------------------------------------------
CREATE TABLE donation_batches (
    batch_id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    donor_org_id     integer  NOT NULL REFERENCES organizations (org_id),
    category_id      smallint NOT NULL REFERENCES food_categories (category_id),
    item_description text NOT NULL,
    quantity_kg      numeric(8,2) NOT NULL CHECK (quantity_kg > 0),
    prepared_at      timestamptz NOT NULL,
    expires_at       timestamptz NOT NULL,
    storage_temp_c   numeric(4,1) NOT NULL,
    status           text NOT NULL DEFAULT 'available'
                     CHECK (status IN ('available','claimed','delivered','spoiled','withdrawn')),
    created_by       integer NOT NULL REFERENCES users (user_id),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT batch_expiry_after_preparation CHECK (expires_at > prepared_at)
);


-- ---------------------------------------------------------------------
-- 4. Allocations (M:N #2): a batch can feed several recipients,
--    a recipient can claim from several batches.
-- ---------------------------------------------------------------------
CREATE TABLE batch_allocations (
    allocation_id    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id         integer  NOT NULL REFERENCES donation_batches (batch_id) ON DELETE CASCADE,
    recipient_org_id integer  NOT NULL REFERENCES organizations (org_id),
    allocated_kg     numeric(8,2) NOT NULL CHECK (allocated_kg > 0),
    status           text NOT NULL DEFAULT 'reserved'
                     CHECK (status IN ('reserved','handed_over','cancelled')),
    allocated_at     timestamptz NOT NULL DEFAULT now(),
    note             text,
    CONSTRAINT allocation_unique_per_recipient UNIQUE (batch_id, recipient_org_id)
);


-- ---------------------------------------------------------------------
-- 5. Logistics: vehicles, runs, run<->batch (M:N #3)
-- ---------------------------------------------------------------------
CREATE TABLE vehicles (
    vehicle_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plate          text NOT NULL UNIQUE,
    capacity_kg    numeric(8,2) NOT NULL CHECK (capacity_kg > 0),
    is_refrigerated boolean NOT NULL DEFAULT false,
    owner_org_id   integer REFERENCES organizations (org_id),
    is_active      boolean NOT NULL DEFAULT true
);

CREATE TABLE delivery_runs (
    run_id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_date      date NOT NULL,
    vehicle_id    integer NOT NULL REFERENCES vehicles (vehicle_id),
    lead_user_id  integer NOT NULL REFERENCES users (user_id),
    status        text NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned','in_progress','completed','cancelled')),
    started_at    timestamptz,
    completed_at  timestamptz,
    total_km      numeric(6,1) CHECK (total_km >= 0),
    notes         text,
    CONSTRAINT run_time_order CHECK (completed_at IS NULL OR started_at IS NULL
                                     OR completed_at >= started_at)
);

-- M:N between delivery_runs and donation_batches
CREATE TABLE run_batches (
    run_id      integer NOT NULL REFERENCES delivery_runs (run_id) ON DELETE CASCADE,
    batch_id    integer NOT NULL REFERENCES donation_batches (batch_id),
    sequence_no smallint NOT NULL CHECK (sequence_no > 0),
    PRIMARY KEY (run_id, batch_id),
    CONSTRAINT run_sequence_unique UNIQUE (run_id, sequence_no)
);

-- ---------------------------------------------------------------------
-- 6. Volunteers: skills + shifts (M:N #4 and M:N #5)
-- ---------------------------------------------------------------------
CREATE TABLE skills (
    skill_id    smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    description text NOT NULL
);

CREATE TABLE volunteer_skills (
    user_id        integer  NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    skill_id       smallint NOT NULL REFERENCES skills (skill_id) ON DELETE CASCADE,
    certified_on   date NOT NULL,
    expires_on     date NOT NULL,
    PRIMARY KEY (user_id, skill_id),
    CONSTRAINT cert_dates_order CHECK (expires_on > certified_on)
);


CREATE TABLE shift_assignments (
    run_id       integer NOT NULL REFERENCES delivery_runs (run_id) ON DELETE CASCADE,
    user_id      integer NOT NULL REFERENCES users (user_id),
    role_on_run  text NOT NULL CHECK (role_on_run IN ('driver','loader','coordinator')),
    hours_logged numeric(4,1) NOT NULL DEFAULT 0 CHECK (hours_logged BETWEEN 0 AND 24),
    PRIMARY KEY (run_id, user_id)
);


-- ---------------------------------------------------------------------
-- 7. Quality assurance / traceability
-- ---------------------------------------------------------------------
CREATE TABLE cold_chain_checks (
    check_id   integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id   integer NOT NULL REFERENCES donation_batches (batch_id) ON DELETE CASCADE,
    checked_by integer NOT NULL REFERENCES users (user_id),
    temp_c     numeric(4,1) NOT NULL,
    passed     boolean NOT NULL,
    checked_at timestamptz NOT NULL DEFAULT now(),
    notes      text
);


CREATE TABLE handovers (
    handover_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    allocation_id   integer NOT NULL UNIQUE
                    REFERENCES batch_allocations (allocation_id) ON DELETE CASCADE,
    received_by_name text NOT NULL,
    received_kg     numeric(8,2) NOT NULL CHECK (received_kg >= 0),
    condition       text NOT NULL CHECK (condition IN ('good','partial','rejected')),
    signed_at       timestamptz NOT NULL DEFAULT now(),
    notes           text
);

-- ---------------------------------------------------------------------
-- 8. Audit trail -- every privileged write leaves a fingerprint.
--    This is what makes the RBAC story verifiable in the demo.
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    audit_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_user_id integer REFERENCES users (user_id),
    action       text NOT NULL,
    entity       text NOT NULL,
    entity_id    text,
    details      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);


-- ---------------------------------------------------------------------
-- 9. Derived views (kept derived on purpose: no redundant columns)
-- ---------------------------------------------------------------------

-- Real-time remaining quantity.  `filter` keeps cancelled allocations
-- from consuming stock.
CREATE VIEW v_batch_availability AS
SELECT b.batch_id,
       b.donor_org_id,
       d.name                      AS donor_name,
       b.category_id,
       c.name                      AS category_name,
       c.needs_refrigeration,
       b.item_description,
       b.quantity_kg,
       COALESCE(SUM(a.allocated_kg)
                FILTER (WHERE a.status <> 'cancelled'), 0)::numeric(8,2)
                                   AS allocated_kg,
       (b.quantity_kg - COALESCE(SUM(a.allocated_kg)
                FILTER (WHERE a.status <> 'cancelled'), 0))::numeric(8,2)
                                   AS remaining_kg,
       b.prepared_at,
       b.expires_at,
       b.storage_temp_c,
       b.status,
       b.created_at
FROM donation_batches b
JOIN organizations   d ON d.org_id = b.donor_org_id
JOIN food_categories c ON c.category_id = b.category_id
LEFT JOIN batch_allocations a ON a.batch_id = b.batch_id
GROUP BY b.batch_id, d.name, c.name, c.needs_refrigeration;

-- Pre-aggregated in subqueries so that counting batches and counting crew
-- members cannot inflate each other (the classic fan-out bug).
CREATE VIEW v_run_summary AS
SELECT r.run_id,
       r.run_date,
       r.status,
       v.plate,
       v.is_refrigerated,
       u.full_name              AS lead_name,
       COALESCE(b.batch_count, 0) AS batch_count,
       COALESCE(s.crew_size, 0)   AS crew_size,
       COALESCE(b.carried_kg, 0)  AS carried_kg,
       r.total_km
FROM delivery_runs r
JOIN vehicles v ON v.vehicle_id = r.vehicle_id
JOIN users    u ON u.user_id    = r.lead_user_id
LEFT JOIN (
    SELECT rb.run_id,
           count(*)                     AS batch_count,
           sum(d.quantity_kg)           AS carried_kg
    FROM run_batches rb
    JOIN donation_batches d ON d.batch_id = rb.batch_id
    GROUP BY rb.run_id
) b ON b.run_id = r.run_id
LEFT JOIN (
    SELECT run_id, count(*) AS crew_size
    FROM shift_assignments
    GROUP BY run_id
) s ON s.run_id = r.run_id;
