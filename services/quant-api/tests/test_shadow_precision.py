"""Exact sealed shadow evidence under caller-controlled Decimal contexts."""

from decimal import ROUND_DOWN, ROUND_UP, Context, Inexact, Rounded, localcontext
from fractions import Fraction

import pytest

from osiris_api.control_plane.database import Database
from osiris_api.control_plane.shadow import COMPARISONS, RUNS, ShadowService


@pytest.fixture
def shadow(tmp_path):
    database = Database(str(tmp_path / "precision.db"))
    return database, ShadowService(database)


def flat_ticks(price="1"):
    return [
        {"event_ms": 10, "price": price, "target": 0},
        {"event_ms": 20, "price": price, "target": 0},
    ]


def paper_request(run, equities=None):
    return {
        "run_id": run["run_id"],
        "comparison_as_of_ms": run["event_times_ms"][-1] + 1,
        "paper_observations": [
            {
                "event_ms": stamp,
                "available_ms": stamp + 1,
                "equity": equity,
                "source_sha256": f"{index:064x}",
            }
            for index, (stamp, equity) in enumerate(
                zip(run["event_times_ms"], run["equity"] if equities is None else equities)
            )
        ],
    }


def context_state(context):
    return (
        context.prec,
        context.rounding,
        context.Emin,
        context.Emax,
        context.capitals,
        context.clamp,
        dict(context.traps),
        dict(context.flags),
    )


def test_flat_run_retains_all_initial_cash_digits_through_restart_and_replay(shadow):
    database, service = shadow
    cash = "1.123456789012345678901234567890123456789"
    result = service.run("flat-precise", flat_ticks(), initial_cash=cash)
    assert result["initial_equity"] == cash
    assert result["configuration"]["initial_cash"] == cash
    assert result["cash"] == cash
    assert result["equity"] == [cash, cash]
    assert result["final_equity"] == cash
    assert result["pnl"] == "0"
    assert result["fills"] == []
    assert result["policy"].endswith("-v2")
    restarted = ShadowService(Database(database.path))
    assert restarted.get_run("flat-precise") == result
    assert restarted.run("flat-precise", flat_ticks(), initial_cash=cash) == result


@pytest.mark.parametrize(
    "field", ["price", "initial_cash", "max_order_amount", "daily_loss_limit", "fee_bps"]
)
def test_trailing_significant_digits_conflict_but_equivalent_zeroes_replay(shadow, field):
    _, service = shadow
    first = "1.00000000000000000000000000001"
    second = "1.00000000000000000000000000002"
    ticks = flat_ticks(first if field == "price" else "1")
    options = {} if field == "price" else {field: first}
    result = service.run("exact-fingerprint", ticks, **options)
    equivalent = flat_ticks(first + "0") if field == "price" else ticks
    equivalent_options = {} if field == "price" else {field: first + "0"}
    assert service.run("exact-fingerprint", equivalent, **equivalent_options) == result
    changed = flat_ticks(second) if field == "price" else ticks
    changed_options = {} if field == "price" else {field: second}
    with pytest.raises(ValueError, match="^shadow_run_id_conflict$"):
        service.run("exact-fingerprint", changed, **changed_options)


def test_run_and_comparison_are_independent_of_precision_rounding_and_traps(tmp_path):
    ticks = [
        {"event_ms": 10, "price": "7.123456789", "target": 1},
        {"event_ms": 20, "price": "7.234567891", "target": 1},
        {"event_ms": 30, "price": "6.987654321", "target": 0},
        {"event_ms": 40, "price": "7.345678912", "target": 0},
    ]
    outcomes = []
    for precision, rounding, traps in [(6, ROUND_UP, True), (50, ROUND_DOWN, False)]:
        database = Database(str(tmp_path / f"context-{precision}.db"))
        service = ShadowService(database)
        context = Context(prec=precision, rounding=rounding)
        context.capitals = 0 if precision == 6 else 1
        context.Emin, context.Emax = -9, 9
        context.traps[Inexact] = context.traps[Rounded] = traps
        context.flags[Inexact] = True
        with localcontext(context) as active:
            before = context_state(active)
            options = {
                "initial_cash": "1000.123456789012345678901234567890123456789",
                "fee_bps": "1.3",
                "daily_loss_limit": "1e-1000",
            }
            run = service.run("context-run", ticks, **options)
            assert service.get_run("context-run") == run
            assert service.run("context-run", ticks, **options) == run
            legacy = service.compare("context-run", run["equity"])
            request = paper_request(run)
            created = service.create_comparison("context-comparison", "alice", request)
            assert created["replayed"] is False
            assert created["comparison"]["policy"].endswith("-v2")
            assert service.get_comparison("context-comparison") == created["comparison"]
            assert service.create_comparison("context-comparison", "alice", request) == {
                **created,
                "replayed": True,
            }
            assert context_state(active) == before
        outcomes.append((run, legacy, created["comparison"]))
    assert outcomes[0] == outcomes[1]


