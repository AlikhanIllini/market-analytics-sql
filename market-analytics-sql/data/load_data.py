"""
load_data.py
Build the database and load price history.

Usage:
    python load_data.py # pull real data from public APIs
    python load_data.py --seed # load bundled offline CSVs (no network)

Real sources, both free and key-less:
    equities / ETFs -> Stooq daily CSV
    crypto -> CoinGecko market_chart

Connection comes from the DATABASE_URL env var, e.g.
    postgresql://quant:quant@localhost:5432/market
"""

import argparse
import io
import os
import time
from pathlib import Path

import pandas as pd
import requests
import psycopg2
from psycopg2.extras import execute_values

HERE = Path(__file__).parent
SCHEMA_DIR = HERE.parent / "schema"
SEED_DIR = HERE / "seed"
DB_URL = os.environ.get("DATABASE_URL", "postgresql://quant:quant@localhost:5432/market")

# symbol -> (name, asset_class, source_id)
# source_id is the Stooq ticker or the CoinGecko coin id.
UNIVERSE = {
    "AAPL": ("Apple Inc.", "equity", "aapl.us"),
    "MSFT": ("Microsoft Corp.", "equity", "msft.us"),
    "NVDA": ("NVIDIA Corp.", "equity", "nvda.us"),
    "SPY":  ("SPDR S&P 500 ETF", "etf", "spy.us"),
    "QQQ":  ("Invesco QQQ ETF", "etf", "qqq.us"),
    "BTC":  ("Bitcoin", "crypto", "bitcoin"),
    "ETH":  ("Ethereum", "crypto", "ethereum"),
    "TON":  ("Toncoin", "crypto", "the-open-network"),
}


def run_sql_file(cur, path):
    cur.execute(Path(path).read_text())


def fetch_stooq(source_id):
    """Daily OHLCV from Stooq as a clean DataFrame."""
    url = f"https://stooq.com/q/d/l/?s={source_id}&i=d"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.lower() for c in df.columns]

    return df[["date", "open", "high", "low", "close", "volume"]].rename(
        columns={"date": "dt"}
    )


def fetch_coingecko(source_id, days=600):
    """Daily close + volume from CoinGecko. No OHLC on the free tier,
    so open/high/low are set to the close (the analytics use close)."""

    url = f"https://api.coingecko.com/api/v3/coins/{source_id}/market_chart"
    r = requests.get(url, params={"vs_currency": "usd", "days": days, "interval": "daily"}, timeout=30)
    r.raise_for_status()
    js = r.json()
    prices = pd.DataFrame(js["prices"], columns=["ts", "close"])
    vols = pd.DataFrame(js["total_volumes"], columns=["ts", "volume"])
    df = prices.merge(vols, on="ts")
    df["dt"] = pd.to_datetime(df["ts"], unit="ms").dt.strftime("%Y-%m-%d")
    df["open"] = df["high"] = df["low"] = df["close"]

    return df[["dt", "open", "high", "low", "close", "volume"]]


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
        (asset_id, r.dt, r.open, r.high, r.low, r.close, r.volume)

        for r in df.itertuples(index=False)
    ]

    execute_values(
        cur,

        """
        INSERT INTO price_daily (asset_id, dt, open, high, low, close, volume)
        VALUES %s
        ON CONFLICT (asset_id, dt) DO UPDATE
            SET close = EXCLUDED.close, volume = EXCLUDED.volume
        """,

        rows,
    )

    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="load bundled offline CSVs")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False
    cur = conn.cursor()

    print("Building schema...")

    run_sql_file(cur, SCHEMA_DIR / "01_schema.sql")

    for symbol, (name, cls, source_id) in UNIVERSE.items():
        try:
            if args.seed:
                df = load_seed(symbol)

            elif cls == "crypto":
                df = fetch_coingecko(source_id)
                time.sleep(2)  # be gentle with the free CoinGecko rate limit

            else:
                df = fetch_stooq(source_id)

        except Exception as e:
            print(f"  {symbol}: fetch failed ({e}); falling back to seed")

            df = load_seed(symbol)

        df = df.dropna(subset=["close"])
        asset_id = upsert_asset(cur, symbol, name, cls)
        count = upsert_prices(cur, asset_id, df)

        print(f"  {symbol:5s} {count} rows")

    print("Building analytical views...")

    run_sql_file(cur, SCHEMA_DIR / "02_views.sql")

    conn.commit()
    cur.close()
    conn.close()

    print("Done.")


if __name__ == "__main__":
    main()
