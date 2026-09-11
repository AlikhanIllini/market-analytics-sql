# Market Analytics (SQL)

A time-series analytics engine for equities, ETFs, and crypto, built on
PostgreSQL. Real market data is loaded into a normalized schema, and every
metric (returns, rolling volatility, moving-average crossovers, drawdowns,
and cross-asset correlations) is computed in SQL using window functions and
CTEs. A small Streamlit dashboard sits on top to visualize the output.

The point of the project is the SQL. The Python is just plumbing to get data
in and pixels out.

![dashboard preview](dashboard/preview.png)

## What it shows

- **Window functions over time series**: `LAG` for returns, trailing-frame
  `STDDEV` for rolling volatility, running `MAX` for drawdowns, frame-bounded
  `AVG` for moving averages.
- **CTEs and signal logic**: a 50/200-day moving-average crossover detector
  that flags golden and death crosses by comparing the current and prior gap.
- **Backtesting without lookahead**: a 50/200 crossover backtest that sets each
  day's position from the *prior* day's signal (`LAG`), charges a 10 bps cost on
  every trade, and compounds daily returns into strategy and buy-and-hold equity
  curves as `EXP(SUM(LN(1 + r)))` (the `v_backtest` view).
- **Cross-asset analysis**: a correlation matrix built with a dated self-join
  and the `CORR` aggregate.
- **Schema design**: a normalized `asset` / `price_daily` model with a
  composite primary key, check constraints, and indexes tuned for the two
  dominant access patterns (per-asset time scans and market-wide date scans).

## Data

Eight assets: AAPL, MSFT, NVDA, SPY, QQQ (equities and ETFs) plus BTC, ETH,
and TON (crypto).

Two ways to load:

- **Real data** (`make load-real`): equities and ETFs come from Stooq, crypto
  from CoinGecko. Both are free and need no API key.
- **Offline sample** (`make load-seed`): a bundled synthetic dataset so the
  project runs with no network. The sample series are generated with a shared
  market factor, so equities correlate tightly (~0.85 to 0.93) and crypto
  stays loosely coupled, matching how real markets behave. The numbers are
  illustrative, not actual prices.

## Quick start

```bash
# 1. start postgres
docker compose up -d

# 2. install deps
pip install -r requirements.txt

# 3. load the offline sample (or `make load-real` for live data)
make load-seed

# 4. run the analytical queries
psql postgresql://quant:quant@localhost:5432/market -f queries/01_performance_summary.sql

# 5. launch the dashboard
make dashboard
```

## Layout

```
schema/
  01_schema.sql            tables, constraints, indexes
  02_views.sql             analytical views (returns, vol, MAs, drawdown, backtest)
queries/
  01_performance_summary.sql   annualized return, vol, Sharpe-style ranking
  02_correlation_matrix.sql    pairwise return correlation
  03_max_drawdown.sql          deepest peak-to-trough per asset
  04_recent_signals.sql        latest MA crossover per asset
  05_backtest_ma_crossover.sql crossover strategy vs buy-and-hold, after costs
data/
  load_data.py             builds schema, loads real or seed data
  generate_seed.py         creates the offline sample CSVs
  seed/                    bundled sample data
dashboard/
  app.py                   streamlit + plotly front end
```

## Sample output

`01_performance_summary.sql` ranks assets by a Sharpe-style ratio:

```
 symbol | asset_class | ann_return_pct | ann_vol_pct | sharpe_like | sharpe_rank
--------+-------------+----------------+-------------+-------------+-------------
 NVDA   | equity      |          36.08 |       27.84 |        1.30 |           2
 MSFT   | equity      |           7.44 |       17.89 |        0.42 |           3
 ...
```

(Values shown are from the offline sample dataset.)
