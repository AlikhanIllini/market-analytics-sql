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

- **Live data** (`make load-real`): equities and ETFs come from Yahoo Finance's
  public chart endpoint (split-adjusted daily bars), crypto from Kraken's public
  OHLC endpoint. Both are free and need no API key. If a source fails, the
  loader falls back to the snapshot below and prints a warning naming the asset.
- **Offline snapshot** (`make load-seed`): the same real prices saved to
  `data/seed/`, covering September 2024 through September 2026, so the project
  runs with no network. `make seed` refreshes the snapshot from live data and
  refuses to overwrite it if any fetch fails.

## Quick start

```bash
# 1. start postgres
docker compose up -d

# 2. install deps
pip install -r requirements.txt

# 3. load live data (or `make load-seed` to run offline from the snapshot)
make load-real

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
  load_data.py             builds schema, loads live data or the snapshot
  seed/                    real price snapshot for offline use
dashboard/
  app.py                   streamlit + plotly front end
  make_preview.py          renders preview.png from the SQL views
```

## Sample output

`01_performance_summary.sql` ranks assets by a Sharpe-style ratio:

```
 symbol | asset_class | trading_days | ann_return_pct | ann_vol_pct | sharpe_like | sharpe_rank
--------+-------------+--------------+----------------+-------------+-------------+-------------
 QQQ    | etf         |          500 |          23.19 |       21.68 |        1.07 |           1
 SPY    | etf         |          500 |          17.11 |       16.52 |        1.04 |           2
 NVDA   | equity      |          500 |          41.29 |       44.25 |        0.93 |           3
 AAPL   | equity      |          500 |          23.48 |       28.98 |        0.81 |           4
 MSFT   | equity      |          500 |          11.76 |       28.87 |        0.41 |           5
 BTC    | crypto      |          719 |          13.41 |       36.93 |        0.36 |           6
 ETH    | crypto      |          719 |          14.16 |       58.01 |        0.24 |           7
 TON    | crypto      |          699 |         -29.04 |       63.19 |       -0.46 |           8
```

(Values from the bundled snapshot of real prices, September 2024 to September 2026.)
