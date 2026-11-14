-- ============================================================
-- 01_performance_summary.sql
-- Per-asset performance and risk summary over the full history.
-- Demonstrates: ranking with RANK() over a derived metric, on top
-- of v_performance_summary (aggregates over an as-of join to the
-- risk-free rate; see schema/02_views.sql).
--
-- Returns are total returns. Annualization uses 252 periods for
-- stocks and ETFs and 365 for crypto. Sharpe is the annualized
-- mean excess return over the 13-week T-bill, divided by the
-- annualized standard deviation of excess returns.
-- ============================================================

SELECT
    symbol,
    asset_class,
    trading_days,
    ROUND((ann_return    * 100)::NUMERIC, 2)   AS ann_return_pct,
    ROUND((ann_vol       * 100)::NUMERIC, 2)   AS ann_vol_pct,
    ROUND((ann_risk_free * 100)::NUMERIC, 2)   AS risk_free_pct,
    ROUND(sharpe::NUMERIC, 2)                  AS sharpe,
    RANK() OVER (ORDER BY sharpe DESC)         AS sharpe_rank
FROM v_performance_summary
ORDER BY sharpe_rank;
