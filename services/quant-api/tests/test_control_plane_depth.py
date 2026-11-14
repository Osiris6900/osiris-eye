"""Depth evidence must stay atomic, exact and bound to its actual source."""

import copy
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Inexact, localcontext
from fractions import Fraction

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from osiris_api.control_plane.api import router
from osiris_api.control_plane.database import Database, digest
from osiris_api.control_plane.depth import MAX_RAW_DEPTH_LEVELS, DepthService


class SnapshotTransport:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.calls = []

    def request(self, method, url):
        self.calls.append((method, url))
        return copy.deepcopy(self.snapshot)


@pytest.fixture
def depth(tmp_path):
    db = Database(str(tmp_path / "depth.db"))
    now = [100.0]
    transport = SnapshotTransport(
        {
            "sequence": 1,
            "bids": [["100", "2", 1], ["99", "1", 2]],
            "asks": [["101", "1", 1], ["102", "1", 2]],
        }
    )
    service = DepthService(
        db,
        entitlement_reference="fixture-only",
        transport=transport,
        clock=lambda: now[0],
    )
    return service, db, now, transport


def test_failed_history_write_rolls_back_latest_and_can_retry(depth):
    service, db, now, transport = depth
    original = service.capture("BTC-USD")
    with db.transaction() as conn:
        conn.execute("""CREATE TRIGGER fail_depth_history BEFORE INSERT ON cp_documents
            WHEN NEW.collection='depth-history'
            BEGIN SELECT RAISE(ABORT,'fixture_history_failure'); END""")
    transport.snapshot["sequence"] = 2
    now[0] = 101
    with pytest.raises(sqlite3.IntegrityError, match="fixture_history_failure"):
        service.capture("BTC-USD")
    assert db.get("depth", "BTC-USD") == {"version": 1, "value": original}
    assert [row["value"] for row in db.list("depth-history")] == [original]
    assert db.get("depth-clock", "BTC-USD")["value"]["observed_ms"] == 101000
    with db.transaction() as conn:
        conn.execute("DROP TRIGGER fail_depth_history")
    recovered = service.capture("BTC-USD")
    assert recovered["sequence"] == 2
    assert db.get("depth", "BTC-USD")["version"] == 2
    assert db.get("depth-history", recovered["snapshot_id"])["value"] == recovered


def test_conflicting_history_never_replaces_latest(depth):
    service, db, _, transport = depth
    original = service.capture("BTC-USD")
    key = "book:" + digest({"instrument": "BTC-USD", "sequence": 2})
    db.put("depth-history", key, {"conflicting": "fixture"})
    transport.snapshot["sequence"] = 2
    with pytest.raises(ValueError, match="depth_history_conflict"):
        service.capture("BTC-USD")
    assert db.get("depth", "BTC-USD")["value"] == original
    assert db.get("depth-history", key)["value"] == {"conflicting": "fixture"}


def test_valid_maximum_identifier_has_complete_history(depth):
    service, db, _, _ = depth
    instrument = "A" * 128
    book = service.capture(instrument)
    assert len(book["snapshot_id"]) <= 128
    assert db.get("depth-history", book["snapshot_id"])["value"] == book
    assert service.impact(instrument, "buy", "1")["filled_quantity"] == "1"


def test_concurrent_captures_preserve_latest_sequence_and_immutable_history(depth):
    service, db, _, transport = depth
    service.capture("BTC-USD")
    barrier = threading.Barrier(2)

    class ConcurrentTransport(SnapshotTransport):
        def request(self, method, url):
            result = super().request(method, url)
            barrier.wait(timeout=5)
            return result

    def capture(sequence):
        snapshot = {**transport.snapshot, "sequence": sequence}
        worker = DepthService(
            Database(db.path),
            entitlement_reference="fixture-only",
            transport=ConcurrentTransport(snapshot),
            clock=lambda: 100,
        )
        try:
            return worker.capture("BTC-USD")["sequence"]
        except ValueError as exc:
            assert str(exc) == "stale_or_replayed_depth"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(capture, [2, 3]))
    assert results[1] == 3
    latest = db.get("depth", "BTC-USD")["value"]
    assert latest["sequence"] == 3
    assert db.get("depth-history", latest["snapshot_id"])["value"] == latest
    history = db.list("depth-history")
    assert sorted(row["value"]["sequence"] for row in history) == sorted(
        [1] + [sequence for sequence in results if sequence is not None]
    )
    assert all(row["version"] == 1 for row in history)


