"""TFI tracker for adverse-flow vetoes."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass
class Trade:
    timestamp: float
    signed_size: float          # + if aggressor bought YES; - if aggressor sold


@dataclass
class TFISample:
    z_score: float
    short_tfi: float
    mean: float
    std: float
    n_long_samples: int


class TFITracker:
    """Maintains rolling TFI samples for a single ticker."""

    def __init__(self, short_window_seconds: float = 30.0,
                 long_window_seconds: float = 1800.0,
                 max_trades: int = 5000):
        self.short_window = short_window_seconds
        self.long_window = long_window_seconds
        self._trades: Deque[Trade] = deque(maxlen=max_trades)
        self._tfi_samples: Deque[float] = deque(maxlen=max_trades)

    def add_trade(self, t: Trade) -> None:
        cutoff = t.timestamp - self.long_window
        while self._trades and self._trades[0].timestamp < cutoff:
            self._trades.popleft()
        self._trades.append(t)
        self._tfi_samples.append(self._short_window_tfi(t.timestamp))

    def _short_window_tfi(self, now: float) -> float:
        cutoff = now - self.short_window
        return sum(t.signed_size for t in self._trades if t.timestamp >= cutoff)

    def sample(self, now: Optional[float] = None) -> TFISample:
        if now is None:
            now = time.time()
        short_tfi = self._short_window_tfi(now)
        n = len(self._tfi_samples)
        if n < 5:
            return TFISample(0.0, short_tfi, 0.0, 0.0, n)
        mean = sum(self._tfi_samples) / n
        var = sum((s - mean) ** 2 for s in self._tfi_samples) / max(1, n - 1)
        std = math.sqrt(var)
        z = 0.0 if std < 1e-9 else (short_tfi - mean) / std
        return TFISample(z, short_tfi, mean, std, n)


def should_veto_yes_buy(sample: TFISample, threshold: float = 2.0) -> bool:
    return sample.z_score < -threshold


def should_veto_no_buy(sample: TFISample, threshold: float = 2.0) -> bool:
    return sample.z_score > threshold
