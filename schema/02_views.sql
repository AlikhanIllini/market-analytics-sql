-- ============================================================
-- 02_views.sql
-- Analytical views built on price_daily.
-- These are the heart of the project: window functions, frames,
-- and time-series logic expressed in pure SQL.
-- ============================================================

-- ------------------------------------------------------------
-- Daily simple and log returns.
-- LAG pulls the prior bar within each asset, ordered by date.
-- NULLIF guards against a divide-by-zero on bad data.
--
-- Returns use adj_close, so they are total returns (dividends
-- included), not just price changes.
--
-- periods_per_year is the annualization factor: stocks and ETFs
-- have about 252 bars a year, crypto trades every day and has 365.
-- Every annualized number downstream reads it from here, so the
-- convention is defined once.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_daily_return CASCADE;
CREATE VIEW v_daily_return AS
SELECT
    p.asset_id,
    a.symbol,
    a.asset_class,
    CASE WHEN a.asset_class = 'crypto' THEN 365 ELSE 252 END  AS periods_per_year,
    p.dt,
    p.close,
    p.adj_close,
    LAG(p.adj_close) OVER w                                   AS prev_adj_close,
    p.adj_close / NULLIF(LAG(p.adj_close) OVER w, 0) - 1      AS simple_return,
    LN(p.adj_close / NULLIF(LAG(p.adj_close) OVER w, 0))      AS log_return
FROM price_daily p
JOIN asset a USING (asset_id)
WINDOW w AS (PARTITION BY p.asset_id ORDER BY p.dt);

-- ------------------------------------------------------------
-- Excess returns over the risk-free rate.
-- An as-of join: each return row picks up the latest T-bill yield
-- on or before its date (LATERAL + ORDER BY ... LIMIT 1), which
-- covers weekends and bond-market holidays without forward-filling
-- the rate table. The annual yield is spread evenly over the
-- asset's periods_per_year to get a per-bar rate.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_excess_return CASCADE;
CREATE VIEW v_excess_return AS
SELECT
    r.asset_id,
    r.symbol,
    r.asset_class,
    r.periods_per_year,
    r.dt,
    r.simple_return,
    rf.annual_yield / r.periods_per_year                      AS rf_return,
    r.simple_return - rf.annual_yield / r.periods_per_year    AS excess_return
FROM v_daily_return r
LEFT JOIN LATERAL (
    SELECT f.annual_yield
    FROM risk_free_daily f
    WHERE f.dt <= r.dt
    ORDER BY f.dt DESC
    LIMIT 1
) rf ON TRUE
WHERE r.simple_return IS NOT NULL;

-- ------------------------------------------------------------
-- Per-asset performance summary over the full history.
-- Annualized return and volatility, and a Sharpe ratio computed
-- from excess returns (mean excess return over its standard
-- deviation, annualized with the asset's own periods_per_year).
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_performance_summary CASCADE;
CREATE VIEW v_performance_summary AS
SELECT
    symbol,
    asset_class,
    periods_per_year,
    COUNT(*)                                                  AS trading_days,
    AVG(simple_return) * periods_per_year                     AS ann_return,
    STDDEV_SAMP(simple_return) * SQRT(periods_per_year)       AS ann_vol,
    AVG(rf_return) * periods_per_year                         AS ann_risk_free,
    AVG(excess_return) * periods_per_year                     AS ann_excess_return,
    AVG(excess_return) / NULLIF(STDDEV_SAMP(excess_return), 0)
        * SQRT(periods_per_year)                              AS sharpe
FROM v_excess_return
GROUP BY symbol, asset_class, periods_per_year;

-- ------------------------------------------------------------
-- Rolling 20-day annualized volatility.
-- STDDEV over a 20-row trailing frame, scaled by the square root
-- of periods_per_year to annualize daily standard deviation.
-- NULL until 20 returns exist, for the same reason the moving
-- averages wait for a full window: a 2-day "20-day vol" is noise.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_rolling_vol CASCADE;
CREATE VIEW v_rolling_vol AS
SELECT
    asset_id,
    symbol,
    dt,
    simple_return,
    CASE WHEN COUNT(simple_return) OVER w = 20
         THEN STDDEV_SAMP(simple_return) OVER w * SQRT(periods_per_year)
    END AS vol_20d_ann
FROM v_daily_return
WINDOW w AS (PARTITION BY asset_id ORDER BY dt
             ROWS BETWEEN 19 PRECEDING AND CURRENT ROW);

-- ------------------------------------------------------------
-- Moving averages and a crossover signal.
-- 50-day vs 200-day SMA. The signal flips to 'golden' when the
-- fast average crosses above the slow one, and 'death' on the
-- way back down. Prior-row comparison via LAG detects the cross.
--
-- Warm-up: an average is NULL until its full window exists (50 or
-- 200 bars). A ROWS frame happily averages a partial window, which
-- would make the "200-day" line a 10-day or 60-day average early on
-- and fire crossovers that never happened. COUNT(*) over the same
-- frame is the guard. No signal can fire until day 201, when both
-- the current and the prior gap are real.
-- ------------------------------------------------------------
DROP VIEW IF EXISTS v_moving_avg CASCADE;
CREATE VIEW v_moving_avg AS
WITH ma AS (
    SELECT
        asset_id,
        symbol,
        dt,
        close,
        CASE WHEN COUNT(*) OVER w50  = 50  THEN AVG(close) OVER w50  END AS sma_50,
        CASE WHEN COUNT(*) OVER w200 = 200 THEN AVG(close) OVER w200 END AS sma_200
    FROM price_daily
    JOIN asset USING (asset_id)
    WINDOW w50  AS (PARTITION BY asset_id ORDER BY dt
                    ROWS BETWEEN 49  PRECEDING AND CURRENT ROW),
           w200 AS (PARTITION BY asset_id ORDER BY dt
                    ROWS BETWEEN 199 PRECEDING AND CURRENT ROW)
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
-- queries/05_backtest_ma_crossover.sql aggregates this view down to one row
-- per asset; this view exposes the running equity curve.
--
-- Position logic is lookahead-free: the signal (SMA50 > SMA200) is taken from
-- the PRIOR day via LAG, so the strategy only ever trades on information it
-- could have known at the time.
--
-- Tested window: the backtest starts on the first day a full 200-day average
-- exists (see the warm-up note on v_moving_avg). Buy-and-hold is measured over
-- that same window, so the two curves are comparable. Returns are total
-- returns (v_daily_return), signals come from the price averages.
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
    WHERE sma_200 IS NOT NULL            -- skip the 200-day warm-up
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
        -- treated as 0 (COALESCE on both sides). Comparing the first row against
        -- NULL instead would book a spurious day-one trade and charge a
        -- transaction cost for a trade that never happened.
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
