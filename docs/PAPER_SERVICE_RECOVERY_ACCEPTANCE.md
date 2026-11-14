# Local running-service paper recovery exercise

`scripts/run_paper_service_dr_exercise.py` starts the actual FastAPI application
in a separate OS process on loopback, with a generated bearer token and a
throwaway paper account. It never selects an operator ledger, submits a broker
order or fetches public/vendor market data. Both unauthenticated access and the
previous process's token are refused by the replacement service.

The exercise admits a working paper order and its first partial fill through
authenticated HTTP, makes a consistent SQLite online backup while the service
is running, and acknowledges a second fill after that backup. It then sends
SIGKILL to the actual service, observes the API outage, removes the temporary
primary database, restores the verified backup at a different path and restarts
the real application on the same local port.

Restored finances, order state and the entire journal must match the backup
snapshot and pass the real reconciliation API. Replaying the backed-up order
and fill must create no new financial state. The deliberately lost post-backup
fill is then reapplied once under its original client fill ID; its next retry
must be idempotent, and the resumed account must match the acknowledged account
from before the crash.

Run from the checkout with the API/development environment installed:

```bash
python scripts/run_paper_service_dr_exercise.py \
  --output-dir /tmp/osiris-paper-service-dr-unique
python -m pytest -q services/quant-api/tests/test_paper_service_dr_acceptance.py
```

Local sockets and child processes are required. The output directory must be
new and outside the checkout. The retained backup, resumed ledger, report and
file digests contain synthetic financial state; generated credentials and
temporary service logs are not retained. `restore_backup`, `verify_recovered`
and `verify_replay` also provide offline digest/integrity and trace-consistency
checks; they do not authenticate who produced an artifact.

| Measurement | Exact scope |
| --- | --- |
| `measured_rto_ms_local_only` | Monotonic elapsed time from issuing SIGKILL through restore, application restart, authenticated account recovery and reconciliation. |
| `recovery_point_age_at_failure_ms_local_only` | Elapsed time from completion of this local backup until the crash is issued. |
| `latest_lost_ack_age_at_failure_ms_local_only` | Elapsed time from the post-backup fill's successful HTTP acknowledgement until the crash. |
| `acknowledged_fills_missing_at_restore` | Difference between the acknowledged primary fill count and restored backup fill count, deliberately **one**, not an invented zero-RPO claim. |

This proves a bounded **local service-process** backup/outage/restore exercise.
It covers the execution-ledger database, not all platform stores, external
storage, independent hosts, production routing, secret-management recovery or
operator acceptance. Report fields for independent hosts, production recovery,
RTO/RPO certification and T18 release acceptance remain false. An approved
independent-host running-service drill and its review are still required.
