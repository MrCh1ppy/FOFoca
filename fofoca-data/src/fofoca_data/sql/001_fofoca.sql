-- fofoca-data v0.1 schema
-- Dedicated PostgreSQL database. NEVER run this against Ariadne's database.
-- The role executing this file must own the resulting tables.

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

CREATE TABLE IF NOT EXISTS fund_sync_state (
    fund_id          BIGINT       NOT NULL REFERENCES fund(id),
    dataset          VARCHAR(32)  NOT NULL,
    first_data_date  DATE         NULL,
    last_data_date   DATE         NULL,
    last_sync_at     TIMESTAMPTZ  NULL,
    last_sync_status VARCHAR(16)  NOT NULL,
    last_error       TEXT         NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (fund_id, dataset)
);

COMMIT;