@pytest.mark.parametrize("precision", [1, 4, 28, 100])
def test_sweep_preserves_small_quantities_and_costs_under_any_decimal_context(depth, precision):
    service, _, _, transport = depth
    first_size = "0.123456789012345678901234567890123456789"
    second_size = "0.000000000000000000000000000000000000001"
    first_price = "1.111111111111111111111111111111111111111"
    second_price = "1.222222222222222222222222222222222222222"
    quantity = "0.123456789012345678901234567890123456790"
    transport.snapshot["bids"] = [["1", "1", 1]]
    transport.snapshot["asks"] = [[second_price, second_size, 1], [first_price, first_size, 1]]
    with localcontext() as context:
        context.prec = precision
        context.traps[Inexact] = True
        service.capture("BTC-USD")
        result = service.impact("BTC-USD", "buy", quantity)
    expected = Fraction(first_size) * Fraction(first_price) + Fraction(second_size) * Fraction(
        second_price
    )
    assert Fraction(result["filled_notional"]) == expected
    assert Fraction(result["filled_quantity"]) == Fraction(quantity)
    assert result["unfilled_quantity"] == "0"
    assert [Fraction(row["quantity"]) for row in result["fills"]] == [
        Fraction(first_size),
        Fraction(second_size),
    ]
    ratio = result["average_price_exact_ratio"]
    assert Fraction(int(ratio["numerator"]), int(ratio["denominator"])) == expected / Fraction(
        quantity
    )
    assert Fraction(result["shortfall_from_midpoint"]) == (
        Fraction(result["filled_notional"])
        - Fraction(result["filled_quantity"]) * Fraction(result["reference_midpoint"])
    )


def test_minimum_supported_exponents_remain_nonzero(depth):
    service, _, _, transport = depth
    transport.snapshot["bids"] = [["1e-1000", "1e-1000", 1]]
    transport.snapshot["asks"] = [["2e-1000", "1e-1000", 1]]
    service.capture("BTC-USD")
    result = service.impact("BTC-USD", "buy", "1e-1000")
    assert Fraction(result["filled_notional"]) == Fraction(2, 10**2000)
    assert Fraction(result["spread_cost"]) == Fraction(1, 2 * 10**2000)
    assert result["unfilled_quantity"] == "0"
    assert result["average_price"] == "2E-1000"
    assert result["average_price_is_rounded"] is False


def test_repeating_average_discloses_display_rounding_and_exact_ratio(depth):
    service, _, _, transport = depth
    transport.snapshot["bids"] = [["0.5", "1", 1]]
    transport.snapshot["asks"] = [["1", "2", 1], ["2", "1", 1]]
    service.capture("BTC-USD")
    result = service.impact("BTC-USD", "buy", "3")
    assert result["average_price"] == "1.333333333333333333333333333"
    assert result["average_price_is_rounded"] is True
    assert result["average_price_exact_ratio"] == {"numerator": "4", "denominator": "3"}
    assert result["filled_notional"] == "4"


