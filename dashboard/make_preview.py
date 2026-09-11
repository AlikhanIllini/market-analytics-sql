"""
Render dashboard/preview.png, the static image shown in the README.

Every number comes from the same SQL views the Streamlit app reads, so the
preview always matches whatever data is loaded. Run after loading data:
    python dashboard/make_preview.py
"""
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
from sqlalchemy import create_engine, text

DB_URL = os.environ.get("DATABASE_URL", "postgresql://quant:quant@localhost:5432/market")
OUT = Path(__file__).parent / "preview.png"
ORDER = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "BTC", "ETH", "TON"]


def q(engine, sql, **params):
    with engine.connect() as c:
        return pd.read_sql(text(sql), c, params=params)


def style(ax, title):
    ax.set_title(title, loc="left", fontweight="bold", fontsize=13)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(alpha=0.3)
    locator = mdates.AutoDateLocator()
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))


def main():
    engine = create_engine(DB_URL)

    ma = q(engine, """
        SELECT dt, close, sma_50, sma_200, cross_signal
        FROM v_moving_avg WHERE symbol = 'AAPL' ORDER BY dt
    """)
    dd = q(engine, "SELECT dt, drawdown FROM v_drawdown WHERE symbol = 'BTC' ORDER BY dt")
    corr = q(engine, """
        WITH r AS (
            SELECT symbol, dt, simple_return
            FROM v_daily_return WHERE simple_return IS NOT NULL
        )
        SELECT a.symbol AS asset_a, b.symbol AS asset_b,
               CORR(a.simple_return, b.simple_return) AS correlation
        FROM r a JOIN r b ON a.dt = b.dt
        GROUP BY a.symbol, b.symbol
    """)
    span = q(engine, "SELECT MIN(dt) AS lo, MAX(dt) AS hi FROM price_daily").iloc[0]

    # NUMERIC columns arrive as Decimal; matplotlib wants floats.
    for df in (ma, dd):
        df["dt"] = pd.to_datetime(df["dt"])
    ma[["close", "sma_50", "sma_200"]] = ma[["close", "sma_50", "sma_200"]].astype(float)
    dd["drawdown"] = dd["drawdown"].astype(float) * 100
    matrix = (corr.pivot(index="asset_a", columns="asset_b", values="correlation")
                  .astype(float).loc[ORDER, ORDER])

    fig = plt.figure(figsize=(14, 9.5))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.1, 1], hspace=0.38, wspace=0.22)

    # ---- AAPL price with moving averages and crossovers ----------------
    ax = fig.add_subplot(gs[0, :])
    ax.plot(ma.dt, ma.close, color="#1f2d3d", lw=1.4, label="Close")
    ax.plot(ma.dt, ma.sma_50, color="#2e86de", lw=1.1, label="SMA 50")
    ax.plot(ma.dt, ma.sma_200, color="#e67e22", lw=1.1, label="SMA 200")
    crosses = ma.dropna(subset=["cross_signal"])
    ax.scatter(crosses.dt, crosses.close, marker="x", s=80, color="#c0392b",
               linewidths=2, zorder=3, label="Crossover")
    ax.legend(loc="upper left", ncol=4, frameon=False)
    style(ax, "AAPL price and moving averages")

    # ---- BTC drawdown from running peak --------------------------------
    ax = fig.add_subplot(gs[1, 0])
    ax.fill_between(dd.dt, dd.drawdown, 0, color="#c0392b", alpha=0.45, lw=0)
    ax.plot(dd.dt, dd.drawdown, color="#c0392b", lw=0.8)
    style(ax, "BTC drawdown from peak (%)")

    # ---- Cross-asset return correlation --------------------------------
    ax = fig.add_subplot(gs[1, 1])
    ax.imshow(matrix.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(ORDER)), ORDER, rotation=45, ha="right")
    ax.set_yticks(range(len(ORDER)), ORDER)
    for i in range(len(ORDER)):
        for j in range(len(ORDER)):
            v = matrix.iat[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=8,
                    color="white" if abs(v) > 0.5 else "#333")
    ax.set_title("Return correlation", loc="left", fontweight="bold", fontsize=13)

    fig.suptitle("Market Analytics: SQL-driven dashboard preview",
                 x=0.01, ha="left", fontweight="bold", fontsize=16)
    fig.text(0.01, 0.935,
             f"Real daily prices, {span.lo} to {span.hi}. "
             "Equities and ETFs from Yahoo Finance, crypto from Kraken.",
             ha="left", fontsize=10, color="#555")
    fig.savefig(OUT, dpi=100, bbox_inches="tight", facecolor="white")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
