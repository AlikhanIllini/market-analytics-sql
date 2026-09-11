-- ============================================================
-- 03_max_drawdown.sql
-- Worst peak-to-trough drawdown per asset, plus the dates that
-- bound it. Demonstrates: DISTINCT ON, ordering tricks, and
-- pulling the row at an extremum rather than just the value.
-- ============================================================

-- For each asset, find the single deepest drawdown day and the
-- peak date that preceded it.
WITH dd AS (
    SELECT
        asset_id,
        symbol,
        dt,
        close,
        running_peak,
        drawdown
    FROM v_drawdown
),
worst AS (
    -- DISTINCT ON keeps the first row per asset after ordering,
    -- so ordering by drawdown ascending lands on the deepest dip.
    SELECT DISTINCT ON (asset_id)
        asset_id,
        symbol,
        dt              AS trough_date,
        close           AS trough_close,
        running_peak    AS peak_close,
        drawdown
    FROM dd
    ORDER BY asset_id, drawdown ASC
)
SELECT
    symbol,
    trough_date,
    ROUND(peak_close,   2)        AS peak_close,
    ROUND(trough_close, 2)        AS trough_close,
    ROUND(drawdown * 100, 2)      AS max_drawdown_pct
FROM worst
ORDER BY max_drawdown_pct ASC;