@pytest.mark.parametrize(
    "side,limit,filled,unfilled,notional,spread,depth_cost",
    [
        ("buy", None, "2", "1", "203", "1", "1"),
        ("sell", None, "3", "0", "299", "1.5", "1"),
        ("buy", "101", "1", "2", "101", "0.5", "0"),
        ("sell", "100", "2", "1", "200", "1", "0"),
        ("buy", "100", "0", "3", "0", "0", "0"),
        ("sell", "101", "0", "3", "0", "0", "0"),
    ],
)
def test_limits_partial_liquidity_and_spread_depth_decomposition(
    depth,
    side,
    limit,
    filled,
    unfilled,
    notional,
    spread,
    depth_cost,
):
    service, db, _, transport = depth
    book = service.capture("BTC-USD")
    result = service.impact("BTC-USD", side, "3", limit_price=limit)
    assert result["filled_quantity"] == filled
    assert result["unfilled_quantity"] == unfilled
    assert result["filled_notional"] == notional
    assert result["reference_midpoint"] == "100.5"
    assert result["spread_cost"] == spread
    assert result["visible_depth_cost"] == depth_cost
    assert Fraction(result["shortfall_from_midpoint"]) == Fraction(spread) + Fraction(depth_cost)
    assert (result["average_price"] is None) == (filled == "0")
    assert (result["average_price_exact_ratio"] is None) == (filled == "0")
    assert result["snapshot_sha256"] == digest(book)
    assert result["raw_sha256"] == digest(transport.snapshot)
    assert result["entitlement_reference"] == "fixture-only"
    assert result["source_url"] == transport.calls[0][1]
    assert result["age_ms"] == 0
    assert result["age_basis"] == "local-response-receipt"
    assert result["execution_authorized"] is False
    assert result["model"] == "static-displayed-book-sweep"
    # Estimates never consume their book or create canonical paper fills.
    assert db.get("depth", "BTC-USD") == {"version": 1, "value": book}
    assert service.impact("BTC-USD", side, "3", limit_price=limit) == result


def test_retained_depth_truncation_is_reported_without_extrapolation(depth):
    service, _, _, transport = depth
    transport.snapshot["asks"] = [[str(price), "1", 1] for price in range(1101, 100, -1)]
    book = service.capture("BTC-USD")
    result = service.impact("BTC-USD", "buy", "1001")
    assert len(book["asks"]) == len(result["fills"]) == 1000
    assert result["retained_depth_truncated"] is True
    assert result["filled_quantity"] == "1000"
    assert result["unfilled_quantity"] == "1"
    assert result["fills"][-1]["price"] == "1100"


@pytest.mark.parametrize("invalid_tail,size", [(False, "1"), (True, "1"), (False, "0.123456789012345678901234567890")])
def test_realistic_full_l2_size_validates_tail_before_retaining_best_levels(depth, invalid_tail, size):
    service, db, _, transport = depth
    # A genuine retained BTC capture had 23,482 ask levels within 992,068 bytes.
    transport.snapshot["asks"] = [[str(price), size, 1] for price in range(101, 23583)]
    if invalid_tail:
        transport.snapshot["asks"][-1][0] = "-1"
        with pytest.raises(ValueError):
            service.capture("BTC-USD")
        assert db.get("depth", "BTC-USD") is None
    else:
        book = service.capture("BTC-USD")
        assert book["asks_truncated"] is True
        assert len(book["asks"]) == 1000
        assert book["asks"][0] == ["101", size]
        assert book["asks"][-1] == ["1100", size]
        source_body = json.dumps(transport.snapshot, sort_keys=True, separators=(",", ":")).encode()
        assert book["raw_sha256"] == hashlib.sha256(source_body).hexdigest()
        if size != "1":
            assert len(source_body) > 1_048_576


def test_raw_l2_size_remains_bounded(depth):
    service, db, _, transport = depth
    transport.snapshot["asks"] = [["101", "1", 1]] * (MAX_RAW_DEPTH_LEVELS + 1)
    with pytest.raises(ValueError, match="invalid_depth_levels"):
        service.capture("BTC-USD")
    assert db.get("depth", "BTC-USD") is None


