"""
load_data.py
Build the database and load price history.

Usage:
    python load_data.py              # pull real data from public APIs
    python load_data.py --seed       # load bundled offline CSVs (no network)
    python load_data.py --write-seed # pull real data and refresh the bundled CSVs

Real sources, both free and key-less:
    equities / ETFs -> Yahoo Finance chart endpoint (split-adjusted daily bars)
    crypto -> Kraken public OHLC (last 720 daily bars, priced in USD)

Connection comes from the DATABASE_URL env var, e.g.
    postgresql://quant:quant@localhost:5432/market
"""

import argparse
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

COLUMNS = ["dt", "open", "high", "low", "close", "volume"]


def run_sql_file(cur, path):
    cur.execute(Path(path).read_text())


def fetch_yahoo(source_id, period="2y"):
    """Daily OHLCV from Yahoo's public chart endpoint. Prices are
    split-adjusted, so returns stay clean across stock splits."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{source_id}"
    r = requests.get(
        url,
        params={"range": period, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    quote = res["indicators"]["quote"][0]

    return pd.DataFrame({
        "dt": pd.to_datetime(res["timestamp"], unit="s").strftime("%Y-%m-%d"),
        "open": quote["open"],
        "high": quote["high"],
        "low": quote["low"],
        "close": quote["close"],
        "volume": quote["volume"],
    })


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
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--seed", action="store_true", help="load bundled offline CSVs")
    mode.add_argument("--write-seed", action="store_true",
                      help="pull real data and save it to data/seed/ as the offline snapshot")
    args = ap.parse_args()

    conn = psycopg2.connect(DB_URL)
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
                df = fetch_kraken(source_id)
                time.sleep(1)  # stay under Kraken's public rate limit

            else:
                df = fetch_yahoo(source_id)

        except Exception as e:
            # Never write a stale or partial snapshot over the seed files.
            if args.write_seed:
                raise SystemExit(f"{symbol}: fetch failed ({e}); seed files left unchanged")

            print(f"  {symbol}: fetch failed ({e}); falling back to seed")

            df = load_seed(symbol)
            fell_back.append(symbol)

        df = df.dropna(subset=["close"]).round(
            {"open": 6, "high": 6, "low": 6, "close": 6, "volume": 2}
        )
        asset_id = upsert_asset(cur, symbol, name, cls)
        count = upsert_prices(cur, asset_id, df)

        if args.write_seed:
            df[COLUMNS].to_csv(SEED_DIR / f"{symbol}.csv", index=False)

        print(f"  {symbol:5s} {count} rows  {df.dt.iloc[0]} to {df.dt.iloc[-1]}")

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
