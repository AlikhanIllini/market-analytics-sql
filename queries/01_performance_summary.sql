-- ============================================================
-- 01_performance_summary.sql
-- Per-asset performance and risk summary over the full history.
-- Demonstrates: aggregate window math, Sharpe-style ratio,
-- and ranking with RANK() over a derived metric.
-- ============================================================

WITH stats AS (
    SELECT
        symbol,
        asset_class,
        COUNT(*)                                   AS trading_days,
        AVG(simple_return)                         AS avg_daily_return,
        STDDEV_SAMP(simple_return)                 AS daily_vol,
        AVG(simple_return) * 252                   AS ann_return,
        STDDEV_SAMP(simple_return) * SQRT(252)     AS ann_vol
    FROM v_daily_return
    WHERE simple_return IS NOT NULL
    GROUP BY symbol, asset_class
)
SELECT
    symbol,
    asset_class,
    trading_days,
    ROUND((ann_return * 100)::NUMERIC, 2)          AS ann_return_pct,
    ROUND((ann_vol    * 100)::NUMERIC, 2)          AS ann_vol_pct,
    -- Risk-adjusted return, risk-free rate assumed zero for simplicity.
    ROUND((ann_return / NULLIF(ann_vol, 0))::NUMERIC, 2) AS sharpe_like,
    RANK() OVER (ORDER BY ann_return / NULLIF(ann_vol, 0) DESC) AS sharpe_rank
FROM stats
ORDER BY sharpe_rank;
