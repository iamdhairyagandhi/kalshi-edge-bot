"""
Soccer bet-builder pipeline.

Layered models (ratings + scoring + player + cards + state-space) feed a
joint Monte Carlo simulator. The simulator produces per-sim match traces
that the bet-builder turns into fair prices for arbitrary leg combos.

Public surface:
    from src.sports.soccer import (
        types, ratings, models, simulator, calibration, pricing, data,
    )
"""

from src.sports.soccer import (  # noqa: F401
    calibration,
    data,
    models,
    pricing,
    ratings,
    simulator,
    types,
)
