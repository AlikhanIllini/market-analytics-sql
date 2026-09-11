-- ============================================================
-- 02_views.sql
-- Analytical views built on price_daily.
-- These are the heart of the project: window functions, frames,
-- and time-series logic expressed in pure SQL.
-- ============================================================

-- ------------------------------------------------------------
-- Daily simple and log returns.
-- LAG pulls the prior close within each asset, ordered by date.
-- NULLIF guards against a divide-by-zero on bad data.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_daily_return CASCADE;
CREATE VIEW v_daily_return AS
SELECT
    p.asset_id,
    a.symbol,
    a.asset_class,
    p.dt,
    p.close,
    LAG(p.close) OVER w                                   AS prev_close,
    p.close / NULLIF(LAG(p.close) OVER w, 0) - 1          AS simple_return,
    LN(p.close / NULLIF(LAG(p.close) OVER w, 0))          AS log_return
FROM price_daily p
JOIN asset a USING (asset_id)
WINDOW w AS (PARTITION BY p.asset_id ORDER BY p.dt);

-- ------------------------------------------------------------
-- Rolling 20-day annualized volatility.
-- STDDEV over a 20-row trailing frame, scaled by sqrt(252)
-- to annualize daily standard deviation.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_rolling_vol CASCADE;
CREATE VIEW v_rolling_vol AS
SELECT
    asset_id,
    symbol,
    dt,
    simple_return,
    STDDEV_SAMP(simple_return) OVER (
        PARTITION BY asset_id ORDER BY dt
        ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
    ) * SQRT(252) AS vol_20d_ann
FROM v_daily_return;

-- ------------------------------------------------------------
-- Moving averages and a crossover signal.
-- 50-day vs 200-day SMA. The signal flips to 'golden' when the
-- fast average crosses above the slow one, and 'death' on the
-- way back down. Prior-row comparison via LAG detects the cross.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_moving_avg CASCADE;
CREATE VIEW v_moving_avg AS
WITH ma AS (
    SELECT
        asset_id,
        symbol,
        dt,
        close,
        AVG(close) OVER (PARTITION BY asset_id ORDER BY dt
                         ROWS BETWEEN 49  PRECEDING AND CURRENT ROW) AS sma_50,
        AVG(close) OVER (PARTITION BY asset_id ORDER BY dt
                         ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma_200
    FROM price_daily
    JOIN asset USING (asset_id)
),
spread AS (
    SELECT
        ma.*,
        sma_50 - sma_200                                       AS gap,
        LAG(sma_50 - sma_200) OVER (PARTITION BY asset_id ORDER BY dt) AS prev_gap
    FROM ma
)
SELECT
    asset_id, symbol, dt, close, sma_50, sma_200, gap,
    CASE
        WHEN gap > 0 AND prev_gap <= 0 THEN 'golden_cross'
        WHEN gap < 0 AND prev_gap >= 0 THEN 'death_cross'
        ELSE NULL
    END AS cross_signal
FROM spread;

-- ------------------------------------------------------------
-- Drawdown from the running peak.
-- The running MAX of close gives the all-time-high-to-date.
-- Drawdown is how far below that peak the asset currently sits.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_drawdown CASCADE;
CREATE VIEW v_drawdown AS
SELECT
    asset_id,
    symbol,
    dt,
    close,
    MAX(close) OVER (PARTITION BY asset_id ORDER BY dt
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_peak,
    close / MAX(close) OVER (PARTITION BY asset_id ORDER BY dt
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) - 1 AS drawdown
FROM price_daily
JOIN asset USING (asset_id);

-- ------------------------------------------------------------
-- Daily backtest equity curves: 50/200 MA crossover vs buy-and-hold.
-- This is the daily, per-day companion to queries/05_backtest_ma_crossover.sql,
-- exposing the running equity curve rather than just the final return.
--
-- Position logic is identical to query 05 and lookahead-free: the signal
-- (SMA50 > SMA200) is taken from the PRIOR day via LAG, so the strategy only
-- ever trades on information it could have known at the time.
--
-- Transaction cost: a flat 10 bps (0.0010) is charged on every position flip
-- -- entering or exiting -- and subtracted from that day's strategy return.
-- A full round trip (in then out) therefore costs ~20 bps. The view exposes
-- both the gross (cost-free) and net (after-cost) strategy curves so the drag
-- is visible side by side.
--
-- Each equity curve is the running geometric product of daily returns,
-- EXP(SUM(LN(1 + r))) over an expanding window, i.e. the growth of $1 invested
-- at the start of the tested window.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_backtest CASCADE;
CREATE VIEW v_backtest AS
WITH signal AS (
    SELECT
        asset_id,
        symbol,
        dt,
        CASE WHEN sma_50 > sma_200 THEN 1 ELSE 0 END AS raw_position
    FROM v_moving_avg
    WHERE sma_200 IS NOT NULL            -- mirror query 05's guard
),
positioned AS (
    SELECT
        s.*,
        -- yesterday's signal drives today's position (no lookahead)
        LAG(raw_position) OVER (PARTITION BY asset_id ORDER BY dt) AS position
    FROM signal s
),
returns AS (
    SELECT
        p.asset_id,
        p.symbol,
        p.dt,
        COALESCE(p.position, 0)                 AS position,
        r.simple_return                         AS bh_return,
        COALESCE(p.position, 0) * r.simple_return AS strat_return,
        -- A trade is any day the held position changes versus the day before.
        -- The strategy starts flat, so the position before the first row is
        -- treated as 0 (COALESCE on both sides). This is deliberately stricter
        -- than query 05, whose flip test compares the first row against NULL
        -- and books a spurious day-one trade -- which here would charge a
        -- transaction cost for a trade that never actually happened.
        CASE WHEN COALESCE(p.position, 0) IS DISTINCT FROM
                  COALESCE(LAG(p.position) OVER (PARTITION BY p.asset_id ORDER BY p.dt), 0)
             THEN 1 ELSE 0 END                  AS traded
    FROM positioned p
    JOIN v_daily_return r
      ON r.asset_id = p.asset_id AND r.dt = p.dt
    WHERE r.simple_return IS NOT NULL
),
net AS (
    SELECT
        r.*,
        0.0010::NUMERIC                                  AS cost_per_trade,  -- 10 bps
        r.strat_return - r.traded * 0.0010::NUMERIC      AS strat_return_net
    FROM returns r
)
SELECT
    asset_id,
    symbol,
    dt,
    position,
    traded,
    bh_return,
    strat_return                                  AS strat_return_gross,
    strat_return_net,
    -- running compounded equity curves: growth of $1 over the window
    EXP(SUM(LN(1 + bh_return))        OVER w)     AS equity_buy_hold,
    EXP(SUM(LN(1 + strat_return))     OVER w)     AS equity_strategy_gross,
    EXP(SUM(LN(1 + strat_return_net)) OVER w)     AS equity_strategy_net
FROM net
WINDOW w AS (PARTITION BY asset_id ORDER BY dt
             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW);
