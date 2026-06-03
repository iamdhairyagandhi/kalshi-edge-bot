"""Data layer for the Streamlit dashboard.

Kept in a separate module from `app.py` so:
  1. The pure functions can be unit-tested without importing Streamlit.
  2. The Streamlit UI module stays focused on layout.

Everything here is READ-ONLY against the SQLite files.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd


def read_sql(db_path: str, query: str, params: tuple = ()) -> pd.DataFrame:
    """Read-only SQL. Returns empty DataFrame if the db doesn't exist."""
    if not Path(db_path).exists():
        return pd.DataFrame()
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def load_trades(db_path: str) -> pd.DataFrame:
    df = read_sql(db_path, "SELECT * FROM paper_trades ORDER BY id DESC")
    if not df.empty:
        df["placed_at"] = pd.to_datetime(df["placed_at"], errors="coerce", utc=True)
    return df


def load_positions(db_path: str) -> pd.DataFrame:
    df = read_sql(db_path, "SELECT * FROM paper_positions ORDER BY opened_at DESC")
    if not df.empty:
        df["opened_at"] = pd.to_datetime(df["opened_at"], errors="coerce", utc=True)
        df["closed_at"] = pd.to_datetime(df["closed_at"], errors="coerce", utc=True)
    return df


def load_predictions(db_path: str) -> pd.DataFrame:
    df = read_sql(db_path, "SELECT * FROM predictions ORDER BY id DESC")
    if not df.empty:
        df["decided_at"] = pd.to_datetime(df["decided_at"], errors="coerce", utc=True)
        df["resolved_at"] = pd.to_datetime(df["resolved_at"], errors="coerce", utc=True)
    return df


def load_strategy_state(db_path: str) -> pd.DataFrame:
    return read_sql(db_path, "SELECT * FROM strategy_state ORDER BY strategy")


def compute_equity_curve(trades: pd.DataFrame, starting_bankroll: float) -> pd.DataFrame:
    """Walking cash balance.

    buy  → cash -= cost + fees
    sell → cash += cost - fees
    """
    if trades.empty:
        return pd.DataFrame(columns=["placed_at", "cash"])
    t = trades.sort_values("placed_at").copy()
    sign = t["action"].map({"buy": -1.0, "sell": +1.0}).fillna(0.0)
    t["cash_delta"] = sign * t["cost"] - t["fees"]
    t["cash"] = starting_bankroll + t["cash_delta"].cumsum()
    return t[["placed_at", "cash"]]


def brier_by_strategy(preds: pd.DataFrame) -> pd.DataFrame:
    """Brier score per strategy over resolved predictions only."""
    if preds.empty:
        return pd.DataFrame()
    resolved = preds.dropna(subset=["outcome"]).copy()
    if resolved.empty:
        return pd.DataFrame()
    resolved["sq_err"] = (resolved["predicted_prob"] - resolved["outcome"]) ** 2
    grp = resolved.groupby("strategy").agg(
        n_resolved=("id", "count"),
        brier=("sq_err", "mean"),
        mean_pred=("predicted_prob", "mean"),
        hit_rate=("outcome", "mean"),
    ).reset_index()
    grp["brier"] = grp["brier"].round(4)
    grp["mean_pred"] = grp["mean_pred"].round(3)
    grp["hit_rate"] = grp["hit_rate"].round(3)
    return grp
