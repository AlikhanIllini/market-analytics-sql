"""
load_data.py
Build the database and load price history.

Usage:
    python load_data.py              # pull real data from public APIs
    python load_data.py --seed       # load bundled offline CSVs (no network)
    python load_data.py --write-seed # pull real data and refresh the bundled CSVs
    python load_data.py --strict     # pull real data; fail instead of falling back

Real sources, all free and key-less:
    equities / ETFs -> Yahoo Finance chart endpoint (daily bars, with a
                       dividend-adjusted close for total returns)
    crypto          -> Kraken public OHLC (last 720 daily bars, priced in USD)
    risk-free rate  -> Yahoo Finance ^IRX (13-week Treasury bill yield)

Only completed daily bars are stored. A bar for a session that is still
trading is dropped, so a load in the middle of the day cannot store an
intraday price as that day's close.

Connection comes from the DATABASE_URL env var, or from DATABASE_URL in
.streamlit/secrets.toml, or falls back to the local Docker database at
    postgresql://quant:quant@localhost:5432/market
"""

import argparse
import os
import time
import tomllib
from pathlib import Path

import pandas as pd
import requests
import psycopg2
from psycopg2.extras import execute_values

HERE = Path(__file__).parent
SCHEMA_DIR = HERE.parent / "schema"
SEED_DIR = HERE / "seed"
LOCAL_URL = "postgresql://quant:quant@localhost:5432/market"
SECRETS_FILE = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"


