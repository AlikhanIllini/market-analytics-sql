"""
Market Analytics dashboard.

A thin Streamlit layer over the SQL. Every chart is driven by a query
against the views in schema/02_views.sql, so the database does the
analytics and the app just renders them.

Run:
    streamlit run dashboard/app.py
"""
import os
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from sqlalchemy import create_engine, text

LOCAL_URL = "postgresql://quant:quant@localhost:5432/market"


def database_url():
    """Connection string for the app.

    Order: DATABASE_URL in Streamlit secrets (the Secrets box on Streamlit
    Community Cloud, or .streamlit/secrets.toml locally), then the
    DATABASE_URL environment variable, then the local Docker database.
    """
    try:
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except FileNotFoundError:  # no secrets file at all
        pass
    return os.environ.get("DATABASE_URL", LOCAL_URL)


@st.cache_resource
def get_engine():
    # Hosted Postgres (Neon) suspends idle compute, which drops open
    # connections. pool_pre_ping checks each pooled connection before use
    # and reconnects if it went stale.
    return create_engine(database_url(), pool_pre_ping=True)


@st.cache_data(ttl=300)
def q(sql, **params):
    with get_engine().connect() as c:
        return pd.read_sql(text(sql), c, params=params)


st.set_page_config(page_title="Market Analytics", layout="wide")
st.title("Market Analytics")
asof = q("SELECT MAX(dt) AS d FROM price_daily").d[0]
st.caption(
    "Equities, ETFs, and crypto. All metrics computed in SQL. "
    f"Data through {asof:%B} {asof.day}, {asof.year}."
)

assets = q("SELECT symbol, asset_class FROM asset ORDER BY asset_class, symbol")
# The selector lives in the page body, not the sidebar: the sidebar starts
# collapsed on narrow windows, which hides the only control on the page.
picker, _ = st.columns([1, 3])
symbol = picker.selectbox("Asset", assets["symbol"])

# ---- Price with moving averages -------------------------------------
ma = q(
    """
    SELECT dt, close, sma_50, sma_200, cross_signal
    FROM v_moving_avg WHERE symbol = :s ORDER BY dt
    """,
    s=symbol,
)
fig = go.Figure()
fig.add_trace(go.Scatter(x=ma.dt, y=ma.close, name="Close", line=dict(width=1.5)))
fig.add_trace(go.Scatter(x=ma.dt, y=ma.sma_50, name="SMA 50", line=dict(width=1)))
fig.add_trace(go.Scatter(x=ma.dt, y=ma.sma_200, name="SMA 200", line=dict(width=1)))
crosses = ma.dropna(subset=["cross_signal"])
fig.add_trace(go.Scatter(
    x=crosses.dt, y=crosses.close, mode="markers", name="Crossover",
    marker=dict(size=9, symbol="x"),
))
fig.update_layout(title=f"{symbol} price and moving averages", height=420,
                  margin=dict(t=40, b=10))
st.plotly_chart(fig, use_container_width=True)

# ---- Backtest equity curves -----------------------------------------
# Growth of $1 under the 50/200 MA crossover strategy vs buy-and-hold,
# straight from v_backtest. The strategy curve is shown net of a 10 bps
# per-trade cost; a faint dotted line marks the cost-free (gross) path so
# the drag from trading frictions is visible.
bt = q(
    """
    SELECT dt, equity_buy_hold, equity_strategy_gross, equity_strategy_net
    FROM v_backtest WHERE symbol = :s ORDER BY dt
    """,
    s=symbol,
)
bfig = go.Figure()
bfig.add_trace(go.Scatter(x=bt.dt, y=bt.equity_buy_hold, name="Buy & hold",
                          line=dict(width=1.6)))
bfig.add_trace(go.Scatter(x=bt.dt, y=bt.equity_strategy_net,
                          name="MA crossover (net of 10 bps)", line=dict(width=1.6)))
bfig.add_trace(go.Scatter(x=bt.dt, y=bt.equity_strategy_gross,
                          name="MA crossover (gross)", opacity=0.45,
                          line=dict(width=1, dash="dot")))
bfig.add_hline(y=1, line_width=1, line_dash="dash", line_color="#888")
bfig.update_layout(
    title=f"{symbol} backtest: growth of $1 (50/200 MA crossover vs buy & hold)",
    height=440, margin=dict(t=40, b=10), yaxis_title="Equity (multiple of $1)",
    # Legend sits under the plot so it cannot collide with the title.
    legend=dict(orientation="h", yanchor="top", y=-0.12, x=0),
)
st.plotly_chart(bfig, use_container_width=True)
if not bt.empty:
    last = bt.iloc[-1]
    bh = float(last.equity_buy_hold) - 1
    net = float(last.equity_strategy_net) - 1
    gross = float(last.equity_strategy_gross) - 1
    st.caption(
        f"Final return over the tested window: strategy {net:+.1%} after costs "
        f"({gross:+.1%} before), buy & hold {bh:+.1%}. "
        f"Costs shaved {gross - net:.1%} off the strategy. Tested window: "
        f"{bt['dt'].iloc[0]:%b %d, %Y} to {last['dt']:%b %d, %Y}. Position is set from "
        "the prior day's signal, so there is no lookahead."
    )

col1, col2 = st.columns(2)

