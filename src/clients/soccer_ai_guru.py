"""AI Soccer Guru — professional bettor analysis with form, history, and sentiment.

Acts as a world-class soccer analyst considering:
- Historical h2h records and patterns
- Current form (recent results, momentum, goal trends)
- Market sentiment (odds movements, public action indicators)
- Injury/suspension impacts when available
- Environmental factors (venue, travel, rest days)

Returns probability adjustments and confidence modifiers for betslips.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class TeamForm:
    """Recent form snapshot for a team."""
    team_id: str
    team_name: str
    last_5_results: List[str]  # e.g. ["W", "D", "L", "W", "W"]
    last_5_goals_for: float
    last_5_goals_against: float
    elo_rating: float
    attack_strength: float
    defense_strength: float
    win_streak: int  # negative = loss streak


@dataclass(frozen=True)
class MatchContext:
    """Full context for AI analysis of a match."""
    home_team: TeamForm
    away_team: TeamForm
    h2h_record: str  # e.g. "H: 3, D: 2, A: 1"
    h2h_goals_home: float
    h2h_goals_away: float
    days_rest_home: int
    days_rest_away: int
    venue_type: str  # "home" | "neutral"
    market_sentiment: Dict[str, float]  # e.g. {"home_win_implied": 0.52, "under_2_5_implied": 0.48}
    recent_line_movement: str  # e.g. "Home drifted from -110 to -130; Sharp money on home"
    injuries_home: List[str]  # list of key players out
    injuries_away: List[str]


class SoccerAIGuruError(RuntimeError):
    pass


class SoccerAIGuru:
    """Professional soccer bettor powered by LLM.
    
    Provides probability adjustments and confidence scores by synthesizing
    historical patterns, current form, and market sentiment.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 30.0,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._own_client = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._own_client:
            self._http.close()

    def __enter__(self) -> SoccerAIGuru:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def analyze_match(
        self,
        context: MatchContext,
        model_probability_home: float,
        model_probability_draw: float,
        model_probability_away: float,
    ) -> SoccerGuruAssessment:
        """Analyze a match using historical form, sentiment, and patterns.
        
        Args:
            context: Complete match context with form, h2h, sentiment
            model_probability_*: Baseline probabilities from statistical model
            
        Returns:
            Assessment with probability adjustments and confidence modifiers
        """
        prompt = self._build_prompt(
            context,
            model_probability_home,
            model_probability_draw,
            model_probability_away,
        )

        try:
            response = self._call_api(prompt)
            return self._parse_response(response)
        except Exception as e:
            _LOG.exception("Soccer AI guru analysis failed: %s", e)
            # Graceful fallback: return identity assessment
            return SoccerGuruAssessment(
                probability_adjustment_home=0.0,
                probability_adjustment_draw=0.0,
                probability_adjustment_away=0.0,
                confidence_multiplier=1.0,
                rationale="AI analysis skipped due to error; using baseline model",
                key_insights=[],
            )

    # ====================================================================
    # Internals
    # ====================================================================

    def _build_prompt(
        self,
        context: MatchContext,
        p_home: float,
        p_draw: float,
        p_away: float,
    ) -> str:
        """Construct a detailed prompt for professional soccer analysis."""
        h_form = context.home_team
        a_form = context.away_team

        prompt = f"""You are a world-class professional soccer bettor and analyst with 15+ years of experience.

MATCH CONTEXT:
- {h_form.team_name} (HOME) vs {a_form.team_name} (AWAY)
- Venue: {"Neutral" if context.venue_type == "neutral" else "Home advantage"}
- Days of rest: {h_form.team_id} = {context.days_rest_home} days, {a_form.team_id} = {context.days_rest_away} days

TEAM FORM (last 5 matches):
{h_form.team_name} (HOME):
  - Results: {' → '.join(context.home_team.last_5_results)}
  - Goals for/against: {h_form.last_5_goals_for:.2f}/{h_form.last_5_goals_against:.2f} per match
  - Elo: {h_form.elo_rating:.0f} | Attack: {h_form.attack_strength:+.2f} | Defense: {h_form.defense_strength:+.2f}
  - Win streak: {h_form.win_streak:+d}

{a_form.team_name} (AWAY):
  - Results: {' → '.join(context.away_team.last_5_results)}
  - Goals for/against: {a_form.last_5_goals_for:.2f}/{a_form.last_5_goals_against:.2f} per match
  - Elo: {a_form.elo_rating:.0f} | Attack: {a_form.attack_strength:+.2f} | Defense: {a_form.defense_strength:+.2f}
  - Win streak: {a_form.win_streak:+d}

HEAD-TO-HEAD HISTORY:
- Record: {context.h2h_record}
- Avg goals/match: Home {context.h2h_goals_home:.2f}, Away {context.h2h_goals_away:.2f}

MARKET SENTIMENT:
- Implied probabilities: {json.dumps(context.market_sentiment, indent=2)}
- Line movement: {context.recent_line_movement}

KEY ABSENCES:
- {h_form.team_name}: {', '.join(context.injuries_home) if context.injuries_home else 'None reported'}
- {a_form.team_name}: {', '.join(context.injuries_away) if context.injuries_away else 'None reported'}

BASELINE MODEL PROBABILITIES:
- Home win: {p_home * 100:.1f}%
- Draw: {p_draw * 100:.1f}%
- Away win: {p_away * 100:.1f}%

ANALYSIS TASK:
You are reviewing the baseline statistical model's predictions. Consider:

1. FORM MOMENTUM: Are winning/losing streaks significant? Is one team hitting peak form or falling apart?
2. TACTICAL MATCHUPS: How do attack/defense strength profiles clash? Is one team vulnerable to the other's style?
3. HEAD-TO-HEAD PATTERNS: Do historical matchups reveal tendencies the model might miss?
4. MARKET INEFFICIENCIES: Is public/sharp money signaling something the model didn't capture?
5. REST & RECOVERY: Is fatigue or recovery advantage creating edge?
6. INJURIES: Do key player absences materially shift win probability?

RESPONSE FORMAT:
Respond with ONLY valid JSON (no markdown, no explanation):
{{
  "home_adjustment": <float between -0.15 and +0.15>,
  "draw_adjustment": <float between -0.15 and +0.15>,
  "away_adjustment": <float between -0.15 and +0.15>,
  "confidence_multiplier": <float between 0.7 and 1.3>,
  "key_insights": [
    "<insight 1>",
    "<insight 2>",
    "<insight 3>"
  ],
  "rationale": "<2-3 sentence summary of your analysis>"
}}

Constraints:
- Adjustments sum to roughly 0 (you're shifting, not inflating)
- confidence_multiplier > 1.0 means you're more confident than baseline; < 1.0 means less confident
- Be conservative: small adjustments (±0.02-0.05) are often more reliable than large ones
- Favor the team with more recent wins, better goal differential, and edge in rest days
"""
        return prompt

    def _call_api(self, prompt: str) -> str:
        """Call OpenAI API with the prompt."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a professional soccer analyst. Respond with ONLY valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }
        if self.model.startswith("gpt-5"):
            # GPT-5 models count internal reasoning against the completion
            # budget, so small caps can produce an empty visible response.
            payload["max_completion_tokens"] = 1800
        else:
            payload["temperature"] = 0.7
            payload["max_tokens"] = 800
        response = self._http.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()
        if "choices" not in data or len(data["choices"]) == 0:
            raise SoccerAIGuruError(f"Unexpected API response: {data}")
        return data["choices"][0]["message"]["content"].strip()

    def _parse_response(self, response_text: str) -> SoccerGuruAssessment:
        """Parse JSON response from API."""
        try:
            # Try to extract JSON from markdown code blocks if present
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0].strip()

            data = json.loads(response_text)
            return SoccerGuruAssessment(
                probability_adjustment_home=float(data.get("home_adjustment", 0.0)),
                probability_adjustment_draw=float(data.get("draw_adjustment", 0.0)),
                probability_adjustment_away=float(data.get("away_adjustment", 0.0)),
                confidence_multiplier=float(data.get("confidence_multiplier", 1.0)),
                rationale=str(data.get("rationale", "")),
                key_insights=data.get("key_insights", []),
            )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise SoccerAIGuruError(f"Failed to parse AI response: {response_text}") from e


@dataclass(frozen=True)
class SoccerGuruAssessment:
    """AI assessment of a match."""
    probability_adjustment_home: float
    probability_adjustment_draw: float
    probability_adjustment_away: float
    confidence_multiplier: float
    rationale: str
    key_insights: List[str]
