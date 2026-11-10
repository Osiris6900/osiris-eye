"""Paper-intent forwarding contract must fail closed (T16).

The helper is not a cryptographic signature verifier. These regressions
exercise the local paper-only boundary and persisted kill-switch recheck.
"""
from copy import deepcopy

import pytest

from osiris_api.paper_execution import (
    initialize_paper_account,
    set_kill_switch,
    snapshot,
    submit_approved_workflow,
)
from osiris_api.paper_workflow import evaluate_paper_workflow

NOW = "2026-11-08T16:00:00+00:00"
FRESH = "2026-11-08T15:59:30+00:00"


@pytest.fixture
def paper_db(tmp_path, monkeypatch):
    path = tmp_path / "bound-paper-execution.sqlite3"
    monkeypatch.setenv("OSIRIS_PAPER_EXECUTION_DB", str(path))
    monkeypatch.delenv("OSIRIS_ENABLE_LIVE_EXECUTION", raising=False)
    return path


def approved():
    result = evaluate_paper_workflow(
        symbol="btc", signals=[0.0, 1.0], asset_returns=[0.0, 0.01],
        quantity=1, price=100,
        kill_switch_active=False, market_data_fresh=True,
        risk_service_available=True, current_gross_exposure=0.0,
    )
    assert result["risk"]["approved_for_paper"] is True
    assert result["risk"]["limits_evaluated"] is True
    return result


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflow", "untrusted"),
        ("execution.mode", "live"),
        ("risk.limits_evaluated", None),
        ("risk.limits_evaluated", False),
        ("risk.limits_evaluated", 1),
        ("risk.live_execution", None),
        ("risk.reason", "approved_for_simulation_only_bypass"),
    ],
)
def test_missing_or_legacy_approval_evidence_blocks_without_db(
    paper_db, field, value,
):
    result = deepcopy(approved())
    if "." in field:
        key, leaf = field.split(".", 1)
        result[key][leaf] = value
    else:
        result[field] = value
    forwarded = submit_approved_workflow(
        result, client_order_id="not-forwarded", now=NOW, observed_at=FRESH,
    )
    assert forwarded["status"] == "not_submitted"
    assert forwarded["reason"] == "approval_evidence_missing"
    assert forwarded["broker_order_submitted"] is False
    assert forwarded["live_execution"] is False
    assert not paper_db.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbol": "ETH"},
        {"side": "SELL"},
        {"quantity": "2"},
        {"price": "200"},
        {"quantity": True},
        {"price": float("nan")},
        {"side": None},
    ],
)
def test_order_mutation_cannot_reuse_an_approval(paper_db, overrides):
    result = submit_approved_workflow(
        approved(), client_order_id="mutated",
        now=NOW, observed_at=FRESH, **overrides,
    )
    assert result["status"] == "not_submitted"
    assert result["reason"] == "paper_intent_mismatch"
    assert result["fill"] is None
    assert result["live_execution"] is False
    assert not paper_db.exists()


def test_semantically_identical_paper_order_can_fill_on_ledger(paper_db):
    initialize_paper_account("1000")
    forwarded = submit_approved_workflow(
        approved(), client_order_id="bound-order", symbol="btc",
        side="buy", quantity="1.00", price="100.000",
        now=NOW, observed_at=FRESH,
    )
    assert forwarded["status"] == "filled"
    assert forwarded["broker_order_submitted"] is False
    assert forwarded["live_execution"] is False
    assert snapshot()["fill_count"] == 1


def test_ledger_kill_switch_rejects_prior_approval_at_submission(paper_db):
    initialize_paper_account("1000")
    result = approved()
    set_kill_switch(True)
    forwarded = submit_approved_workflow(
        result, client_order_id="after-kill", now=NOW, observed_at=FRESH,
    )
    assert forwarded["status"] == "risk_rejected"
    assert forwarded["reason"] == "kill_switch_active"
    assert snapshot()["fill_count"] == 0


def test_live_execution_override_is_rejected(paper_db):
    initialize_paper_account("1000")
    with pytest.raises(ValueError, match="live_execution_disabled"):
        submit_approved_workflow(
            approved(), client_order_id="live-refused",
            live_execution=True, now=NOW, observed_at=FRESH,
        )
    assert snapshot()["fill_count"] == 0
