"""
Shot-level Expected Goals (xG) model.

Trained on StatsBomb shot events. Replaces raw historical goal counts as
the target for Dixon-Coles attack/defense fitting (xG is a much lower
variance estimator of team strength than actual goals).

Features used:
  - distance_to_goal        (Euclidean, in StatsBomb pitch units)
  - angle_to_goal           (rad, the angle the goal mouth subtends from
                             the shot location)
  - is_header               (body_part == "Head")
  - is_left_foot / is_right_foot
  - is_volley               (Volley / Half Volley / Overhead Kick)
  - is_penalty              (type == "Penalty")
  - is_free_kick            (type == "Free Kick")
  - is_open_play            (type == "Open Play")
  - is_first_time           (shot.first_time)
  - is_one_on_one           (shot.one_on_one)
  - is_open_goal            (shot.open_goal)
  - is_under_pressure       (event.under_pressure)

Model: XGBoost if available, otherwise sklearn GradientBoostingClassifier.
Output is `predict_proba` for the "goal" class. We additionally calibrate
via isotonic regression on a held-out split so raw probabilities map to
empirical goal rates (important for Poisson aggregation downstream).

This module is dependency-light: only numpy + scikit-learn are required.
"""

from __future__ import annotations

import logging
import math
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.model_selection import train_test_split

try:  # pragma: no cover - exercised only when xgboost is installed
    import xgboost as xgb  # type: ignore

    _HAS_XGB = True
except ImportError:  # pragma: no cover
    _HAS_XGB = False


_LOG = logging.getLogger(__name__)

# StatsBomb pitch frame: 0..120 x 0..80, goal centred at (120, 40),
# goal posts at (120, 36) and (120, 44).
_GOAL_X = 120.0
_GOAL_LEFT_Y = 36.0
_GOAL_RIGHT_Y = 44.0
_GOAL_CENTER_Y = 40.0

# Feature schema: ORDER MATTERS. Used for both training and inference.
FEATURE_NAMES: Tuple[str, ...] = (
    "distance_to_goal",
    "angle_to_goal",
    "is_header",
    "is_left_foot",
    "is_right_foot",
    "is_volley",
    "is_penalty",
    "is_free_kick",
    "is_open_play",
    "is_first_time",
    "is_one_on_one",
    "is_open_goal",
    "is_under_pressure",
)


# ----------------------------------------------------------------------
# Feature extraction
# ----------------------------------------------------------------------
def extract_shot_features(shot: Mapping[str, object]) -> Dict[str, float]:
    """Derive an xG feature vector (as a dict) from a normalised shot row.

    The input `shot` is a dict produced by `StatsBombOpenData.shots_for_match`
    (or any caller providing the same flat schema):
        x, y                 : float pitch coords (StatsBomb frame)
        body_part            : str | None  ("Right Foot" / "Left Foot" / "Head" / "Other")
        technique            : str | None  ("Normal" / "Volley" / "Half Volley" / ...)
        shot_type            : str | None  ("Open Play" / "Free Kick" / "Penalty" / ...)
        first_time           : bool | None
        under_pressure       : bool | None
        one_on_one           : bool | None
        open_goal            : bool | None

    Returns a dict keyed by `FEATURE_NAMES` with float (0.0/1.0) values.
    """
    x = float(shot.get("x") or 0.0)
    y = float(shot.get("y") or _GOAL_CENTER_Y)

    dx = _GOAL_X - x
    dy = _GOAL_CENTER_Y - y
    distance = math.hypot(dx, dy)

    # Angle the goal mouth subtends from (x, y).
    # Use law of cosines on triangle (shot, left-post, right-post).
    a2 = (x - _GOAL_X) ** 2 + (y - _GOAL_LEFT_Y) ** 2
    b2 = (x - _GOAL_X) ** 2 + (y - _GOAL_RIGHT_Y) ** 2
    c2 = (_GOAL_RIGHT_Y - _GOAL_LEFT_Y) ** 2
    a = math.sqrt(a2)
    b = math.sqrt(b2)
    if a < 1e-9 or b < 1e-9:
        angle = math.pi  # shot from on the goal line, hard to define
    else:
        cos_v = (a2 + b2 - c2) / (2.0 * a * b)
        cos_v = max(-1.0, min(1.0, cos_v))
        angle = math.acos(cos_v)

    body = str(shot.get("body_part") or "").lower()
    technique = str(shot.get("technique") or "").lower()
    stype = str(shot.get("shot_type") or "").lower()

    is_header = 1.0 if "head" in body else 0.0
    is_left_foot = 1.0 if "left" in body else 0.0
    is_right_foot = 1.0 if "right" in body else 0.0
    is_volley = 1.0 if ("volley" in technique or "overhead" in technique) else 0.0
    is_penalty = 1.0 if "penalty" in stype else 0.0
    is_free_kick = 1.0 if "free kick" in stype else 0.0
    is_open_play = 1.0 if "open play" in stype else 0.0
    is_first_time = 1.0 if bool(shot.get("first_time")) else 0.0
    is_one_on_one = 1.0 if bool(shot.get("one_on_one")) else 0.0
    is_open_goal = 1.0 if bool(shot.get("open_goal")) else 0.0
    is_under_pressure = 1.0 if bool(shot.get("under_pressure")) else 0.0

    return {
        "distance_to_goal": distance,
        "angle_to_goal": angle,
        "is_header": is_header,
        "is_left_foot": is_left_foot,
        "is_right_foot": is_right_foot,
        "is_volley": is_volley,
        "is_penalty": is_penalty,
        "is_free_kick": is_free_kick,
        "is_open_play": is_open_play,
        "is_first_time": is_first_time,
        "is_one_on_one": is_one_on_one,
        "is_open_goal": is_open_goal,
        "is_under_pressure": is_under_pressure,
    }


