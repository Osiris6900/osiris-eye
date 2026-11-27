# Shadow run and comparison arithmetic

The `ShadowService` is an isolated long/flat simulation. It uses the previous
tick's target, applies fees and a loss halt, and never submits broker orders.

## Version 2

New runs use `lagged-long-flat-independent-risk-v2`; new comparison reports use
`event-aligned-paper-equity-v2`. These policies make calculation independent of
the calling thread's Decimal precision, rounding, exponent settings and traps.

- Input fingerprints preserve every significant decimal digit. Equivalent
  representations such as `100`, `100.0` and `1e2` normalize identically.
  Canonical values use compact exponent notation when fixed notation would
  exceed 64 characters.
- Cash, notionals, fees, equity, P&L and comparison differences use exact finite
  decimal arithmetic. A flat run preserves its starting cash exactly.
- Desired and affordable position allocations use 28 significant digits,
  rounded toward zero. Both allocation quotients are nonnegative, so this
  rounding cannot exceed the position cap or spend more cash than available.
  Small residual cash is retained. This is a deterministic quantity convention,
  not exchange lot-size or fill calibration.
- Run configuration and tick inputs retain the broker monetary parser's
  64-character limit. Persisted results and paper-equity observations use the
  shared decimal bounds: 256 significant digits and absolute decimal exponent
  at most 1000. Monetary fields, including reported fill quantities, retain
  the existing absolute magnitude ceiling of `1e15`.
- Generated runs and reports are validated before insertion. Results outside
  those bounds are rejected atomically rather than rounded or stored as
  records that fail on their next read.

Paper observations remain operator supplied. Their hashes bind the submitted
evidence; they do not establish independent market or ledger verification.

## Existing records

Sealed version 1 records keep their original contents and SHA-256 hashes.
Reads verify their historical equations under an explicit 28-digit,
half-even Decimal context. Ordinary identical-input run replays return the
existing record; new conflicting input fails without overwriting it.

Version 1 already discarded some high-precision input digits. The original
values cannot be recovered from an aliased fingerprint. Requests that no
longer match the exact fingerprint fail closed; the service does not rewrite
or silently upgrade historical evidence. Unsealed or corrupt records remain
invalid. New comparison reports can reference a verified version 1 run.

Run the regression checks with:

```sh
python -m pytest -q services/quant-api/tests/test_shadow_engine.py \
  services/quant-api/tests/test_shadow_precision.py \
  services/quant-api/tests/test_shadow_legacy.py \
  services/quant-api/tests/test_control_plane_research.py
```
