"""Soccer AI Guru context builder — gathers team form, h2h history, and market sentiment."""

from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from src.clients.soccer_ai_guru import TeamForm, MatchContext
from src.sports.soccer.data.store import SoccerStore
from src.sports.soccer.models.dixon_coles import DixonColesModel


def build_match_context(
    *,
    store: SoccerStore,
    fixture: Dict[str, object],
    team_names: Dict[str, str],
    elo_table: any,  # EloTable instance
    score_model: DixonColesModel,
    events: List[Dict[str, object]],  # from OddsApiClient
    days_since_last_match_home: int = 3,
    days_since_last_match_away: int = 3,
) -> MatchContext:
    """Build complete match context for AI guru analysis.
    
    Args:
        store: SoccerStore instance
        fixture: Fixture dict with home_team_id, away_team_id, kickoff_unix
        team_names: Mapping of team_id -> name
        elo_table: Team Elo ratings
        score_model: Fitted DixonColes model with attack/defense params
        events: Odds events from OddsApiClient
        days_since_last_match_home: Days of rest estimate (if exact data unavailable)
        days_since_last_match_away: Days of rest estimate (if exact data unavailable)
        
    Returns:
        MatchContext ready for AI analysis
    """
    home_id = str(fixture["home_team_id"])
    away_id = str(fixture["away_team_id"])
    neutral = bool(fixture.get("neutral_venue", False))
    kickoff_unix = int(fixture["kickoff_unix"])

    # Query all historical matches
    all_matches = store.historical_matches()

    # Build recent form for both teams
    home_form = _build_team_form(
        team_id=home_id,
        team_name=team_names.get(home_id, home_id),
        matches=all_matches,
        elo=elo_table.get(home_id).rating if hasattr(elo_table, 'get') else 1500.0,
        score_model=score_model,
        lookback_days=30,
        fixture_kickoff_unix=kickoff_unix,
    )
    away_form = _build_team_form(
        team_id=away_id,
        team_name=team_names.get(away_id, away_id),
        matches=all_matches,
        elo=elo_table.get(away_id).rating if hasattr(elo_table, 'get') else 1500.0,
        score_model=score_model,
        lookback_days=30,
        fixture_kickoff_unix=kickoff_unix,
    )

    # H2H history
    h2h_record, h2h_goals_home, h2h_goals_away = _build_h2h(
        home_id, away_id, all_matches
    )

    # Rest days (simplified — assumes ~3 days between matches if unavailable)
    days_rest_home = days_since_last_match_home
    days_rest_away = days_since_last_match_away

    # Market sentiment from odds
    sentiment, line_movement = _extract_sentiment(events, team_names, home_id, away_id)

    # Injuries (placeholder — would integrate with injury database if available)
    injuries_home: List[str] = []
    injuries_away: List[str] = []

    return MatchContext(
        home_team=home_form,
        away_team=away_form,
        h2h_record=h2h_record,
        h2h_goals_home=h2h_goals_home,
        h2h_goals_away=h2h_goals_away,
        days_rest_home=days_rest_home,
        days_rest_away=days_rest_away,
        venue_type="neutral" if neutral else "home",
        market_sentiment=sentiment,
        recent_line_movement=line_movement,
        injuries_home=injuries_home,
        injuries_away=injuries_away,
    )


