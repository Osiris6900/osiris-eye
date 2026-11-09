from dataclasses import replace

from osiris_api.market_feed_gate import MarketObservation, validate_market_observation


BASE = MarketObservation("BTCUSD", 100.0, 1000, 1100, True, True, "approved-vendor")


def test_accepts_valid_observation_for_research_only():
    assert validate_market_observation(BASE, decision_time_ms=1200) == (
        True, "accepted_for_research_only"
    )


def test_rejects_untrusted_and_unentitled():
    assert validate_market_observation(replace(BASE, authenticated=False), decision_time_ms=1200)[0] is False
    assert validate_market_observation(replace(BASE, entitled=False), decision_time_ms=1200)[0] is False


def test_rejects_stale_future_and_out_of_order():
    assert validate_market_observation(BASE, decision_time_ms=7000)[1] == "stale_observation"
    assert validate_market_observation(replace(BASE, received_time_ms=1300), decision_time_ms=1200)[1] == "future_receipt"
    assert validate_market_observation(BASE, decision_time_ms=1200, previous_event_time_ms=1000)[1] == "out_of_order_observation"


def test_rejects_bad_price_and_missing_source():
    assert validate_market_observation(replace(BASE, price=float("nan")), decision_time_ms=1200)[1] == "invalid_price"
    assert validate_market_observation(replace(BASE, source=" "), decision_time_ms=1200)[1] == "missing_instrument_or_source"


def test_rejects_inconsistent_event_receipt_and_invalid_clock():
    assert validate_market_observation(replace(BASE, event_time_ms=1200), decision_time_ms=1200)[1] == "event_after_receipt"
    assert validate_market_observation(BASE, decision_time_ms=True)[1] == "invalid_timestamp"
