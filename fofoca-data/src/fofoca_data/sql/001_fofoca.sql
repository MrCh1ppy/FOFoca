-- fofoca-data v0.2 schema (two tables: fund, fund_nav_daily)
-- Dedicated PostgreSQL database. NEVER run this against Ariadne's database.
-- The role executing this file must own the resulting tables.
--
-- The legacy fund_sync_state table was removed in v0.2. This script never
-- drops anything; removing the old table from an existing deployment is a
-- separate, explicit, backup-verified operator migration (see
-- fofoca_data.migration / deploy/README.md). Repeat initialization is safe:
-- it creates the two retained tables if missing and does not recreate the
-- removed one.

BEGIN;

CREATE TABLE IF NOT EXISTS fund (
    id               BIGSERIAL PRIMARY KEY,
    code             VARCHAR(16)  NOT NULL UNIQUE,
    name             VARCHAR(128) NOT NULL,
    fund_type        VARCHAR(64)  NOT NULL,
    status           VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
    established_date DATE         NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fund_nav_daily (
    fund_id          BIGINT        NOT NULL REFERENCES fund(id),
    nav_date         DATE          NOT NULL,
    unit_nav         NUMERIC(18,6) NULL,
    accumulated_nav  NUMERIC(18,6) NULL,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (fund_id, nav_date)
);

COMMIT;
