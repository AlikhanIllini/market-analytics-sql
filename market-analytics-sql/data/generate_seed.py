"""
Generate realistic synthetic OHLCV history for the seed dataset.

This is ONLY used so the repo runs offline with no API keys. The real
loader (load_data.py) pulls actual market data. Series here use a
geometric Brownian motion with a shared market factor so the assets
show plausible cross-correlations.
"""
import numpy as np
import pandas as pd
from pathlib import Path

rng = np.random.default_rng(42)
SEED_DIR = Path(__file__).parent / "seed"
SEED_DIR.mkdir(exist_ok=True)

# symbol, name, asset_class, start_price, ann_drift, ann_vol, market_beta
UNIVERSE = [
    ("AAPL", "Apple Inc.",            "equity", 150.0, 0.18, 0.28, 1.1),
    ("MSFT", "Microsoft Corp.",       "equity", 250.0, 0.20, 0.26, 1.0),
    ("NVDA", "NVIDIA Corp.",          "equity",  45.0, 0.55, 0.50, 1.6),
    ("SPY",  "SPDR S&P 500 ETF",      "etf",    400.0, 0.12, 0.16, 1.0),
    ("QQQ",  "Invesco QQQ ETF",       "etf",    320.0, 0.16, 0.22, 1.2),
    ("BTC",  "Bitcoin",               "crypto", 28000.0, 0.22, 0.45, 0.8),
    ("ETH",  "Ethereum",              "crypto",  1600.0, 0.18, 0.50, 0.9),
    ("TON",  "Toncoin",               "crypto",     2.0, 0.20, 0.60, 0.7),
]

# Two years of calendar days; equities later filtered to weekdays.
dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=600)
crypto_dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=600)
n = len(dates)

# Shared market factor drives common variance across equities/ETFs.
MARKET_VOL = 0.011   # daily std of the systematic factor
EQUITY_IDIO = 0.004  # small asset-specific daily noise
market = rng.normal(0, MARKET_VOL, n)

for symbol, name, cls, p0, drift, vol, beta in UNIVERSE:
    idx = crypto_dates if cls == "crypto" else dates
    m = len(idx)
    dt = 1 / 252
    if cls == "crypto":
        # Crypto runs on its own GBM, largely decoupled from equities.
        daily = (drift - 0.5 * vol**2) * dt + rng.normal(0, vol * np.sqrt(dt), m)
    else:
        # Equity/ETF return = drift + beta * market factor + small idio.
        daily = ((drift - 0.5 * vol**2) * dt
                 + beta * market[:m]
                 + rng.normal(0, EQUITY_IDIO, m))
    close = p0 * np.exp(np.cumsum(daily))

    # Build OHLC around the close with small intraday noise.
    noise = lambda scale: np.abs(rng.normal(0, scale, m))
    openp = close * (1 + rng.normal(0, 0.004, m))
    high = np.maximum(openp, close) * (1 + noise(0.006))
    low = np.minimum(openp, close) * (1 - noise(0.006))
    base_vol = 5e7 if cls != "crypto" else 8e8
    volume = (base_vol * (1 + np.abs(rng.normal(0, 0.4, m)))).round(2)

    df = pd.DataFrame({
        "dt": idx.strftime("%Y-%m-%d"),
        "open": openp.round(6),
        "high": high.round(6),
        "low": low.round(6),
        "close": close.round(6),
        "volume": volume,
    })
    df.to_csv(SEED_DIR / f"{symbol}.csv", index=False)
    print(f"{symbol:5s} {cls:6s} {m} rows  last close {close[-1]:.2f}")

# Asset metadata table for the loader.
meta = pd.DataFrame(
    [(s, nm, c) for s, nm, c, *_ in UNIVERSE],
    columns=["symbol", "name", "asset_class"],
)
meta.to_csv(SEED_DIR / "_assets.csv", index=False)
print(f"\nWrote {len(UNIVERSE)} files + _assets.csv to {SEED_DIR}")
