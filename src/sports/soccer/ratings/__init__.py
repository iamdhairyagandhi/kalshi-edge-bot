"""Ratings sub-package."""

from src.sports.soccer.ratings.elo import (  # noqa: F401
    EloRating,
    EloTable,
    expected_score,
    update_match,
)
