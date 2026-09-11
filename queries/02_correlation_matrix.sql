-- ============================================================
-- 02_correlation_matrix.sql
-- Pairwise return correlation across every asset.
-- Demonstrates: self-join on a date key, corr() aggregate,
-- and filtering the matrix to one half to avoid duplicate pairs.
-- ============================================================

WITH r AS (
    SELECT asset_id, symbol, dt, simple_return
    FROM v_daily_return
    WHERE simple_return IS NOT NULL
)
SELECT
    a.symbol                              AS asset_a,
    b.symbol                              AS asset_b,
    COUNT(*)                              AS overlapping_days,
    ROUND(CORR(a.simple_return, b.simple_return)::NUMERIC, 3) AS correlation
FROM r a
JOIN r b
  ON a.dt = b.dt          -- align on the same trading day
 AND a.asset_id < b.asset_id   -- one direction only, no self-pairs
GROUP BY a.symbol, b.symbol
HAVING COUNT(*) >= 30      -- require a meaningful overlap window
ORDER BY correlation DESC;
