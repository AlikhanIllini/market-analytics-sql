-- ============================================================
-- 04_recent_signals.sql
-- The most recent moving-average crossover for each asset.
-- Demonstrates: filtering window output, DISTINCT ON for the
-- latest event, and date arithmetic for recency.
-- ============================================================

SELECT DISTINCT ON (symbol)
    symbol,
    dt              AS signal_date,
    cross_signal,
    ROUND(close,   2) AS close,
    ROUND(sma_50,  2) AS sma_50,
    ROUND(sma_200, 2) AS sma_200,
    (CURRENT_DATE - dt) AS days_ago
FROM v_moving_avg
WHERE cross_signal IS NOT NULL
ORDER BY symbol, dt DESC;