def test_tiny_comparison_difference_is_exact_persistent_and_replayable(shadow):
    database, service = shadow
    initial = "1.000000000000000000000000000001"
    final_paper = "1.000000000000000000000000000002"
    run = service.run("tiny-difference", flat_ticks(), initial_cash=initial)
    request = paper_request(run, [initial, final_paper])
    created = service.create_comparison("tiny-comparison", "alice", request)
    comparison = created["comparison"]
    unit = Fraction(1, 10**30)
    assert Fraction(comparison["rows"][-1]["difference"]) == -unit
    assert Fraction(comparison["final_difference"]) == -unit
    assert Fraction(comparison["max_absolute_difference"]) == unit
    assert Fraction(comparison["paper_pnl"]) == unit
    assert Fraction(comparison["shadow_pnl"]) == 0
    legacy = service.compare("tiny-difference", [initial, final_paper])
    assert Fraction(legacy["equity_differences"][-1]) == -unit
    restarted = ShadowService(Database(database.path))
    assert restarted.get_comparison("tiny-comparison") == comparison
    assert restarted.create_comparison("tiny-comparison", "alice", request) == {
        **created,
        "replayed": True,
    }


def test_representable_extreme_exponent_configuration_can_be_read_and_replayed(shadow):
    database, service = shadow
    run = service.run("small-stop", flat_ticks(), daily_loss_limit="1e-1000")
    assert Fraction(run["configuration"]["daily_loss_limit"]) == Fraction(1, 10**1000)
    restarted = ShadowService(Database(database.path))
    assert restarted.get_run("small-stop") == run
    assert restarted.run("small-stop", flat_ticks(), daily_loss_limit="0.1e-999") == run


@pytest.mark.parametrize("price,fee_bps,cap", [("3", "10", "2"), ("6", "0", "1")])
def test_repeating_allocation_cannot_spend_cash_or_exceed_cap(shadow, price, fee_bps, cap):
    _, service = shadow
    ticks = [
        {"event_ms": 10, "price": price, "target": 1},
        {"event_ms": 20, "price": price, "target": 1},
    ]
    run = service.run(
        "bounded-allocation", ticks, initial_cash="1", max_order_amount=cap, fee_bps=fee_bps
    )
    assert len(run["fills"]) == 1
    cash = Fraction(1)
    fees = Fraction(0)
    for fill in run["fills"]:
        quantity, execution_price, fee = map(
            Fraction, (fill["quantity"], fill["price"], fill["fee"])
        )
        assert fee == abs(quantity * execution_price) * Fraction(fee_bps) / 10000
        cash -= quantity * execution_price + fee
        fees += fee
        assert cash >= 0
    assert Fraction(run["cash"]) == cash
    assert Fraction(run["fees"]) == fees
    assert Fraction(run["quantity"]) * Fraction(price) <= Fraction(cap)
    assert Fraction(run["equity"][-1]) == cash + Fraction(run["quantity"]) * Fraction(price)
    assert service.get_run("bounded-allocation") == run


def test_unsupported_generated_equity_is_rejected_before_persistence(shadow):
    database, service = shadow
    ticks = [
        {"event_ms": 10, "price": "1", "target": 1},
        {"event_ms": 20, "price": "1", "target": 1},
        {"event_ms": 30, "price": "2", "target": 1},
    ]
    with pytest.raises(ValueError):
        service.run(
            "equity-overflow",
            ticks,
            initial_cash="1e15",
            max_order_amount="1e15",
            daily_loss_limit="1e15",
            fee_bps="0",
        )
    assert database.list(RUNS) == []
    assert database.list(COMPARISONS) == []


def test_unsupported_generated_comparison_difference_is_not_persisted(shadow):
    database, service = shadow
    run = service.run("comparison-bound", flat_ticks(), initial_cash="1e15")
    with pytest.raises(ValueError):
        service.create_comparison(
            "difference-overflow",
            "alice",
            paper_request(run, ["1e15", "-1e15"]),
        )
    assert len(database.list(RUNS)) == 1
    assert database.list(COMPARISONS) == []


def test_unsupported_fill_quantity_is_rejected_even_when_run_finishes_flat(shadow):
    database, service = shadow
    ticks = [
        {"event_ms": 10, "price": "1e-1000", "target": 1},
        {"event_ms": 20, "price": "1e-1000", "target": 0},
        {"event_ms": 30, "price": "1e-1000", "target": 0},
    ]
    with pytest.raises(ValueError, match="^invalid_shadow_run$"):
        service.run("quantity-overflow", ticks, initial_cash="1", max_order_amount="1", fee_bps="0")
    assert database.list(RUNS) == []
