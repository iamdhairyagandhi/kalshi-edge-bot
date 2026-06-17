from src.strategies.market_filters import (
    category_max_entry_premium,
    classify_market,
    market_filter,
    market_preference_score,
)


def test_market_universe_prefers_modelable_categories():
    assert classify_market("Will CPI inflation be above 3%?") == "macro"
    assert classify_market("Will Bitcoin hit $100k?") == "crypto"
    assert classify_market("Will NYC temperature exceed 90F?") == "weather"
    assert classify_market("Will Company X beat earnings?") == "business"
    assert classify_market("Libema Open: Player A vs Player B") == "sports"

    assert market_preference_score("Will CPI inflation be above 3%?") > market_preference_score(
        "Will random viral topic happen?"
    )


def test_market_filter_blocks_sports_live_and_099_chases():
    sports = market_filter("NBA: Knicks vs Celtics", live_price=0.55)
    assert sports.blocked
    assert sports.reason == "category_sports"

    live = market_filter("Will candidate win live debate market?", live_price=0.40)
    assert live.blocked
    assert live.reason == "live_event"

    chase = market_filter("Will CPI inflation be above 3%?", live_price=0.99)
    assert chase.blocked
    assert chase.reason == "chase_0_99"


def test_category_premium_limit_is_stricter_for_unknown_than_crypto():
    assert category_max_entry_premium("other", 0.03) < category_max_entry_premium("crypto", 0.03)
