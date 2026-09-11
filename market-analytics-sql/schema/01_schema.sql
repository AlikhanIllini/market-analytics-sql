-- ============================================================
-- 01_schema.sql
-- Core tables for the market analytics database.
-- Covers equities, ETFs, and crypto in one normalized model.
-- ============================================================

DROP TABLE IF EXISTS price_daily CASCADE;
DROP TABLE IF EXISTS asset CASCADE;

-- One row per tradable instrument.
CREATE TABLE asset (
    asset_id     SERIAL PRIMARY KEY,
    symbol       TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    asset_class  TEXT NOT NULL CHECK (asset_class IN ('equity', 'etf', 'crypto')),
    currency     TEXT NOT NULL DEFAULT 'USD'
);

-- Daily OHLCV bar. One row per asset per trading day.
-- Composite primary key blocks duplicate bars and gives a
-- natural lookup index on (asset_id, dt).
CREATE TABLE price_daily (
    asset_id   INT  NOT NULL REFERENCES asset(asset_id) ON DELETE CASCADE,
    dt         DATE NOT NULL,
    open       NUMERIC(18, 6),
    high       NUMERIC(18, 6),
    low        NUMERIC(18, 6),
    close      NUMERIC(18, 6) NOT NULL,
    volume     NUMERIC(20, 2),
    PRIMARY KEY (asset_id, dt),
    CHECK (high >= low),
    CHECK (close > 0)
);

-- Secondary index for date-range scans across all assets
-- (correlation queries, market-wide snapshots on a given day).
CREATE INDEX idx_price_dt ON price_daily (dt);

-- Helper index for the common "latest bar per asset" pattern.
CREATE INDEX idx_price_asset_dt_desc ON price_daily (asset_id, dt DESC);
