"""Seed a demo paper.db + calibration.db with realistic-ish data so the
dashboard has something to render."""

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.paper.executor import PaperExecutor
from src.strategies.overround_arb import ArbOpportunity
from src.utils.calibration import CalibrationStore
from src.risk.strategy_kill import disable

random.seed(42)

PAPER_DB = "data/demo_paper.db"
CAL_DB = "data/demo_cal.db"

# ------------------------------------------------------------ paper portfolio
ex = PaperExecutor(PAPER_DB, starting_bankroll=10_000.0)

tickers = ["KX-BTC-100K-2026", "KX-NBA-LAL-WIN", "KX-SPX-6500-EOM",
           "KX-CPI-3PCT-JUN", "KX-FED-50BP-JUL", "KX-NFL-KC-DIV",
           "KX-OPEC-CUT-Q3", "KX-AAPL-200-EOM"]
strategies = ["overround_arb", "safe_compounder", "as_mm"]

for i in range(45):
    t = random.choice(tickers)
    yes_p = round(random.uniform(0.30, 0.55), 2)
    no_p = round(random.uniform(0.30, 0.55), 2)
    if yes_p + no_p >= 0.99:  # need a real arb
        yes_p = round(yes_p - 0.05, 2)
    contracts = random.randint(3, 15)
    opp = ArbOpportunity(
        ticker=t, direction="buy_both", contracts=contracts,
        yes_price=yes_p, no_price=no_p,
        gross_edge_per_contract=1.0 - yes_p - no_p,
        fees_per_contract=0.02,
        net_edge_per_contract=1.0 - yes_p - no_p - 0.02,
        net_profit_total=(1.0 - yes_p - no_p - 0.02) * contracts,
    )
    try:
        ex.execute_arb(opp, strategy=random.choice(strategies))
    except Exception as e:
        pass

print(f"Paper: cash=${ex.portfolio.cash:.2f}, open={len(ex.portfolio.positions)}")

# -------------------------------------------------------- calibration history
store = CalibrationStore(CAL_DB)
base = datetime.now(timezone.utc) - timedelta(days=20)

# Good strategy: arb is well-calibrated
for i in range(40):
    pred = random.uniform(0.4, 0.95)
    outcome = 1 if random.random() < pred else 0
    pid = store.log_prediction(
        strategy="overround_arb", ticker=f"K{i}", side="YES",
        predicted_prob=pred, price_at_decision=pred,
        decided_at=(base + timedelta(hours=i)).isoformat(),
    )
    store.resolve(pid, outcome=outcome,
                  resolved_at=(base + timedelta(hours=i+24)).isoformat())

# Marginal strategy
for i in range(35):
    pred = random.uniform(0.5, 0.9)
    # Outcome only loosely tracks prediction
    outcome = 1 if random.random() < pred * 0.7 else 0
    pid = store.log_prediction(
        strategy="safe_compounder", ticker=f"S{i}", side="NO",
        predicted_prob=pred, price_at_decision=pred,
        decided_at=(base + timedelta(hours=i)).isoformat(),
    )
    store.resolve(pid, outcome=outcome,
                  resolved_at=(base + timedelta(hours=i+24)).isoformat())

# Bad strategy: confidently wrong
for i in range(32):
    pred = 0.85
    outcome = 0 if random.random() < 0.7 else 1
    pid = store.log_prediction(
        strategy="experimental_llm", ticker=f"E{i}", side="YES",
        predicted_prob=pred, price_at_decision=pred,
        decided_at=(base + timedelta(hours=i)).isoformat(),
    )
    store.resolve(pid, outcome=outcome,
                  resolved_at=(base + timedelta(hours=i+24)).isoformat())

# Also seed some unresolved
for i in range(8):
    store.log_prediction(
        strategy="overround_arb", ticker=f"OPEN{i}", side="YES",
        predicted_prob=random.uniform(0.4, 0.9), price_at_decision=0.5,
        decided_at=datetime.now(timezone.utc).isoformat(),
    )

# Disable the bad one via the kill switch
disable(CAL_DB, "experimental_llm", reason="Brier=0.51 > 0.25 threshold")

print(f"Calibration: 115 predictions logged, experimental_llm disabled.")
print(f"DBs ready at {PAPER_DB} and {CAL_DB}")
