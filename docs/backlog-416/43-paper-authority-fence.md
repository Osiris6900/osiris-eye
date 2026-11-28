# Package 43: paper-book writer fence

The canonical paper ledger is one SQLite file. SQLite already serializes
transactions on that file. What it does not do is reject a second writer that
should have stopped after failover. Control-plane leases fence control-plane
documents only. Paper orders, fills, cancels, marks, kill-switch changes,
pipeline decisions and pipeline-run records did not consult a lease.

## Behavior

Each mutation takes or renews one lease in the same transaction as the write.
A crash rolls the renewal back with the order.

- The owner is `OSIRIS_PAPER_WRITER_TOKEN` when that value matches
  `[A-Za-z0-9._:-]{1,128}`. An invalid token fails closed and writes nothing.
- When the variable is unset, every process uses `shared-file`. A restart of
  the same file can still write. Two hosts that both leave the variable unset
  are not distinguished.
- A different owner is `split_brain_rejected` until 30 seconds after the last
  committed mutation. The next owner then gets the next epoch. The previous
  owner cannot commit.
- Authority time moves only forward. A backward clock is
  `authority_clock_rollback`.
- The HTTP body cannot name a writer. The snapshot reports the epoch and a
  fingerprint, not the token.
- Reads do not take the lease.

This is one lease on one file. It is not Raft, a quorum, or detection of a
copied database that reuses the same token. `LIVE_EXECUTION_ENABLED` stays
false.
