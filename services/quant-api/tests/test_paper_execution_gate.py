"""Paper execution cross-module authorization contract tests."""
from dataclasses import replace

from osiris_api.paper_execution_gate import PaperOrderContext, authorize_paper_order


BASE = PaperOrderContext(
    quantity=2,
    price=100,
    current_position_notional=0,
    daily_pnl=0,
    observation_time_ms=1000,
    decision_time_ms=1200,
    model_approved=True,
    feed_authenticated=True,
    feed_entitled=True,
    kill_switch_active=False,
)


def test_approved_only_for_simulation():
    assert authorize_paper_order(BASE) == (True, "approved_for_simulation_only")


def test_rejects_missing_feed_authentication():
    assert authorize_paper_order(replace(BASE, feed_authenticated=False)) == (
        False, "feed_unauthenticated"
    )


def test_rejects_unentitled_feed():
    assert authorize_paper_order(replace(BASE, feed_entitled=False)) == (
        False, "feed_unentitled"
    )


def test_rejects_unapproved_model():
    assert authorize_paper_order(replace(BASE, model_approved=False)) == (
        False, "model_not_approved"
    )


def test_kill_switch_overrides_all_other_checks():
    assert authorize_paper_order(replace(BASE, kill_switch_active=True)) == (
        False, "kill_switch_active"
    )


def test_rejects_stale_or_future_observations():
    assert authorize_paper_order(replace(BASE, decision_time_ms=7000)) == (
        False, "stale_feed"
    )
    assert authorize_paper_order(replace(BASE, observation_time_ms=1201)) == (
        False, "future_observation"
    )


def test_rejects_invalid_timestamps():
    assert authorize_paper_order(replace(BASE, decision_time_ms=True)) == (
        False, "invalid_feed_timestamp"
    )


def test_delegates_to_existing_risk_limits():
    assert authorize_paper_order(replace(BASE, quantity=1000)) == (
        False, "max_order_notional_exceeded"
    )
