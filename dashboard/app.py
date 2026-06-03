"""
Kalshi Edge Bot — Streamlit Dashboard.

Run with:
    streamlit run dashboard/app.py -- --paper-db data/paper.db --calibration-db data/calibration.db

The dashboard is READ-ONLY. It opens SQLite in `mode=ro` so it can safely
run alongside a live arb_runner without lock contention.

All data-layer helpers live in dashboard.data and are unit-tested.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from dashboard import data as dd


# ---------------------------------------------------------------- args / paths
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--paper-db", default=os.environ.get("PAPER_DB", "data/paper.db"))
    parser.add_argument("--calibration-db", default=os.environ.get("CALIBRATION_DB", "data/calibration.db"))
    parser.add_argument("--starting-bankroll", type=float,
                        default=float(os.environ.get("STARTING_BANKROLL", "10000")))
    parser.add_argument("--refresh-seconds", type=int, default=10)
    args, _ = parser.parse_known_args()
    return args


ARGS = _parse_args()


# -------------------------------------------------------------- cached loaders
# Wrapping in @st.cache_data here (rather than in dd) keeps the data module
# importable in plain-Python tests without Streamlit installed.
@st.cache_data(ttl=5)
def load_trades(p): return dd.load_trades(p)

@st.cache_data(ttl=5)
def load_positions(p): return dd.load_positions(p)

@st.cache_data(ttl=5)
def load_predictions(p): return dd.load_predictions(p)

@st.cache_data(ttl=5)
def load_strategy_state(p): return dd.load_strategy_state(p)


# ---------------------------------------------------------------- layout
st.set_page_config(page_title="Kalshi Edge Bot", page_icon="📈", layout="wide")

with st.sidebar:
    st.markdown("### ⚙️ Settings")
    st.text_input("Paper DB", value=ARGS.paper_db, disabled=True)
    st.text_input("Calibration DB", value=ARGS.calibration_db, disabled=True)
    st.number_input("Starting bankroll", value=ARGS.starting_bankroll, disabled=True)
    auto_refresh = st.checkbox("Auto-refresh", value=True)
    refresh_seconds = st.slider("Refresh (s)", 3, 60, ARGS.refresh_seconds)
    if st.button("🔄 Refresh now"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Last load: {datetime.now().strftime('%H:%M:%S')}")

st.title("📈 Kalshi Edge Bot")
st.caption("Live paper-trading dashboard. Read-only view of paper.db + calibration.db.")

trades = load_trades(ARGS.paper_db)
positions = load_positions(ARGS.paper_db)
preds = load_predictions(ARGS.calibration_db)
strategy_state = load_strategy_state(ARGS.calibration_db)

equity_curve = dd.compute_equity_curve(trades, ARGS.starting_bankroll)
current_cash = float(equity_curve["cash"].iloc[-1]) if not equity_curve.empty else ARGS.starting_bankroll
open_positions = positions[positions["closed_at"].isna()] if not positions.empty else positions
n_open = len(open_positions)
open_cost = float(open_positions["contracts"].mul(open_positions["avg_price"]).sum()) if n_open else 0.0
equity = current_cash + open_cost
pnl = equity - ARGS.starting_bankroll
pnl_pct = (pnl / ARGS.starting_bankroll * 100) if ARGS.starting_bankroll else 0.0
fees_today = 0.0
trades_today = 0
if not trades.empty:
    today_utc = datetime.now(timezone.utc).date()
    today_mask = trades["placed_at"].dt.date == today_utc
    fees_today = float(trades.loc[today_mask, "fees"].sum())
    trades_today = int(today_mask.sum())

m1, m2, m3, m4, m5, m6 = st.columns(6)
m1.metric("Equity", f"${equity:,.2f}", f"{pnl:+,.2f} ({pnl_pct:+.2f}%)")
m2.metric("Cash", f"${current_cash:,.2f}")
m3.metric("Open positions", n_open)
m4.metric("Deployed", f"${open_cost:,.2f}")
m5.metric("Trades today", trades_today)
m6.metric("Fees today", f"${fees_today:,.2f}")

st.divider()

tab_overview, tab_positions, tab_fills, tab_calibration, tab_strategies = st.tabs(
    ["📊 Overview", "📂 Positions", "🧾 Fills", "🎯 Calibration", "🛑 Strategy state"]
)

with tab_overview:
    c1, c2 = st.columns([2, 1])
    with c1:
        st.subheader("Equity curve")
        if equity_curve.empty:
            st.info("No trades yet. Paper portfolio at starting bankroll.")
        else:
            st.line_chart(equity_curve.set_index("placed_at")["cash"], height=320)
    with c2:
        st.subheader("Trades per strategy")
        if trades.empty:
            st.info("—")
        else:
            by_strat = trades.groupby("strategy").size().reset_index(name="trades")
            st.dataframe(by_strat, hide_index=True, use_container_width=True)

    st.subheader("Cumulative fees")
    if trades.empty:
        st.info("—")
    else:
        fees_curve = (trades.sort_values("placed_at")
                            .assign(cum_fees=lambda d: d["fees"].cumsum())
                            .set_index("placed_at")[["cum_fees"]])
        st.area_chart(fees_curve, height=200)

with tab_positions:
    st.subheader(f"Open ({n_open})")
    if open_positions.empty:
        st.info("No open positions.")
    else:
        show = open_positions.assign(
            cost=lambda d: (d["contracts"] * d["avg_price"]).round(2)
        )[["ticker", "side", "contracts", "avg_price", "cost", "opened_at"]]
        st.dataframe(show, hide_index=True, use_container_width=True)

    closed = positions[positions["closed_at"].notna()] if not positions.empty else positions
    st.subheader(f"Closed ({len(closed)})")
    if closed.empty:
        st.info("No closed positions.")
    else:
        st.dataframe(
            closed[["ticker", "side", "contracts", "avg_price",
                    "opened_at", "closed_at", "realized_pnl"]].head(50),
            hide_index=True, use_container_width=True,
        )

with tab_fills:
    st.subheader("Recent fills")
    if trades.empty:
        st.info("No fills yet.")
    else:
        st.dataframe(
            trades.head(100)[["placed_at", "strategy", "ticker", "side", "action",
                              "contracts", "price", "is_maker", "fees", "cost"]],
            hide_index=True, use_container_width=True,
        )

with tab_calibration:
    st.subheader("Brier score by strategy")
    brier_df = dd.brier_by_strategy(preds)
    if brier_df.empty:
        st.info("No resolved predictions yet. Brier needs outcome ∈ {0,1}.")
    else:
        def _flag(b: float) -> str:
            if b > 0.25:
                return "🔴 worse than random"
            if b > 0.18:
                return "🟡 marginal"
            return "🟢 ok"
        brier_df["status"] = brier_df["brier"].apply(_flag)
        st.dataframe(brier_df, hide_index=True, use_container_width=True)

    n_total = len(preds) if not preds.empty else 0
    n_resolved = int(preds["outcome"].notna().sum()) if not preds.empty else 0
    st.caption(f"{n_resolved} of {n_total} predictions resolved.")

with tab_strategies:
    st.subheader("Strategy enable/disable")
    if strategy_state.empty:
        st.info("No kill-switch state recorded. All strategies default-enabled.")
    else:
        ss = strategy_state.copy()
        ss["enabled"] = ss["enabled"].map({1: "✅", 0: "🛑"})
        st.dataframe(ss, hide_index=True, use_container_width=True)

if auto_refresh:
    time.sleep(refresh_seconds)
    st.rerun()
