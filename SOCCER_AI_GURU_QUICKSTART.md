"""
Quick start: Soccer AI Guru

This file demonstrates how to use the professional soccer bettor AI.
"""

# ============================================================================
# 1. Environment Setup
# ============================================================================
# Add to .env:
"""
# Enable the AI Guru
SOCCER_AI_GURU_ENABLED=true

# Use gpt-4o-mini (or gpt-4 for higher quality)
SOCCER_AI_GURU_MODEL=gpt-4o-mini

# Your OpenAI key
OPENAI_API_KEY=sk-your-key-here
"""

# ============================================================================
# 2. Basic Usage via Dashboard
# ============================================================================
# Just visit: http://localhost:5173/soccer
# 
# The betslips now include AI Guru analysis automatically!
# Check the "reasons" section for AI insights.


# ============================================================================
# 3. Programmatic Usage
# ============================================================================

from src.clients.soccer_ai_guru import SoccerAIGuru, MatchContext, TeamForm
from src.sports.soccer.ai.context_builder import build_match_context
from src.sports.soccer.data.store import SoccerStore
from src.config import settings

# Example: Analyze England vs Denmark match

store = SoccerStore("data/soccer.db")

# Build context from fixture, form, h2h, odds
fixture = {
    "id": "123",
    "home_team_id": "england",
    "away_team_id": "denmark",
    "kickoff_unix": 1687500000,
    "neutral_venue": False,
}

context = build_match_context(
    store=store,
    fixture=fixture,
    team_names={"england": "England", "denmark": "Denmark"},
    elo_table=elo_table,  # Your EloTable instance
    score_model=score_model,  # Your fitted DixonColesModel
    events=odds_events,  # From OddsApiClient
)

# Call AI guru
with SoccerAIGuru(api_key=settings.openai_api_key) as guru:
    assessment = guru.analyze_match(
        context,
        model_probability_home=0.68,  # Dixon-Coles baseline
        model_probability_draw=0.15,
        model_probability_away=0.17,
    )

# Use the assessment
print(f"Home adjustment: {assessment.probability_adjustment_home:+.3f}")
print(f"Confidence multiplier: {assessment.confidence_multiplier:.2f}x")
print(f"Rationale: {assessment.rationale}")
for insight in assessment.key_insights:
    print(f"  • {insight}")


# ============================================================================
# 4. What the AI Guru Analyzes
# ============================================================================

# Form Analysis:
# - Last 5 match results (W/D/L streaks)
# - Goals for/against trends
# - Attack/defense strength parameters
# - Win streaks (positive = hot, negative = cold)

# Head-to-Head:
# - Historical record (H wins, Draws, A wins)
# - Average goals per match
# - Patterns in previous meetings

# Market Sentiment:
# - Implied probabilities from odds
# - Line movement (which way money is flowing)
# - Sharp vs public action indicators

# Context:
# - Rest days between matches
# - Home advantage (neutral vs home)
# - Key player absences (if data available)
# - Team strength (Elo, attack/defense stats)


# ============================================================================
# 5. Configuration Options
# ============================================================================

# Disable AI Guru entirely (use baseline model only)
# SOCCER_AI_GURU_ENABLED=false

# Use GPT-4 for higher quality (more expensive)
# SOCCER_AI_GURU_MODEL=gpt-4

# Limit to top N matches per call (for cost control)
# SOCCER_AI_GURU_MAX_MATCHES=10


# ============================================================================
# 6. Expected Probability Adjustments
# ============================================================================

# Typical adjustments are small but meaningful:
#
# Strong home form + away team cold:
#   → +0.05 to +0.08 home adjustment
#
# Market overreacting to recent result:
#   → ±0.03 against the crowd
#
# Teams rarely play well away:
#   → -0.04 to -0.06 away adjustment
#
# Even matchup (model well-calibrated):
#   → ±0.01 to ±0.02 fine-tuning only
#
# Confidence adjustments typically 0.85x to 1.15x baseline


# ============================================================================
# 7. Troubleshooting
# ============================================================================

# Q: AI Guru isn't running
# A: Check SOCCER_AI_GURU_ENABLED=true and OPENAI_API_KEY is set

# Q: Getting API errors
# A: Verify your OpenAI key is valid and has quota remaining

# Q: Betslips are taking too long
# A: AI adds ~2-5 seconds per match. Use SOCCER_AI_GURU_ENABLED=false if needed

# Q: Adjustments seem wrong
# A: Rebuild match history in DB. AI needs at least 5 recent matches per team.

# Q: High costs
# A: Each analysis uses ~1000 tokens. Adjust max_matches or use gpt-4o-mini.