def database_url():
    """DATABASE_URL env var first, then DATABASE_URL in .streamlit/secrets.toml
    (the same file the dashboard reads), then the local Docker database."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if SECRETS_FILE.exists():
        with open(SECRETS_FILE, "rb") as f:
            url = tomllib.load(f).get("DATABASE_URL")
        if url:
            return url
    return LOCAL_URL


# symbol -> (name, asset_class, source_id)
# source_id is the Yahoo ticker or the Kraken pair.
UNIVERSE = {
    "AAPL": ("Apple Inc.", "equity", "AAPL"),
    "MSFT": ("Microsoft Corp.", "equity", "MSFT"),
    "NVDA": ("NVIDIA Corp.", "equity", "NVDA"),
    "SPY":  ("SPDR S&P 500 ETF", "etf", "SPY"),
    "QQQ":  ("Invesco QQQ ETF", "etf", "QQQ"),
    "BTC":  ("Bitcoin", "crypto", "XBTUSD"),
    "ETH":  ("Ethereum", "crypto", "ETHUSD"),
    "TON":  ("Toncoin", "crypto", "TONUSD"),
}

COLUMNS = ["dt", "open", "high", "low", "close", "adj_close", "volume"]
RISK_FREE_ID = "^IRX"                 # 13-week T-bill yield, quoted in percent
RISK_FREE_SEED = SEED_DIR / "_risk_free.csv"


def run_sql_file(cur, path):
    cur.execute(Path(path).read_text())


def with_retries(fetch, *args, attempts=3, **kwargs):
    """Call a fetcher, retrying with a short backoff. Public endpoints
    occasionally rate-limit or time out; one retry usually clears it."""
    for attempt in range(1, attempts + 1):
        try:
            return fetch(*args, **kwargs)
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(2 * attempt)


def yahoo_chart(source_id, period):
    """One result block from Yahoo's public chart endpoint, with any bar
    for a session that is still trading removed from its timestamps."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{source_id}"
    r = requests.get(
        url,
        params={"range": period, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]

    # Yahoo includes today's bar while the market is open, with the latest
    # trade standing in for the close. Count it only once the session ends.
    keep = len(res["timestamp"])
    session = res.get("meta", {}).get("currentTradingPeriod", {}).get("regular", {})
    if session and session["start"] <= res["timestamp"][-1] and time.time() < session["end"]:
        keep -= 1
    return res, keep


def fetch_yahoo(source_id, period="2y"):
    """Daily OHLCV from Yahoo. close is split-adjusted; adj_close is also
    adjusted for dividends, which is what total returns are built from."""
    res, keep = yahoo_chart(source_id, period)
    quote = res["indicators"]["quote"][0]

    return pd.DataFrame({
        "dt": pd.to_datetime(res["timestamp"], unit="s").strftime("%Y-%m-%d"),
        "open": quote["open"],
        "high": quote["high"],
        "low": quote["low"],
        "close": quote["close"],
        "adj_close": res["indicators"]["adjclose"][0]["adjclose"],
        "volume": quote["volume"],
    }).iloc[:keep]


def fetch_risk_free(period="5y"):
    """13-week T-bill yield as an annual decimal (0.04 = 4%). Pulled over a
    longer range than prices so every return date has a rate on or before it."""
    res, keep = yahoo_chart(RISK_FREE_ID, period)
    df = pd.DataFrame({
        "dt": pd.to_datetime(res["timestamp"], unit="s").strftime("%Y-%m-%d"),
        "annual_yield": res["indicators"]["quote"][0]["close"],
    }).iloc[:keep].dropna()
    df["annual_yield"] = (df["annual_yield"] / 100).round(6)
    return df


def fetch_kraken(source_id):
    """Daily OHLCV from Kraken. Volume is in units of the coin."""
    url = "https://api.kraken.com/0/public/OHLC"
    r = requests.get(url, params={"pair": source_id, "interval": 1440}, timeout=30)
    r.raise_for_status()
    js = r.json()
    if js["error"]:
        raise RuntimeError(", ".join(js["error"]))

    # The result is keyed by Kraken's own pair name (e.g. XXBTZUSD) plus "last".
    bars = next(v for k, v in js["result"].items() if k != "last")
    df = pd.DataFrame(bars, columns=["ts", "open", "high", "low", "close", "vwap", "volume", "count"])
    df["dt"] = pd.to_datetime(df["ts"], unit="s").dt.strftime("%Y-%m-%d")

    # Crypto pays no dividends, so the adjusted close is the close.
    df["adj_close"] = df["close"]

    # The final bar is today's candle, which is still forming.
    return df[COLUMNS].iloc[:-1].astype({c: float for c in COLUMNS[1:]})


def load_seed(symbol):
    return pd.read_csv(SEED_DIR / f"{symbol}.csv")


def upsert_asset(cur, symbol, name, asset_class):
    cur.execute(
        """
        INSERT INTO asset (symbol, name, asset_class)
        VALUES (%s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET name = EXCLUDED.name
        RETURNING asset_id
        """,

        (symbol, name, asset_class),
    )

    return cur.fetchone()[0]


def upsert_prices(cur, asset_id, df):
    rows = [
        (asset_id, r.dt, r.open, r.high, r.low, r.close, r.adj_close, r.volume)

        for r in df.itertuples(index=False)
    ]

    execute_values(
        cur,

        """
        INSERT INTO price_daily (asset_id, dt, open, high, low, close, adj_close, volume)
        VALUES %s
        ON CONFLICT (asset_id, dt) DO UPDATE
            SET close = EXCLUDED.close, adj_close = EXCLUDED.adj_close,
                volume = EXCLUDED.volume
        """,

        rows,
    )

    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--seed", action="store_true", help="load bundled offline CSVs")
    mode.add_argument("--write-seed", action="store_true",
                      help="pull real data and save it to data/seed/ as the offline snapshot")
    mode.add_argument("--strict", action="store_true",
                      help="pull real data and abort, changing nothing, if any source fails "
                           "(for scheduled refreshes, where a silent fallback would be wrong)")
    args = ap.parse_args()

    conn = psycopg2.connect(database_url())
    conn.autocommit = False
    cur = conn.cursor()

    print("Building schema...")

    run_sql_file(cur, SCHEMA_DIR / "01_schema.sql")

    fell_back = []

    for symbol, (name, cls, source_id) in UNIVERSE.items():
        try:
            if args.seed:
                df = load_seed(symbol)

            elif cls == "crypto":
                df = with_retries(fetch_kraken, source_id)
                time.sleep(1)  # stay under Kraken's public rate limit

            else:
                df = with_retries(fetch_yahoo, source_id)

        except Exception as e:
            # Never write a stale or partial snapshot over the seed files,
            # and never let a strict run replace live data with the seed.
            # Nothing has been committed, so the database is left as it was.
            if args.write_seed:
                raise SystemExit(f"{symbol}: fetch failed ({e}); seed files left unchanged")
            if args.strict:
                raise SystemExit(f"{symbol}: fetch failed ({e}); database left unchanged")

            print(f"  {symbol}: fetch failed ({e}); falling back to seed")

            df = load_seed(symbol)
            fell_back.append(symbol)

        df = df.dropna(subset=["close", "adj_close"]).round(
            {"open": 6, "high": 6, "low": 6, "close": 6, "adj_close": 6, "volume": 2}
        )
        asset_id = upsert_asset(cur, symbol, name, cls)
        count = upsert_prices(cur, asset_id, df)

        if args.write_seed:
            df[COLUMNS].to_csv(SEED_DIR / f"{symbol}.csv", index=False)

        print(f"  {symbol:5s} {count} rows  {df.dt.iloc[0]} to {df.dt.iloc[-1]}")

    # Risk-free rate, with the same fallback rules as prices.
    try:
        rf = pd.read_csv(RISK_FREE_SEED) if args.seed else with_retries(fetch_risk_free)
    except Exception as e:
        if args.write_seed:
            raise SystemExit(f"risk-free rate: fetch failed ({e}); seed files left unchanged")
        if args.strict:
            raise SystemExit(f"risk-free rate: fetch failed ({e}); database left unchanged")
        print(f"  risk-free rate: fetch failed ({e}); falling back to seed")
        rf = pd.read_csv(RISK_FREE_SEED)
        fell_back.append("risk-free rate")
    execute_values(
        cur,
        "INSERT INTO risk_free_daily (dt, annual_yield) VALUES %s",
        list(rf[["dt", "annual_yield"]].itertuples(index=False, name=None)),
    )
    if args.write_seed:
        rf.to_csv(RISK_FREE_SEED, index=False)
    print(f"  T-bill {len(rf)} rows  {rf.dt.iloc[0]} to {rf.dt.iloc[-1]}")

    print("Building analytical views...")

    run_sql_file(cur, SCHEMA_DIR / "02_views.sql")

    conn.commit()
    cur.close()
    conn.close()

    if fell_back:
        print(f"WARNING: {', '.join(fell_back)} loaded from the bundled seed, not live data.")

    print("Done.")


if __name__ == "__main__":
    main()
