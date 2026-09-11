-- ============================================================
-- 05_backtest_ma_crossover.sql
-- Backtest a 50/200 moving-average crossover strategy and compare
-- it to buy-and-hold for every asset, before and after costs.
--
-- Rule: hold the asset when SMA50 > SMA200, otherwise sit in cash.
-- The position is set from the PRIOR day's signal so the strategy
-- only trades on information it could actually have known. This is
-- the single most common backtest bug (lookahead), avoided with LAG.
--
-- The lookahead-free position logic, the equity curves, and the
-- transaction-cost model all live in the v_backtest view (see
-- schema/02_views.sql); this query just aggregates that view down to
-- one row per asset. Equity is compounded in SQL as EXP(SUM(LN(1+r))),
-- the running geometric product of daily returns.
--
-- Transaction cost: 10 bps is charged on every position flip. The
-- gross column is cost-free; the net column pays the cost; cost_drag
-- is the gap between them -- i.e. exactly what trading frictions take
-- off the table.
-- ============================================================

SELECT
    symbol,
    COUNT(*)                                                      AS days_tested,
    SUM(traded)                                                   AS trades,
    -- cost-free strategy return (matches the original backtest)
    ROUND((EXP(SUM(LN(1 + strat_return_gross))) - 1)::NUMERIC * 100, 1) AS strategy_gross_pct,
    -- strategy return after paying 10 bps on every flip
    ROUND((EXP(SUM(LN(1 + strat_return_net)))   - 1)::NUMERIC * 100, 1) AS strategy_net_pct,
    -- what the transaction costs alone removed (gross minus net)
    ROUND((EXP(SUM(LN(1 + strat_return_gross))) -
           EXP(SUM(LN(1 + strat_return_net))))::NUMERIC  * 100, 1)      AS cost_drag_pct,
    ROUND((EXP(SUM(LN(1 + bh_return)))          - 1)::NUMERIC * 100, 1) AS buy_hold_pct,
    -- after-cost edge over simply buying and holding
    ROUND((EXP(SUM(LN(1 + strat_return_net))) -
           EXP(SUM(LN(1 + bh_return))))::NUMERIC          * 100, 1)     AS net_outperformance_pct,
    -- fraction of days the strategy was actually invested
    ROUND(AVG(position::NUMERIC) * 100, 0)                       AS time_in_market_pct
FROM v_backtest
GROUP BY symbol
ORDER BY net_outperformance_pct DESC;