def test_raw_l2_byte_limit_applies_before_any_snapshot_is_admitted(depth):
    service, db, _, transport = depth
    size = "0." + "1" * 60
    transport.snapshot["asks"] = [[str(price), size, 1] for price in range(101, 30101)]
    with pytest.raises(ValueError, match="depth_raw_response_too_large"):
        service.capture("BTC-USD")
    assert db.get("depth", "BTC-USD") is None
    assert db.list("depth-history") == []


def test_expired_refusal_persists_clock_and_rollback_cannot_revive_snapshot(depth):
    service, db, now, _ = depth
    book = service.capture("BTC-USD")
    now[0] = 105
    assert service.impact("BTC-USD", "buy", "1")["age_ms"] == 5000
    now[0] = 105.001
    with pytest.raises(ValueError, match="stale_depth"):
        service.impact("BTC-USD", "buy", "1")
    restarted = DepthService(
        Database(db.path), entitlement_reference="fixture-only", clock=lambda: 101
    )
    with pytest.raises(ValueError, match="depth_clock_rollback"):
        restarted.impact("BTC-USD", "buy", "1")
    assert db.get("depth-clock", "BTC-USD")["value"]["observed_ms"] == 105001
    assert db.get("depth", "BTC-USD")["value"] == book


def test_new_sequence_cannot_bypass_capture_clock_rollback(depth):
    service, db, now, transport = depth
    original = service.capture("BTC-USD")
    now[0] = 99
    transport.snapshot["sequence"] = 2
    with pytest.raises(ValueError, match="depth_clock_rollback"):
        service.capture("BTC-USD")
    assert db.get("depth", "BTC-USD")["value"] == original


def test_clock_is_rechecked_after_waiting_for_database_lock(depth):
    service, _, _, _ = depth
    ticks = iter([100, 100, 99])
    service.clock = lambda: next(ticks)
    with pytest.raises(ValueError, match="depth_clock_rollback"):
        service.capture("BTC-USD")


@pytest.mark.parametrize("clock", [None, True, "100", -1, float("inf"), float("nan"), 10**400])
def test_invalid_clock_is_refused_before_transport(depth, clock):
    service, db, _, transport = depth
    service.clock = lambda: clock
    with pytest.raises(ValueError, match="invalid_depth_clock"):
        service.capture("BTC-USD")
    assert transport.calls == []
    assert db.get("depth", "BTC-USD") is None


@pytest.mark.parametrize("reference", [None, True, "", "   ", "x" * 513, "fixture\nsecret"])
def test_invalid_entitlement_reference_is_refused(depth, reference):
    _, db, _, _ = depth
    with pytest.raises(ValueError, match="entitlement_reference_required"):
        DepthService(db, entitlement_reference=reference)


def test_changed_entitlement_requires_fresh_capture(depth):
    service, db, _, transport = depth
    service.capture("BTC-USD")
    changed = DepthService(
        db,
        entitlement_reference="different-fixture",
        transport=transport,
        clock=lambda: 100,
    )
    with pytest.raises(ValueError, match="depth_entitlement_mismatch"):
        changed.impact("BTC-USD", "buy", "1")
    transport.snapshot["sequence"] = 2
    changed.capture("BTC-USD")
    assert changed.impact("BTC-USD", "buy", "1")["entitlement_reference"] == "different-fixture"


@pytest.mark.parametrize("missing", [False, True])
def test_incomplete_or_changed_history_is_never_used(depth, missing):
    service, db, _, _ = depth
    book = service.capture("BTC-USD")
    with db.transaction() as conn:
        if missing:
            conn.execute("DELETE FROM cp_documents WHERE collection='depth-history'")
        else:
            altered = {**book, "captured_ms": 99999}
            conn.execute(
                "UPDATE cp_documents SET body=? WHERE collection='depth-history'",
                (json.dumps(altered),),
            )
    with pytest.raises(ValueError, match="depth_history_mismatch"):
        service.impact("BTC-USD", "buy", "1")


