from concurrent.futures import ThreadPoolExecutor
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from osiris_api.control_plane.agents import JobWorker, ResearchAgents
from osiris_api.control_plane.coordination import Coordinator
from osiris_api.control_plane.database import Database, canonical, digest
from osiris_api.control_plane.replication import Replicator


def test_one_leader_and_fencing_after_failover(tmp_path):
    db = Database(str(tmp_path / "control.db"))
    now = [100.0]
    coordinator = Coordinator(db, clock=lambda: now[0])

    def acquire(worker):
        try:
            return coordinator.acquire("ledger", worker)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        winners = [lease for lease in pool.map(acquire, [f"worker-{i}" for i in range(8)]) if lease]
    assert len(winners) == 1
    old = winners[0]
    coordinator.write("ledger", old["owner"], old["epoch"], "account", {"cash": "100"})
    now[0] += 31
    new = coordinator.acquire("ledger", "replacement")
    assert new["epoch"] == old["epoch"] + 1
    with pytest.raises(ValueError, match="stale"):
        coordinator.write("ledger", old["owner"], old["epoch"], "account", {"cash": "999"})
    coordinator.write("ledger", "replacement", new["epoch"], "account", {"cash": "100"})


def test_retry_rejects_old_worker_completion(tmp_path):
    now = [100.0]
    queue = Coordinator(Database(str(tmp_path / "queue.db")), clock=lambda: now[0])
    queue.submit("job-1", "backtest", {"x": 1})
    first = queue.claim("worker-a", ["backtest"], ttl=1)
    now[0] += 2
    second = queue.claim("worker-b", ["backtest"])
    assert second["attempt"] == 2
    with pytest.raises(ValueError, match="stale"):
        queue.finish("job-1", "worker-a", first["claim_token"], {"forged": True})
    queue.finish("job-1", "worker-b", second["claim_token"], {"ok": True})
    assert queue.submit("job-1", "backtest", {"x": 1})["status"] == "completed"
    with pytest.raises(ValueError, match="conflict"):
        queue.submit("job-1", "backtest", {"x": 2})


def test_review_and_actual_restricted_worker(tmp_path):
    db = Database(str(tmp_path / "queue.db"))
    agents = ResearchAgents(db)
    agents.request(
        "experiment-1",
        "alice",
        "analyst",
        "backtest",
        {"signals": [0, 1, 0], "asset_returns": [0, 0.1, -0.1]},
    )
    with pytest.raises(ValueError, match="independent"):
        agents.approve("experiment-1", "alice", "operator")
    agents.approve("experiment-1", "bob", "operator")
    assert JobWorker(agents.queue, db, "local-worker").once()["status"] == "completed"
    result = agents.queue.jobs()[0]["result"]
    assert result["periods"] == 2
    assert result["total_return"] == pytest.approx(-0.1)
    with pytest.raises(ValueError, match="permission"):
        agents.request("evil", "alice", "analyst", "shell", {"command": "whoami"})
    with pytest.raises(ValueError, match="unexpected"):
        agents.request(
            "credential-mistake",
            "alice",
            "analyst",
            "backtest",
            {"signals": [0, 1], "asset_returns": [0, 0.1], "password": "TEST-NOT-A-CREDENTIAL"},
        )
    assert db.get("agent-requests", "credential-mistake") is None


def test_replica_convergence_signature_gap_and_atomic_rollback(tmp_path):
    key_a, key_b = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    trusted = {
        name: key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        for name, key in [("a", key_a), ("b", key_b)]
    }
    left = Replicator(Database(str(tmp_path / "left.db")), trusted)
    right = Replicator(Database(str(tmp_path / "right.db")), trusted)
    a = Replicator.sign("a", 1, "event-a", {"cash": "10"}, key_a)
    b = Replicator.sign("b", 1, "event-b", {"cash": "20"}, key_b)
    left.apply([a, b])
    right.apply([b, a])
    assert left.converged_state() == right.converged_state()
    assert left.apply([a])["duplicates"] == 1
    with pytest.raises(ValueError, match="signature"):
        left.apply([{**a, "signature": "AAAA"}])
    next_event = Replicator.sign("a", 2, "event-a2", {}, key_a)
    gap = Replicator.sign("a", 4, "event-a4", {}, key_a)
    with pytest.raises(ValueError, match="gap"):
        left.apply([next_event, gap])
    assert len(left.export("a")) == 1


@pytest.mark.parametrize("sequence", [2**63, 10**100])
def test_replication_integer_overflow_rejected_before_any_write(tmp_path, sequence):
    key = Ed25519PrivateKey.generate()
    trusted = {"a": key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)}
    replica = Replicator(Database(str(tmp_path / "replica.db")), trusted)
    first = Replicator.sign("a", 1, "first", {}, key)
    # Build a correctly signed envelope without relying on the signing helper's
    # validation, as an independently implemented peer could do.
    event = {"origin": "a", "seq": sequence, "id": "overflow", "payload": {}}
    envelope = {**event, "digest": digest(event),
                "signature": base64.b64encode(key.sign(canonical(event))).decode()}
    with pytest.raises(ValueError, match="sequence"):
        replica.apply([first, envelope])
    assert replica.export("a") == []
    with pytest.raises(ValueError, match="sequence"):
        Replicator.sign("a", sequence, "overflow", {}, key)
    with pytest.raises(ValueError, match="cursor"):
        replica.export("a", after=sequence)


def test_replication_cursor_accepts_largest_sqlite_integer(tmp_path):
    replica = Replicator(Database(str(tmp_path / "replica.db")), {})
    assert replica.export("a", after=2**63 - 1) == []
