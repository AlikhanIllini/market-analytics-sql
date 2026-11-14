# Market Analytics (SQL)

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://market-analytics-sql.streamlit.app/)

**Live dashboard: [market-analytics-sql.streamlit.app](https://market-analytics-sql.streamlit.app/)**
(Streamlit Community Cloud on a Neon Postgres database. The app sleeps when
idle, so the first load can take a few seconds.)

A time-series analytics engine for equities, ETFs, and crypto, built on
PostgreSQL. Real market data is loaded into a normalized schema, and every
metric (returns, rolling volatility, moving-average crossovers, drawdowns,
and cross-asset correlations) is computed in SQL using window functions and
CTEs. A small Streamlit dashboard sits on top to visualize the output.

The point of the project is the SQL. The Python is just plumbing to get data
in and pixels out.

[![dashboard preview](dashboard/preview.png)](https://market-analytics-sql.streamlit.app/)

## What it shows

- **Window functions over time series**: `LAG` for returns, trailing-frame
  `STDDEV` for rolling volatility, running `MAX` for drawdowns, frame-bounded
  `AVG` for moving averages.
- **CTEs and signal logic**: a 50/200-day moving-average crossover detector
  that flags golden and death crosses by comparing the current and prior gap.
  Averages stay `NULL` until their full window exists (a `COUNT(*)` over the
  same frame), so no signal fires off a partial "200-day" average.
- **Backtesting without lookahead**: a 50/200 crossover backtest that sets each
  day's position from the *prior* day's signal (`LAG`), charges a 10 bps cost on
  every trade, and compounds daily returns into strategy and buy-and-hold equity
  curves as `EXP(SUM(LN(1 + r)))` (the `v_backtest` view). The test starts once
  200 days of history exist, and buy-and-hold is measured over the same window.
- **As-of join for the Sharpe ratio**: each daily return picks up the latest
  13-week T-bill yield on or before its date with a `LATERAL` subquery, and the
  Sharpe ratio is computed from those excess returns (`v_excess_return`,
  `v_performance_summary`).
- **Cross-asset analysis**: a correlation matrix built with a dated self-join
  and the `CORR` aggregate.
- **Schema design**: a normalized `asset` / `price_daily` model with a
  composite primary key, check constraints, and indexes tuned for the two
  dominant access patterns (per-asset time scans and market-wide date scans).

## Conventions

- **Total returns.** Returns use the dividend-adjusted close. Price charts,
  moving averages, and drawdowns use the plain (split-adjusted) close.
- **Annualization.** 252 periods a year for stocks and ETFs, 365 for crypto,
  which trades every day. The factor is defined once, in `v_daily_return`.
- **Sharpe ratio.** Mean excess return over the 13-week Treasury bill, divided
  by the standard deviation of excess returns, annualized.
- **Annualized return** is the arithmetic mean daily return times the periods
  per year, not a compound growth rate.

## Data

Eight assets: AAPL, MSFT, NVDA, SPY, QQQ (equities and ETFs) plus BTC, ETH,
and TON (crypto).

Two ways to load:

- **Live data** (`make load-real`): equities and ETFs come from Yahoo Finance's
  public chart endpoint (daily bars with a dividend-adjusted close), crypto from
  Kraken's public OHLC endpoint, and the risk-free rate from Yahoo's `^IRX`
  (13-week T-bill yield). All are free and need no API key. Only completed
  daily bars are stored: a bar for a session that is still trading is dropped.
  If a source fails, the loader falls back to the snapshot below and prints a
  warning naming the asset.
- **Offline snapshot** (`make load-seed`): the same real data saved to
  `data/seed/`, covering November 2024 through November 2026, so the project
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

## Hosted database (Neon) and deploy

The app and the scripts look for the database in this order: `DATABASE_URL`
in Streamlit secrets (app only), the `DATABASE_URL` environment variable,
`DATABASE_URL` in `.streamlit/secrets.toml`, then the local Docker database.

To run against a Neon database (Python 3.11 or newer):

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# paste your Neon connection string into .streamlit/secrets.toml
make load-real      # builds the schema and views in Neon and loads prices
make dashboard
```

`.streamlit/secrets.toml` is git-ignored. To deploy on Streamlit Community
Cloud, point the app at `dashboard/app.py` and paste the same
`DATABASE_URL = "..."` line into the app's Secrets box.

### Keeping the live data fresh

`.github/workflows/refresh-data.yml` reloads prices and rebuilds the views in
the hosted database every day at 00:30 UTC, after US markets close. It needs
one repository secret, `DATABASE_URL`, holding the same connection string
(Settings, Secrets and variables, Actions). It can also be run by hand from
the Actions tab.

The scheduled load runs with `--strict`: if any source fails, it aborts
without committing, so the previous data stays in place instead of being
replaced by the offline snapshot. The dashboard shows the date of the latest
bar under its title.

## Layout

```
schema/
  01_schema.sql            tables, constraints, indexes
  02_views.sql             analytical views (returns, excess returns, vol, MAs,
                           drawdown, backtest, performance summary)
queries/
  01_performance_summary.sql   annualized return, vol, Sharpe ranking
  02_correlation_matrix.sql    pairwise return correlation
  03_max_drawdown.sql          deepest peak-to-trough per asset
  04_recent_signals.sql        latest MA crossover per asset
  05_backtest_ma_crossover.sql crossover strategy vs buy-and-hold, after costs
data/
  load_data.py             builds schema, loads live data or the snapshot
  seed/                    real price and T-bill snapshot for offline use
.github/workflows/
  refresh-data.yml         daily reload of the hosted database
dashboard/
  app.py                   streamlit + plotly front end
  make_preview.py          renders preview.png from the SQL views
```

## Sample output

`01_performance_summary.sql` ranks assets by Sharpe ratio:

```
 symbol | asset_class | trading_days | ann_return_pct | ann_vol_pct | risk_free_pct | sharpe | sharpe_rank
--------+-------------+--------------+----------------+-------------+---------------+--------+-------------
 AAPL   | equity      |          500 |          27.03 |       29.01 |          3.92 |   0.80 |           1
 SPY    | etf         |          500 |          14.67 |       16.89 |          3.92 |   0.64 |           2
 QQQ    | etf         |          500 |          17.69 |       22.66 |          3.92 |   0.61 |           3
 MSFT   | equity      |          500 |          15.40 |       29.54 |          3.92 |   0.39 |           4
 NVDA   | equity      |          500 |          15.38 |       44.70 |          3.92 |   0.26 |           5
 ETH    | crypto      |          719 |          -4.88 |       68.76 |          3.91 |  -0.13 |           6
 BTC    | crypto      |          719 |          -8.97 |       43.37 |          3.91 |  -0.30 |           7
 TON    | crypto      |          719 |         -59.04 |       75.73 |          3.91 |  -0.83 |           8
```

(Values from the bundled snapshot of real data, November 2024 to November 2026.)