def _feature_vector(shot: Mapping[str, object]) -> np.ndarray:
    feats = extract_shot_features(shot)
    return np.array([feats[k] for k in FEATURE_NAMES], dtype=float)


def _feature_matrix(shots: Sequence[Mapping[str, object]]) -> np.ndarray:
    return np.vstack([_feature_vector(s) for s in shots])


# ----------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------
@dataclass
class XgModel:
    """Shot-level xG classifier with an isotonic calibration head."""

    backend: str = "auto"  # "auto" | "xgboost" | "sklearn"
    model_: object = field(default=None, repr=False)
    calibrator_: Optional[IsotonicRegression] = field(default=None, repr=False)
    n_shots_trained: int = 0
    n_goals_trained: int = 0
    base_rate: float = 0.10

    # ------------------------------------------------------------------
    @classmethod
    def fit(
        cls,
        shots: Sequence[Mapping[str, object]],
        *,
        backend: str = "auto",
        random_state: int = 42,
        max_estimators: int = 200,
    ) -> "XgModel":
        """Train an xG classifier on a list of shot rows. Each shot must
        carry `is_goal` (0/1) plus the feature fields consumed by
        `extract_shot_features`."""
        if not shots:
            raise ValueError("XgModel.fit requires at least one shot")

        X = _feature_matrix(shots)
        y = np.array([int(bool(s.get("is_goal"))) for s in shots], dtype=int)

        goals = int(y.sum())
        base_rate = float(goals / max(1, len(y)))

        # Single-class dataset (e.g. tests/tiny seasons) — train a degenerate
        # constant predictor that always returns the base rate.
        if goals == 0 or goals == len(y):
            inst = cls(
                backend="constant",
                model_=None,
                calibrator_=None,
                n_shots_trained=len(y),
                n_goals_trained=goals,
                base_rate=base_rate,
            )
            return inst

        eff_backend = backend
        if eff_backend == "auto":
            eff_backend = "xgboost" if _HAS_XGB else "sklearn"

        # Hold out 25% for calibration to avoid overfitting the isotonic head.
        try:
            X_tr, X_cal, y_tr, y_cal = train_test_split(
                X, y, test_size=0.25, random_state=random_state, stratify=y,
            )
        except ValueError:
            # Too few positives to stratify; use the whole set for both.
            X_tr, X_cal, y_tr, y_cal = X, X, y, y

        if eff_backend == "xgboost" and _HAS_XGB:
            model = xgb.XGBClassifier(  # type: ignore
                n_estimators=max_estimators,
                max_depth=4,
                learning_rate=0.05,
                subsample=0.8,
                colsample_bytree=0.8,
                objective="binary:logistic",
                eval_metric="logloss",
                random_state=random_state,
                tree_method="hist",
                verbosity=0,
            )
            model.fit(X_tr, y_tr)
        else:
            eff_backend = "sklearn"
            model = GradientBoostingClassifier(
                n_estimators=min(max_estimators, 200),
                max_depth=3,
                learning_rate=0.05,
                subsample=0.8,
                random_state=random_state,
            )
            model.fit(X_tr, y_tr)

        # Isotonic calibration on held-out data
        raw = model.predict_proba(X_cal)[:, 1]
        calibrator: Optional[IsotonicRegression] = None
        try:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(raw, y_cal)
            calibrator = iso
        except Exception as e:  # pragma: no cover
            _LOG.warning("xg isotonic calibration failed: %s", e)

        return cls(
            backend=eff_backend,
            model_=model,
            calibrator_=calibrator,
            n_shots_trained=len(y),
            n_goals_trained=goals,
            base_rate=base_rate,
        )

    # ------------------------------------------------------------------
    def predict_xg(self, shot: Mapping[str, object]) -> float:
        return float(self.predict_xgs([shot])[0])

    def predict_xgs(self, shots: Sequence[Mapping[str, object]]) -> np.ndarray:
        if not shots:
            return np.zeros(0, dtype=float)
        if self.backend == "constant" or self.model_ is None:
            return np.full(len(shots), self.base_rate, dtype=float)
        X = _feature_matrix(shots)
        raw = np.asarray(self.model_.predict_proba(X)[:, 1], dtype=float)  # type: ignore[attr-defined]
        if self.calibrator_ is not None:
            raw = self.calibrator_.transform(raw)
        return np.clip(raw, 1e-6, 1.0 - 1e-6)

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------
    def match_xg(
        self,
        shots: Sequence[Mapping[str, object]],
        *,
        home_team_id: str,
        away_team_id: str,
    ) -> Tuple[float, float]:
        """Sum xG per side for a single match. Shots must carry `team_id`."""
        if not shots:
            return 0.0, 0.0
        preds = self.predict_xgs(shots)
        home_xg = 0.0
        away_xg = 0.0
        for s, p in zip(shots, preds):
            tid = str(s.get("team_id") or "")
            if tid == str(home_team_id):
                home_xg += float(p)
            elif tid == str(away_team_id):
                away_xg += float(p)
        return home_xg, away_xg

    # ------------------------------------------------------------------
    def save(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            pickle.dump(
                {
                    "backend": self.backend,
                    "model": self.model_,
                    "calibrator": self.calibrator_,
                    "n_shots_trained": self.n_shots_trained,
                    "n_goals_trained": self.n_goals_trained,
                    "base_rate": self.base_rate,
                    "feature_names": list(FEATURE_NAMES),
                },
                fh,
            )

    @classmethod
    def load(cls, path: str) -> "XgModel":
        with open(path, "rb") as fh:
            blob = pickle.load(fh)
        return cls(
            backend=str(blob.get("backend", "sklearn")),
            model_=blob.get("model"),
            calibrator_=blob.get("calibrator"),
            n_shots_trained=int(blob.get("n_shots_trained", 0)),
            n_goals_trained=int(blob.get("n_goals_trained", 0)),
            base_rate=float(blob.get("base_rate", 0.10)),
        )


# ----------------------------------------------------------------------
# Convenience: aggregate match-level xG totals over a list of shots
# spanning multiple matches.
# ----------------------------------------------------------------------
def aggregate_match_xg(
    shots: Sequence[Mapping[str, object]],
    model: XgModel,
    *,
    match_home_away: Mapping[str, Tuple[str, str]],
) -> Dict[str, Tuple[float, float]]:
    """Return `{match_id: (home_xg, away_xg)}` from a flat shot list.

    `match_home_away` maps `match_id -> (home_team_id, away_team_id)` so we
    can split shots into home/away buckets without re-reading the matches.
    """
    if not shots:
        return {}
    preds = model.predict_xgs(shots)
    out: Dict[str, List[float]] = {}
    for s, p in zip(shots, preds):
        mid = str(s.get("match_id") or "")
        if not mid or mid not in match_home_away:
            continue
        home_id, _away_id = match_home_away[mid]
        bucket = out.setdefault(mid, [0.0, 0.0])
        if str(s.get("team_id") or "") == str(home_id):
            bucket[0] += float(p)
        else:
            bucket[1] += float(p)
    return {mid: (v[0], v[1]) for mid, v in out.items()}
