"""Seed the paper-trading DB with realistic-looking sample data so the
dashboard has something to render.

Run:
    python scripts/seed_demo_data.py
    # then refresh http://localhost:5173

Idempotent: safe to re-run; clears prior demo rows first (matched by
strategy='demo_seed' and a special cohort_version='demo-cohort').
"""

from __future__ import annotations

import hashlib
import random
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import settings  # noqa: E402
from src.paper.executor import PaperExecutor, SCHEMA  # noqa: E402
from src.jobs.copy_runner import SIGNAL_LOG_SCHEMA  # noqa: E402
from src.utils.calibration import CalibrationStore  # noqa: E402
from src.risk.strategy_kill import SCHEMA as STRAT_SCHEMA  # noqa: E402

random.seed(20260604)

DEMO_STRATEGY = "demo_seed"
DEMO_COHORT_VERSION = "demo-cohort-v1"


def _ensure_schema(conn: sqlite3.Connection) -> None:
    from src.paper.migrations import run_all
    run_all(conn)
    conn.executescript(SCHEMA)
    conn.executescript(SIGNAL_LOG_SCHEMA)
    conn.commit()


def _clear_demo(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM paper_trades WHERE strategy = ?", (DEMO_STRATEGY,))
    conn.execute(
        "DELETE FROM polymarket_consensus_signals WHERE cohort_version = ?",
        (DEMO_COHORT_VERSION,),
    )
    conn.execute(
        "DELETE FROM polymarket_cohort_snapshots WHERE cohort_version = ?",
        (DEMO_COHORT_VERSION,),
    )
    conn.commit()


def seed_cohort_snapshot(conn: sqlite3.Connection) -> int:
    """Persist a realistic ranked smart-money cohort for the dashboard."""
    snapshot_unix = int(time.time())
    n = 0
    for i, w in enumerate(DEMO_WALLETS):
        # Synthesise plausible-looking stats: top wallets have higher PnL,
        # more trades, better stability.
        pnl = round(60_000 / (i + 1) + random.uniform(-3_000, 3_000), 2)
        trades = 200 - i * 18 + random.randint(-10, 10)
        resolved = max(20, trades // 3)
        stability = round(0.95 - i * 0.06 + random.uniform(-0.03, 0.03), 3)
        score = round(pnl ** 0.5 * stability, 2)
        last_trade = snapshot_unix - random.randint(120, 14_400)
        conn.execute(
            """INSERT INTO polymarket_cohort_snapshots(
                snapshot_unix, cohort_version, wallet, rank, score,
                realized_pnl_usd, n_trades, n_resolved, last_trade_unix, pnl_stability)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_unix, DEMO_COHORT_VERSION, w, i + 1, score,
             pnl, trades, resolved, last_trade, stability),
        )
        n += 1
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Calibration + strategy state (separate DB)
# ---------------------------------------------------------------------------


DEMO_STRATEGIES = [
    ("kalshi_arb", 0.09, True, None),
    ("consensus_copy", 0.17, True, None),
    ("resolution_decay", 0.21, True, None),
    ("momentum_reversion", 0.29, False, "Brier 0.2912 > 0.25 (n=34)"),
]


def seed_calibration() -> tuple[int, int]:
    cal_path = Path(settings.calibration_db_path)
    cal_path.parent.mkdir(parents=True, exist_ok=True)
    CalibrationStore(str(cal_path))  # ensures predictions table

    conn = sqlite3.connect(str(cal_path))
    try:
        conn.executescript(STRAT_SCHEMA)
        conn.execute("DELETE FROM predictions WHERE notes = 'demo_seed'")
        conn.execute("DELETE FROM strategy_state WHERE strategy IN (" +
                     ",".join("?" for _ in DEMO_STRATEGIES) + ")",
                     [s[0] for s in DEMO_STRATEGIES])

        n_pred = 0
        now = datetime.now(timezone.utc)
        for strat, target_brier, *_ in DEMO_STRATEGIES:
            for i in range(60):
                # Predicted prob ~ U(0.2, 0.8); outcome biased to match brier target
                pred = round(random.uniform(0.2, 0.8), 3)
                # With prob = pred + noise vs random, generate outcomes whose
                # mean squared error approximates target_brier.
                err = random.gauss(0, target_brier ** 0.5)
                target = max(0.0, min(1.0, pred + err))
                outcome = 1 if random.random() < target else 0
                decided_at = (now - timedelta(hours=60 - i)).isoformat(timespec="seconds")
                resolved_at = (now - timedelta(hours=60 - i - 0.5)).isoformat(timespec="seconds")
                conn.execute(
                    """INSERT INTO predictions(strategy, ticker, side, predicted_prob,
                        price_at_decision, decided_at, resolved_at, outcome, notes)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (strat, f"DEMO-{i:03d}", "YES", pred, pred,
                     decided_at, resolved_at, outcome, "demo_seed"),
                )
                n_pred += 1

        n_strat = 0
        for strat, brier, enabled, reason in DEMO_STRATEGIES:
            conn.execute(
                """INSERT INTO strategy_state(strategy, enabled, last_brier,
                    last_n_samples, last_evaluated_at, disabled_reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (strat, int(enabled), brier, 60,
                 now.isoformat(timespec="seconds"), reason),
            )
            n_strat += 1
        conn.commit()
    finally:
        conn.close()
    return n_pred, n_strat


# ---------------------------------------------------------------------------
# Fills — a 48-hour stream across both venues
# ---------------------------------------------------------------------------


KALSHI_TICKERS = [
    ("PRES-2028-DEM", "YES"),
    ("FED-RATE-CUT-JUL", "YES"),
    ("FED-RATE-CUT-JUL", "NO"),
    ("SBOWL-WINNER-KC", "YES"),
    ("BTC-100K-EOY", "YES"),
    ("CPI-OVER-3-AUG", "NO"),
]
POLY_MARKETS = [
    ("0xc0nd1ti0n_btc_120k_eoy", "Will Bitcoin reach $120k by EOY 2026?", 0),
    ("0xc0nd1ti0n_eth_5k_q3", "Ethereum above $5k by end of Q3?", 0),
    ("0xc0nd1ti0n_lakers_finals", "Lakers reach NBA Finals 2026?", 1),
    ("0xc0nd1ti0n_msft_3t", "Microsoft mkt cap > $3.5T by EOY?", 0),
]


def seed_fills(conn: sqlite3.Connection) -> int:
    now = datetime.now(timezone.utc)
    n = 0
    cur_price: dict[tuple[str, str, str], float] = {}
    for hour in range(48 * 6, 0, -1):  # every 10 min over 48h
        ts = now - timedelta(minutes=hour * 10)
        if random.random() > 0.55:  # ~half the slots have an event
            continue

        venue = "kalshi" if random.random() < 0.6 else "polymarket"
        if venue == "kalshi":
            ticker, side = random.choice(KALSHI_TICKERS)
            base = cur_price.get((venue, ticker, side), random.uniform(0.20, 0.80))
            price = max(0.02, min(0.98, base + random.gauss(0, 0.015)))
            contracts = random.choice([5, 10, 15, 20, 25, 50])
            is_maker = random.random() < 0.4
            fee = 0.0 if is_maker else round(0.07 * contracts * price * (1 - price), 4)
        else:
            cond_id, _, idx = random.choice(POLY_MARKETS)
            ticker = cond_id
            side = "YES" if idx == 0 else f"OUT{idx}"
            base = cur_price.get((venue, ticker, side), random.uniform(0.15, 0.75))
            price = max(0.02, min(0.98, base + random.gauss(0, 0.012)))
            contracts = random.choice([10, 25, 50, 100])
            is_maker = False
            fee = 0.05  # flat gas

        action = "buy" if random.random() < 0.62 else "sell"
        cost = round(price * contracts, 4)

        # Avoid selling more than we have: skip a sell if no prior buy in same key
        key = (venue, ticker, side)
        if action == "sell" and key not in cur_price:
            action = "buy"
        cur_price[key] = price

        strategy = DEMO_STRATEGY
        notes = (
            "demo: arb leg" if venue == "kalshi" and random.random() < 0.3
            else f"demo: consensus_copy" if venue == "polymarket"
            else "demo"
        )

        conn.execute(
            """INSERT INTO paper_trades(placed_at, strategy, venue, ticker, side,
                action, contracts, price, is_maker, fees, cost, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ts.isoformat(timespec="seconds"),
                strategy, venue, ticker, side, action,
                contracts, round(price, 4), int(is_maker), fee, cost, notes,
            ),
        )
        n += 1
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Polymarket consensus signals
# ---------------------------------------------------------------------------

DEMO_WALLETS = [
    "0xabc1234567890abcdef000000000000000000aa01",
    "0xabc1234567890abcdef000000000000000000aa02",
    "0xabc1234567890abcdef000000000000000000aa03",
    "0xabc1234567890abcdef000000000000000000aa04",
    "0xabc1234567890abcdef000000000000000000aa05",
    "0xabc1234567890abcdef000000000000000000aa06",
    "0xabc1234567890abcdef000000000000000000aa07",
    "0xabc1234567890abcdef000000000000000000aa08",
]


def seed_signals(conn: sqlite3.Connection) -> int:
    now = int(time.time())
    n = 0
    for hours_ago in [36, 28, 22, 18, 12, 8, 5, 3, 2, 1, 0.5, 0.15]:
        cond_id, question, outcome_idx = random.choice(POLY_MARKETS)
        token_id = f"{cond_id}_token_{outcome_idx}"
        decision_unix = now - int(hours_ago * 3600)
        first_trade = decision_unix - random.randint(60, 1800)
        last_trade = decision_unix - random.randint(10, 60)
        window_end = decision_unix
        window_start = window_end - 24 * 3600
        cohort_size = 8
        consensus_k = random.choice([3, 4, 5])
        agreeing = random.sample(DEMO_WALLETS, k=consensus_k)
        avg_entry = round(random.uniform(0.32, 0.71), 3)
        notional = round(random.uniform(2_500, 38_000), 2)

        # ~60% filled, mix of rejection reasons
        roll = random.random()
        if roll < 0.62:
            decision, exec_px, exec_qty = "filled", round(avg_entry + random.uniform(0.001, 0.018), 3), random.choice([10, 15, 25])
            slippage = round((exec_px - avg_entry) * 100, 2)
            notes = "filled at live ask"
        elif roll < 0.82:
            decision, exec_px, exec_qty = "rejected_slippage", None, None
            slippage = round(random.uniform(2.5, 7.0), 2)
            notes = f"slippage {slippage}c > 2c gate"
        elif roll < 0.93:
            decision, exec_px, exec_qty, slippage = "rejected_no_book", None, None, None
            notes = "no asks on CLOB"
        else:
            decision, exec_px, exec_qty, slippage = "rejected_other", None, None, None
            notes = "kill-switch active"

        seed_str = f"{cond_id}|{token_id}|{window_start}|{DEMO_COHORT_VERSION}|{decision_unix}"
        idem = hashlib.sha1(seed_str.encode()).hexdigest()

        conn.execute(
            """INSERT OR REPLACE INTO polymarket_consensus_signals(
                idempotency_key, detected_at, condition_id, outcome_token_id,
                outcome_index, market_question, cohort_size, consensus_k,
                agreeing_wallets, first_trade_unix, last_trade_unix,
                window_start_unix, window_end_unix, cohort_version,
                avg_wallet_entry_price, total_wallet_notional_usd,
                decision, executed_price, executed_contracts, slippage_cents,
                decision_unix, notes
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                idem,
                datetime.fromtimestamp(decision_unix, tz=timezone.utc).isoformat(timespec="seconds"),
                cond_id, token_id, outcome_idx, question,
                cohort_size, consensus_k,
                ",".join(agreeing),
                first_trade, last_trade, window_start, window_end,
                DEMO_COHORT_VERSION, avg_entry, notional,
                decision, exec_px, exec_qty, slippage,
                decision_unix, notes,
            ),
        )
        n += 1
    conn.commit()
    return n


def main() -> None:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Use PaperExecutor briefly to ensure tables + migrations are present.
    PaperExecutor(str(db_path), settings.starting_bankroll)

    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_schema(conn)
        _clear_demo(conn)
        nf = seed_fills(conn)
        ns = seed_signals(conn)
        nc = seed_cohort_snapshot(conn)
    finally:
        conn.close()
    np, nstrat = seed_calibration()
    print(f"Seeded {nf} fills + {ns} signals + {nc} cohort rows in {db_path}")
    print(f"Seeded {np} predictions + {nstrat} strategy_state rows in {settings.calibration_db_path}")


if __name__ == "__main__":
    main()
