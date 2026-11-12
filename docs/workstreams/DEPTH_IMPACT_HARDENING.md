# Atomic depth capture and exact displayed-book estimates

Addresses defects in the liquidity workstream (#396) and the displayed-depth
portion of paper execution realism (#370 / T15).

## Corrected failure paths

Previously, capture committed the latest snapshot before inserting its history.
A history-write failure left published evidence incomplete. This also occurred
for a valid 128-character instrument because appending its sequence made the
history identifier exceed the storage limit. Latest and immutable history now
commit in one SQLite transaction. A deterministic bounded history ID binds the
instrument and sequence. Sequence checks run inside the same write lock, so
concurrent captures cannot regress the published sequence or overwrite history.

Freshness now records a durable per-instrument observed-time watermark, including
when an expired or replayed operation is refused. A new process cannot revive an
expired book by rolling the authority clock back. Capture checks time before and
after the HTTP response and again inside the database lock. Invalid clocks fail
before fetching. This is local clock fencing; it does not authenticate exchange
time, prove vendor-cache freshness or replace independent time-source acceptance.

Captures retain source URL, venue, raw digest, local receipt time and the exact
configured entitlement reference. Analysis verifies matching immutable history
and the current reference. Legacy captures require a fresh, higher-sequence
capture instead of being relabelled with new provenance. Old history remains
unchanged. A reference is an operator declaration, not rights-owner approval.

## Calculation and API contract

The authenticated research-read endpoint remains:

```http
POST /control-plane/depth/BTC-USD/impact
Authorization: Bearer <configured analyst token>
Content-Type: application/json

{"side":"buy","quantity":"3","limit_price":"101"}
```

`limit_price` is optional. Marketable levels are consumed in price order only
inside that limit; quantities beyond the limit or retained depth stay unfilled.
For bids at 100 and asks of one unit at 101 and one at 102, this request estimates
one filled unit, two unfilled units and notional 101. Without the limit it
estimates two units, one unfilled unit and notional 203. Estimates never consume
the stored snapshot, create paper-ledger fills or submit a broker order.

All quantity, notional and cost arithmetic uses exact rational values derived
from the existing bounded decimal parser. Results include individual estimated
fills, filled notional, midpoint, half-spread cost, visible-depth cost and total
shortfall from midpoint. Spread and depth costs add exactly to the shortfall for
the filled quantity. Input exponents remain bounded to 1000; each retained side
has at most 1000 levels. Truncation is explicitly reported without extrapolation.

A volume-weighted average can have a repeating expansion. Its display uses an
explicit 28-significant-digit half-even context, independently of the process's
Decimal settings. The response also includes its exact numerator/denominator
and a flag when the display is rounded. Display rounding never feeds accounting
or the decomposition. Zero fills return a null average and ratio.

The response binds the estimate to snapshot ID/digest, raw digest, source,
entitlement, receipt time and observed age. The age is explicitly local response
receipt age. This static displayed-book calculation excludes queue position,
latency, cancellation/replenishment, hidden liquidity, fees, margin and borrow.
It is not calibrated future market impact or realized transaction-cost analysis.
Capture still requires an MFA research session; analysis retains research-read
authorization. Live execution remains disabled.

## Verification

```bash
python -m pytest -q services/quant-api/tests/test_control_plane_depth.py
python -m pytest -q services/quant-api/tests tests/test_ultra_fast_feed.py
```

There are 57 new regressions. They inject a real SQLite history-insert failure,
race two capture workers, exercise maximum identifier length, rejected history
conflicts, retained-depth truncation, exact tiny and high-precision amounts,
changed ambient Decimal contexts, repeating VWAP, both sides and price limits,
clock rollback after an expiry refusal/restart, legacy and mismatched provenance,
malformed input and actual API permission/validation paths. The complete backend
suite passes 1,931 tests and 26 subtests with no skips.

These fixes strengthen W17 and the depth estimates used by T15. They do not close
T15's venue calibration, queue/margin/borrow models or independent multi-asset
comparison. The seventeen outstanding release gates remain outstanding; source
tests cannot substitute for external reviews, licences or approved host evidence.