# ---- Rolling volatility ---------------------------------------------
# The first 20 days have no value yet (the window is still filling). Leave
# them out: an area chart would draw the missing values as zero.
vol = q(
    """
    SELECT dt, vol_20d_ann FROM v_rolling_vol
    WHERE symbol = :s AND vol_20d_ann IS NOT NULL ORDER BY dt
    """,
    s=symbol,
)
vfig = px.area(vol, x="dt", y="vol_20d_ann",
               labels={"dt": "Date", "vol_20d_ann": "Annualized volatility"},
               title=f"{symbol} rolling 20-day annualized volatility")
vfig.update_layout(height=320, margin=dict(t=40, b=10), yaxis_tickformat=".0%",
                   xaxis_title=None)
col1.plotly_chart(vfig, use_container_width=True)

# ---- Drawdown -------------------------------------------------------
dd = q("SELECT dt, drawdown FROM v_drawdown WHERE symbol = :s ORDER BY dt", s=symbol)
dfig = px.area(dd, x="dt", y="drawdown",
               labels={"dt": "Date", "drawdown": "Drawdown"},
               title=f"{symbol} drawdown from peak")
dfig.update_layout(height=320, margin=dict(t=40, b=10), yaxis_tickformat=".0%",
                   xaxis_title=None)
dfig.update_traces(line_color="#c0392b")
col2.plotly_chart(dfig, use_container_width=True)

# ---- Correlation heatmap --------------------------------------------
st.subheader("Cross-asset return correlation")
corr = q(
    """
    WITH r AS (
        SELECT asset_id, symbol, dt, simple_return
        FROM v_daily_return WHERE simple_return IS NOT NULL
    )
    SELECT a.symbol AS asset_a, b.symbol AS asset_b,
           CORR(a.simple_return, b.simple_return) AS correlation
    FROM r a JOIN r b ON a.dt = b.dt
    GROUP BY a.symbol, b.symbol
    """
)
matrix = corr.pivot(index="asset_a", columns="asset_b", values="correlation")
# Fit the color scale to the data. When every pair is positively correlated
# (the usual case for this universe) a 0 to 1 sequential scale uses the full
# color range; a diverging -1 to 1 scale is only used if a negative appears.
if float(matrix.min().min()) >= 0:
    scale, zmin = "Reds", 0
else:
    scale, zmin = "RdBu_r", -1
hfig = px.imshow(matrix, text_auto=".2f", color_continuous_scale=scale,
                 zmin=zmin, zmax=1, aspect="auto",
                 labels=dict(x="Asset", y="Asset", color="Correlation"))
hfig.update_layout(height=420, margin=dict(t=10, b=10),
                   xaxis_title=None, yaxis_title=None)
st.plotly_chart(hfig, use_container_width=True)

# ---- Performance table ----------------------------------------------
st.subheader("Performance summary")
# v_performance_summary was added after the first deployment. A database
# built from the older schema keeps the dashboard up, with the simpler
# return-over-volatility ratio, until its next data load rebuilds the views.
has_summary = bool(
    q("SELECT to_regclass('v_performance_summary') IS NOT NULL AS ok").ok[0]
)
if has_summary:
    perf = q(
        """
        SELECT symbol AS "Symbol",
               CASE asset_class WHEN 'etf' THEN 'ETF'
                                ELSE INITCAP(asset_class) END AS "Asset class",
               ROUND((ann_return * 100)::NUMERIC, 1)::FLOAT8 AS "Ann return %",
               ROUND((ann_vol * 100)::NUMERIC, 1)::FLOAT8    AS "Ann vol %",
               ROUND(sharpe::NUMERIC, 2)::FLOAT8             AS "Sharpe"
        FROM v_performance_summary ORDER BY sharpe DESC
        """
    )
    perf_note = (
        "Total returns, dividends included. Annualized over 252 trading days "
        "for stocks and ETFs and 365 days for crypto. Sharpe is the excess "
        "return over the 13-week Treasury bill divided by its volatility."
    )
else:
    perf = q(
        """
        WITH stats AS (
            SELECT symbol, asset_class,
                   CASE WHEN asset_class = 'crypto' THEN 365 ELSE 252 END AS ppy,
                   AVG(simple_return) AS mean_r, STDDEV_SAMP(simple_return) AS sd_r
            FROM v_daily_return WHERE simple_return IS NOT NULL
            GROUP BY symbol, asset_class
        )
        SELECT symbol AS "Symbol",
               CASE asset_class WHEN 'etf' THEN 'ETF'
                                ELSE INITCAP(asset_class) END AS "Asset class",
               ROUND((mean_r * ppy * 100)::NUMERIC, 1)::FLOAT8       AS "Ann return %",
               ROUND((sd_r * SQRT(ppy) * 100)::NUMERIC, 1)::FLOAT8   AS "Ann vol %",
               ROUND((mean_r / NULLIF(sd_r, 0) * SQRT(ppy))::NUMERIC, 2)::FLOAT8
                                                                      AS "Return / vol"
        FROM stats ORDER BY "Return / vol" DESC
        """
    )
    perf_note = (
        "Annualized over 252 trading days for stocks and ETFs and 365 days "
        "for crypto. Return / vol is not adjusted for the risk-free rate."
    )
st.dataframe(
    perf, use_container_width=True, hide_index=True,
    column_config={
        "Ann return %": st.column_config.NumberColumn(format="%.1f"),
        "Ann vol %": st.column_config.NumberColumn(format="%.1f"),
        perf.columns[-1]: st.column_config.NumberColumn(format="%.2f"),
    },
)
st.caption(perf_note)