def test_legacy_snapshot_requires_recapture_and_is_not_relabelled(depth):
    service, db, _, transport = depth
    legacy = {**transport.snapshot, "captured_ms": 100000}
    db.put("depth", "BTC-USD", legacy)
    with pytest.raises(ValueError, match="depth_recapture_required"):
        service.impact("BTC-USD", "buy", "1")
    assert db.get("depth", "BTC-USD")["value"] == legacy
    transport.snapshot["sequence"] = 2
    service.capture("BTC-USD")
    assert service.impact("BTC-USD", "buy", "1")["sequence"] == 2


@pytest.mark.parametrize("sequence", [True, -1, 2**63, "2", None])
def test_invalid_sequence_leaves_no_snapshot(depth, sequence):
    service, db, _, transport = depth
    transport.snapshot["sequence"] = sequence
    with pytest.raises(ValueError, match="invalid_depth_sequence"):
        service.capture("BTC-USD")
    assert db.get("depth", "BTC-USD") is None


@pytest.mark.parametrize("count", [True, 0, -1, 2**63, "1"])
def test_malformed_order_count_leaves_no_snapshot(depth, count):
    service, db, _, transport = depth
    transport.snapshot["asks"][0][2] = count
    with pytest.raises(ValueError, match="invalid_depth_level"):
        service.capture("BTC-USD")
    assert db.get("depth", "BTC-USD") is None


@pytest.fixture
def api(depth, monkeypatch):
    service, db, _, _ = depth
    # Route defaults use real UTC. Bind only this fixture service to test time.
    import osiris_api.control_plane.depth as module

    monkeypatch.setenv("OSIRIS_CONTROL_DB", db.path)
    monkeypatch.setenv(
        "OSIRIS_ADMIN_TOKENS", json.dumps({"analyst": "test-analyst", "viewer": "test-viewer"})
    )
    monkeypatch.setenv("OSIRIS_L2_ENTITLEMENT_REFERENCE", "fixture-only")
    service.capture("BTC-USD")
    monkeypatch.setattr(module, "DepthService", lambda *args, **kwargs: service)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_http_limit_reaches_actual_sweep(api):
    response = api.post(
        "/control-plane/depth/BTC-USD/impact",
        headers={"Authorization": "Bearer test-analyst"},
        json={"side": "buy", "quantity": "3", "limit_price": "101"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["filled_quantity"] == "1"
    assert response.json()["unfilled_quantity"] == "2"
    assert response.json()["execution_authorized"] is False


@pytest.mark.parametrize(
    "payload",
    [
        {"side": [], "quantity": "1"},
        {"side": "buy", "quantity": "0"},
        {"side": "buy", "quantity": "-1"},
        {"side": "buy", "quantity": "1e-1001"},
        {"side": "buy", "quantity": "1", "limit_price": []},
        {"side": "buy", "quantity": "1", "limit_price": "NaN"},
    ],
)
def test_http_bad_economics_are_validation_errors(api, payload):
    response = api.post(
        "/control-plane/depth/BTC-USD/impact",
        headers={"Authorization": "Bearer test-analyst"},
        json=payload,
    )
    assert response.status_code == 422


@pytest.mark.parametrize("token,status", [(None, 401), ("test-viewer", 403)])
def test_http_impact_still_requires_research_permission(api, token, status):
    headers = {} if token is None else {"Authorization": "Bearer " + token}
    response = api.post(
        "/control-plane/depth/BTC-USD/impact",
        headers=headers,
        json={"side": "buy", "quantity": "1"},
    )
    assert response.status_code == status


def test_http_capture_still_requires_mfa(api, depth):
    response = api.post(
        "/control-plane/depth/BTC-USD/capture",
        headers={"Authorization": "Bearer test-analyst"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "mfa_session_required"
    assert len(depth[3].calls) == 1
