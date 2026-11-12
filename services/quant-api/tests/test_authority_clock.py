"""Expired authority stays expired after clock changes, refusal and restart."""

from contextlib import contextmanager

import pytest

from osiris_api.control_plane.coordination import Coordinator
from osiris_api.control_plane.database import Database


@pytest.fixture
def authority(tmp_path):
    now = [100.0]
    db = Database(str(tmp_path / "authority.db"))
    return db, now, Coordinator(db, clock=lambda: now[0])


@pytest.mark.parametrize("operation", ["acquire", "renew", "write", "claim", "finish"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, True, "100", 10**400])
def test_invalid_time_cannot_grant_or_exercise_authority(authority, operation, bad):
    db, now, coordinator = authority
    lease = coordinator.acquire("ledger", "owner")
    coordinator.submit("job", "backtest", {})
    claim = coordinator.claim("worker", ["backtest"])
    now[0] = bad
    calls = {
        "acquire": lambda: coordinator.acquire("other", "owner"),
        "renew": lambda: coordinator.renew("ledger", "owner", lease["epoch"]),
        "write": lambda: coordinator.write("ledger", "owner", lease["epoch"], "cash", {}),
        "claim": lambda: coordinator.claim("other", ["backtest"]),
        "finish": lambda: coordinator.finish("job", "worker", claim["claim_token"], {}),
    }
    with pytest.raises(ValueError, match="invalid_authority_clock"):
        calls[operation]()
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cp_writes").fetchone()[0] == 0
        assert conn.execute("SELECT status FROM cp_jobs").fetchone()[0] == "running"


@pytest.mark.parametrize("operation", ["write", "renew"])
def test_refused_expired_lease_never_revives_across_restart(authority, operation):
    db, now, coordinator = authority
    lease = coordinator.acquire("ledger", "owner", ttl=1)
    now[0] = 102

    def exercise(service):
        if operation == "write":
            service.write("ledger", "owner", lease["epoch"], "cash", {"amount": "999"})
        else:
            service.renew("ledger", "owner", lease["epoch"])

    with pytest.raises(ValueError, match="stale_or_foreign_lease"):
        exercise(coordinator)
    assert db.get("authority-clock", "leases")["value"]["observed_at"] == 102
    now[0] = 100.5
    restarted = Coordinator(Database(db.path), clock=lambda: now[0])
    with pytest.raises(ValueError, match="authority_clock_rollback"):
        exercise(restarted)
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cp_writes").fetchone()[0] == 0
        assert conn.execute("SELECT expires FROM cp_leases").fetchone()[0] == 101
    now[0] = 102
    replacement = restarted.acquire("ledger", "new-owner")
    assert replacement["epoch"] == lease["epoch"] + 1


def test_refused_expired_job_never_revives_across_restart(authority):
    db, now, coordinator = authority
    coordinator.submit("job", "backtest", {})
    claim = coordinator.claim("worker", ["backtest"], ttl=1)
    now[0] = 102
    with pytest.raises(ValueError, match="stale_or_foreign_job_claim"):
        coordinator.finish("job", "worker", claim["claim_token"], {"forged": True})
    now[0] = 100.5
    restarted = Coordinator(Database(db.path), clock=lambda: now[0])
    with pytest.raises(ValueError, match="authority_clock_rollback"):
        restarted.finish("job", "worker", claim["claim_token"], {"forged": True})
    assert restarted.jobs()[0]["status"] == "running"
    now[0] = 102
    replacement = restarted.claim("new-worker", ["backtest"])
    assert replacement["attempt"] == 2
    restarted.finish("job", "new-worker", replacement["claim_token"], {"ok": True})
    assert restarted.jobs()[0]["result"] == {"ok": True}


def test_even_held_lease_refusal_remembers_observed_time(authority):
    db, now, coordinator = authority
    coordinator.acquire("ledger", "owner")
    now[0] = 110
    with pytest.raises(ValueError, match="lease_held"):
        coordinator.acquire("ledger", "contender")
    now[0] = 105
    with pytest.raises(ValueError, match="authority_clock_rollback"):
        Coordinator(Database(db.path), clock=lambda: now[0]).claim("worker", ["backtest"])


@pytest.mark.parametrize("operation", ["acquire", "claim"])
def test_authority_samples_clock_only_after_database_lock(authority, monkeypatch, operation):
    db, now, coordinator = authority
    coordinator.submit("job", "backtest", {})
    transaction = db.transaction

    @contextmanager
    def delayed_lock():
        with transaction() as conn:
            now[0] = 200  # Time elapsed obtaining the database write lock.
            yield conn

    monkeypatch.setattr(db, "transaction", delayed_lock)
    result = (
        coordinator.acquire("ledger", "owner", ttl=10)
        if operation == "acquire"
        else coordinator.claim("worker", ["backtest"], ttl=10)
    )
    assert result["expires"] == 210


def test_failed_business_write_rolls_back_but_clock_commits(authority):
    db, now, coordinator = authority
    lease = coordinator.acquire("ledger", "owner")
    # Inject a failure after an actual insertion, before statement completion.
    with db.transaction() as conn:
        conn.execute(
            "CREATE TRIGGER refuse_write AFTER INSERT ON cp_writes "
            "BEGIN SELECT RAISE(FAIL, 'injected write failure'); END"
        )
    now[0] = 110
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
        coordinator.write("ledger", "owner", lease["epoch"], "cash", {"amount": "999"})
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM cp_writes").fetchone()[0] == 0
    assert db.get("authority-clock", "leases")["value"]["observed_at"] == 110


def test_renewal_uses_one_time_sample(authority):
    db, _now, coordinator = authority
    lease = coordinator.acquire("ledger", "owner", ttl=1)
    samples = iter([100.5, 10000])
    renewed = Coordinator(db, clock=lambda: next(samples)).renew("ledger", "owner", lease["epoch"])
    assert renewed["expires"] == 130.5
    assert next(samples) == 10000
