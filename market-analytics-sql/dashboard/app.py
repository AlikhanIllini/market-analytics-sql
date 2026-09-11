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

DB_URL = os.environ.get("DATABASE_URL", "postgresql://quant:quant@localhost:5432/market")


@st.cache_resource
def get_engine():
    return create_engine(DB_URL)


@st.cache_data(ttl=300)
def q(sql, **params):
    with get_engine().connect() as c:
        return pd.read_sql(text(sql), c, params=params)


st.set_page_config(page_title="Market Analytics", layout="wide")
st.title("Market Analytics")
st.caption("Equities, ETFs, and crypto. All metrics computed in SQL.")

assets = q("SELECT symbol, asset_class FROM asset ORDER BY asset_class, symbol")
symbol = st.sidebar.selectbox("Asset", assets["symbol"])

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
    height=420, margin=dict(t=40, b=10), yaxis_title="Equity (multiple of $1)",
    legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
)
st.plotly_chart(bfig, use_container_width=True)
if not bt.empty:
    last = bt.iloc[-1]
    bh = float(last.equity_buy_hold) - 1
    net = float(last.equity_strategy_net) - 1
    gross = float(last.equity_strategy_gross) - 1
    st.caption(
        f"Final return over the tested window — strategy {net:+.1%} after costs "
        f"({gross:+.1%} before), buy & hold {bh:+.1%}. "
        f"Costs shaved {gross - net:.1%} off the strategy. Position is set from "
        "the prior day's signal, so there is no lookahead."
    )

col1, col2 = st.columns(2)

# ---- Rolling volatility ---------------------------------------------
vol = q(
    "SELECT dt, vol_20d_ann FROM v_rolling_vol WHERE symbol = :s ORDER BY dt",
    s=symbol,
)
vfig = px.area(vol, x="dt", y="vol_20d_ann",
               title=f"{symbol} rolling 20-day annualized volatility")
vfig.update_layout(height=320, margin=dict(t=40, b=10), yaxis_tickformat=".0%")
col1.plotly_chart(vfig, use_container_width=True)

# ---- Drawdown -------------------------------------------------------
dd = q("SELECT dt, drawdown FROM v_drawdown WHERE symbol = :s ORDER BY dt", s=symbol)
dfig = px.area(dd, x="dt", y="drawdown", title=f"{symbol} drawdown from peak")
dfig.update_layout(height=320, margin=dict(t=40, b=10), yaxis_tickformat=".0%")
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
hfig = px.imshow(matrix, text_auto=".2f", color_continuous_scale="RdBu_r",
                 zmin=-1, zmax=1, aspect="auto")
hfig.update_layout(height=420, margin=dict(t=10, b=10))
st.plotly_chart(hfig, use_container_width=True)

# ---- Performance table ----------------------------------------------
st.subheader("Performance summary")
perf = q(
    """
    WITH stats AS (
        SELECT symbol, asset_class,
               AVG(simple_return) * 252 AS ann_return,
               STDDEV_SAMP(simple_return) * SQRT(252) AS ann_vol
        FROM v_daily_return WHERE simple_return IS NOT NULL
        GROUP BY symbol, asset_class
    )
    SELECT symbol, asset_class,
           ROUND((ann_return * 100)::NUMERIC, 1) AS "Ann return %",
           ROUND((ann_vol * 100)::NUMERIC, 1)    AS "Ann vol %",
           ROUND((ann_return / NULLIF(ann_vol, 0))::NUMERIC, 2) AS "Sharpe"
    FROM stats ORDER BY "Sharpe" DESC
    """
)
st.dataframe(perf, use_container_width=True, hide_index=True)
