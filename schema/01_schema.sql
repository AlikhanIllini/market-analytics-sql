-- ============================================================
-- 01_schema.sql
-- Core tables for the market analytics database.
-- Covers equities, ETFs, and crypto in one normalized model.
-- ============================================================

DROP TABLE IF EXISTS price_daily CASCADE;
DROP TABLE IF EXISTS risk_free_daily CASCADE;
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
    -- Close adjusted for dividends as well as splits. Returns are computed
    -- from this column so they are total returns; charts and moving averages
    -- use the plain close. For crypto the two are equal.
    adj_close  NUMERIC(18, 6) NOT NULL,
    volume     NUMERIC(20, 2),
    PRIMARY KEY (asset_id, dt),
    CHECK (high >= low),
    CHECK (close > 0),
    CHECK (adj_close > 0)
);

-- Risk-free rate: the 13-week US Treasury bill yield, annualized, stored
-- as a decimal (0.04 = 4%). One row per day the bill market was open.
-- Used to turn returns into excess returns for the Sharpe ratio.
CREATE TABLE risk_free_daily (
    dt            DATE PRIMARY KEY,
    annual_yield  NUMERIC(10, 6) NOT NULL CHECK (annual_yield > -0.05 AND annual_yield < 1)
);

-- Secondary index for date-range scans across all assets
-- (correlation queries, market-wide snapshots on a given day).
CREATE INDEX idx_price_dt ON price_daily (dt);

-- Helper index for the common "latest bar per asset" pattern.
CREATE INDEX idx_price_asset_dt_desc ON price_daily (asset_id, dt DESC);
