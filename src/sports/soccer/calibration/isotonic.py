"""
Calibration + CLV layer.

Two responsibilities:
  1. Probability calibration. Raw model probabilities are rarely
     well-calibrated; we fit per-market isotonic regression on resolved
     predictions and apply the mapping at inference time.
  2. Closing-line value (CLV). Beating Pinnacle's *closing* line is a
     stronger predictor of long-run profit than ROI on actual results in
     small samples — backtests should target it.

Both are stateless utilities here; persistence lives in `data.store`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

try:
    from sklearn.isotonic import IsotonicRegression
except ImportError:  # pragma: no cover
    IsotonicRegression = None  # type: ignore[assignment]


@dataclass
class PredictionRecord:
    market_type: str          # e.g. "match_result", "anytime_scorer"
    fair_probability: float   # what the model said
    book_decimal_odds: Optional[float] = None  # what we got
    pinnacle_close_decimal: Optional[float] = None  # CLV benchmark
    outcome: Optional[int] = None  # 0/1, None until resolved
    note: Optional[str] = None


def brier_score(records: Sequence[PredictionRecord]) -> float:
    resolved = [r for r in records if r.outcome is not None]
    if not resolved:
        return float("nan")
    sq = [(r.fair_probability - float(r.outcome or 0)) ** 2 for r in resolved]
    return float(sum(sq) / len(sq))


def reliability_buckets(
    records: Sequence[PredictionRecord], n_bins: int = 10
) -> List[Dict[str, float]]:
    """Equal-width bins over [0,1] with empirical hit rate per bin."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out: List[Dict[str, float]] = []
    for i in range(n_bins):
        lo, hi = float(edges[i]), float(edges[i + 1])
        in_bin = [
            r for r in records
            if r.outcome is not None and lo <= r.fair_probability < (hi if i < n_bins - 1 else hi + 1e-9)
        ]
        if not in_bin:
            out.append({
                "bin_lo": lo, "bin_hi": hi,
                "n": 0, "predicted": 0.0, "observed": 0.0,
            })
            continue
        pred = sum(r.fair_probability for r in in_bin) / len(in_bin)
        obs = sum(int(r.outcome or 0) for r in in_bin) / len(in_bin)
        out.append({
            "bin_lo": lo, "bin_hi": hi,
            "n": len(in_bin), "predicted": pred, "observed": obs,
        })
    return out


def clv_decimal(
    book_decimal: float,
    pinnacle_close_decimal: float,
) -> float:
    """CLV in implied-prob delta. Positive means we beat the closing line.

    We "beat the close" when the odds we got carried a *lower* implied
    probability than the close (i.e. our decimal odds were higher).

        CLV = (1/close) - (1/book)
            > 0  if book_decimal > close_decimal  (we got better odds)
            < 0  if book_decimal < close_decimal
    """
    if book_decimal <= 1.0 or pinnacle_close_decimal <= 1.0:
        return float("nan")
    return (1.0 / pinnacle_close_decimal) - (1.0 / book_decimal)


@dataclass
class CalibrationLayer:
    """One IsotonicRegression per market type. Defaults to identity if unfit."""
    market_to_iso: Dict[str, "IsotonicRegression"] = field(default_factory=dict)
    n_per_market: Dict[str, int] = field(default_factory=dict)

    def fit(self, records: Sequence[PredictionRecord]) -> None:
        if IsotonicRegression is None:
            raise RuntimeError(
                "scikit-learn not installed; install it to use CalibrationLayer.fit"
            )
        by_mkt: Dict[str, List[PredictionRecord]] = {}
        for r in records:
            if r.outcome is None:
                continue
            by_mkt.setdefault(r.market_type, []).append(r)
        for mkt, rows in by_mkt.items():
            if len(rows) < 30:  # not enough to fit
                continue
            x = np.array([r.fair_probability for r in rows])
            y = np.array([float(r.outcome or 0) for r in rows])
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(x, y)
            self.market_to_iso[mkt] = iso
            self.n_per_market[mkt] = len(rows)

    def calibrate(self, market_type: str, p: float) -> float:
        iso = self.market_to_iso.get(market_type)
        if iso is None:
            return float(p)
        return float(iso.predict([p])[0])