def _build_team_form(
    *,
    team_id: str,
    team_name: str,
    matches: List[Dict[str, object]],
    elo: float,
    score_model: DixonColesModel,
    lookback_days: int,
    fixture_kickoff_unix: int,
) -> TeamForm:
    """Build recent form for a team."""
    # Filter to matches within lookback window, before fixture
    cutoff = fixture_kickoff_unix - (lookback_days * 86400)
    recent_matches = [
        m for m in matches
        if int(m.get("kickoff_unix", 0)) >= cutoff and int(m.get("kickoff_unix", 0)) < fixture_kickoff_unix
    ]

    # Determine which team is home/away in each match
    home_matches = [m for m in recent_matches if str(m.get("home_team_id")) == team_id]
    away_matches = [m for m in recent_matches if str(m.get("away_team_id")) == team_id]

    # Results for last 5 matches
    last_5_results: List[str] = []
    total_goals_for = 0.0
    total_goals_against = 0.0
    match_count = 0

    for m in (home_matches + away_matches)[-5:]:
        if str(m.get("home_team_id")) == team_id:
            hg = int(m.get("home_goals", 0))
            ag = int(m.get("away_goals", 0))
        else:
            hg = int(m.get("away_goals", 0))
            ag = int(m.get("home_goals", 0))

        if hg > ag:
            last_5_results.append("W")
        elif hg < ag:
            last_5_results.append("L")
        else:
            last_5_results.append("D")

        total_goals_for += hg
        total_goals_against += ag
        match_count += 1

    # Pad to 5 if fewer matches
    while len(last_5_results) < 5:
        last_5_results.insert(0, "-")

    last_5_gf = total_goals_for / max(1, match_count)
    last_5_ga = total_goals_against / max(1, match_count)

    # Win streak (positive for wins, negative for losses)
    win_streak = 0
    for result in reversed(last_5_results):
        if result == "W":
            win_streak += 1
        elif result == "L":
            win_streak -= 1
        else:
            break

    # Get attack/defense params from model
    attack = float(score_model.params.attack.get(team_id, 0.0))
    defense = float(score_model.params.defense.get(team_id, 0.0))

    return TeamForm(
        team_id=team_id,
        team_name=team_name,
        last_5_results=last_5_results,
        last_5_goals_for=last_5_gf,
        last_5_goals_against=last_5_ga,
        elo_rating=elo,
        attack_strength=attack,
        defense_strength=defense,
        win_streak=win_streak,
    )


def _build_h2h(
    home_id: str, away_id: str, all_matches: List[Dict[str, object]]
) -> Tuple[str, float, float]:
    """Calculate h2h record and goals."""
    h2h_matches = [
        m for m in all_matches
        if (str(m.get("home_team_id")) == home_id and str(m.get("away_team_id")) == away_id)
        or (str(m.get("home_team_id")) == away_id and str(m.get("away_team_id")) == home_id)
    ]

    home_wins = 0
    draws = 0
    away_wins = 0
    home_goals = 0.0
    away_goals = 0.0

    for m in h2h_matches:
        hg = int(m.get("home_goals", 0))
        ag = int(m.get("away_goals", 0))

        if str(m.get("home_team_id")) == home_id:
            # This is a match where home_id is home team
            if hg > ag:
                home_wins += 1
            elif hg < ag:
                away_wins += 1
            else:
                draws += 1
            home_goals += hg
            away_goals += ag
        else:
            # This is a match where home_id is away team
            if ag > hg:
                home_wins += 1
            elif ag < hg:
                away_wins += 1
            else:
                draws += 1
            home_goals += ag
            away_goals += hg

    record = f"H: {home_wins}, D: {draws}, A: {away_wins}"
    avg_home_goals = home_goals / max(1, len(h2h_matches))
    avg_away_goals = away_goals / max(1, len(h2h_matches))

    return record, avg_home_goals, avg_away_goals


def _extract_sentiment(
    events: List[Dict[str, object]],
    team_names: Dict[str, str],
    home_id: str,
    away_id: str,
) -> Tuple[Dict[str, float], str]:
    """Extract market sentiment from odds events."""
    sentiment: Dict[str, float] = {}
    line_movement = "No line data available"

    if not events:
        return sentiment, line_movement

    # Simplified extraction: find h2h odds for the teams
    for event in events:
        home_name = event.get("home_team", "")
        away_name = event.get("away_team", "")

        # Try to match team names
        if not home_name or not away_name:
            continue

        bookmakers = event.get("bookmakers", [])
        if not bookmakers:
            continue

        # Use first bookmaker's odds
        bm = bookmakers[0]
        markets = bm.get("markets", [])
        h2h_market = next((m for m in markets if m.get("key") == "h2h"), None)

        if not h2h_market:
            continue

        outcomes = h2h_market.get("outcomes", [])
        for outcome in outcomes:
            name = outcome.get("name", "")
            price = outcome.get("price", 0)

            # Convert decimal odds to implied probability
            if price > 0:
                implied = 1.0 / price
                if name == home_name:
                    sentiment["home_win_implied"] = implied
                elif name == away_name:
                    sentiment["away_win_implied"] = implied
                elif name == "Draw":
                    sentiment["draw_implied"] = implied

        # Line movement heuristic (would need historical odds to track true movement)
        if sentiment:
            line_movement = (
                f"Market implied: Home {sentiment.get('home_win_implied', 0)*100:.0f}%, "
                f"Draw {sentiment.get('draw_implied', 0)*100:.0f}%, "
                f"Away {sentiment.get('away_win_implied', 0)*100:.0f}%"
            )

    return sentiment, line_movement
