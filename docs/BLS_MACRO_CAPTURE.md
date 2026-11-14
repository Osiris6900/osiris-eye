# Keyless BLS monthly macro capture

`record_bls_macro.py` explicitly captures fixed official BLS public-API series:
US all-items CPI (`CUUR0000SA0`, not seasonally adjusted), civilian unemployment
rate (`LNS14000000`) and total nonfarm payroll employment (`CES0000000001`). It
batches the selected series into one request covering the current and previous
calendar years. It runs only when invoked; no background polling or default
mesh publication is added. Observe BLS public API limits before scheduling use.

From a clean committed checkout:

```bash
python scripts/record_bls_macro.py capture --output-dir /tmp/bls-capture-unique \
  --series CUUR0000SA0 LNS14000000 CES0000000001
python scripts/record_bls_macro.py verify --capture-dir /tmp/bls-capture-unique \
  --commit FULL_CAPTURE_COMMIT --response-sha256 PRINTED_RESPONSE_DIGEST
```

The private, exclusive capture directory contains the frozen plan and exact
request bytes, original response bytes, client request/receive times, code and
response hashes, normalized monthly observations and actual success/failure.
HTTP redirects, malformed/partial series, duplicate or missing months, stale
latest periods, future periods, unbounded responses and clock rollback fail
closed. A failed parse retains its bounded original response. No synthetic
replacement is generated when BLS is unavailable.

The statistical event date is the exclusive end of the reference month. It is
**not a BLS publication timestamp**. Every observation's availability is the
actual HTTP receive time, including historical or revised values returned by
the latest-vintage API. This cannot reconstruct what BLS published in the past,
serve as an advance release calendar or establish a profitable macro signal.
Preliminary/revision footnotes are retained. Annual averages are excluded.

Optional research-store ingestion requires an existing genuine operator feed
policy, provider `bls-public-monthly`, instruments `MACRO:BLS:<series ID>`, and
the existing ingestion kill switch enabled. The collector supplies no policy,
reviewer or entitlement approval. The same `feed_admission.admit_feed` policy
and current-capture freshness checks run before database creation/writes:

```bash
OSIRIS_P2P_INGEST_ENABLED=1 python scripts/record_bls_macro.py ingest \
  --capture-dir /tmp/bls-capture-unique --commit FULL_CAPTURE_COMMIT \
  --response-sha256 PRINTED_RESPONSE_DIGEST \
  --policy-json /private/operator-feed-policy.json --database /private/research.sqlite3
```

Unchanged series/month/value/footnotes keep their first actual availability.
Revised values or footnotes append a new immutable research observation linked
to the previous version; old observations remain queryable as of their original
availability. A full batch commits atomically. The output does not send a broker
order or broadcast into P2P.

Source/API references: <https://www.bls.gov/developers/api_signature_v2.htm> and
<https://www.bls.gov/bls/linksite.htm>. These links and an accessible API do not
replace an independent source-rights decision. Source authenticity, rights,
model and release acceptance remain unverified. This supplies genuine financial
macro capture capability for T10; T09/T10 acceptance is not promoted by code or
local fixtures. Live trading remains disabled.
