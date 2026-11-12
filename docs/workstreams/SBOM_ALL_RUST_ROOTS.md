# T01: complete Rust lock coverage in dependency evidence

The SBOM command previously read only `services/p2p-node/Cargo.lock`. It omitted
the native-feed and risk-kernel applications and the native feed's `memmap2`
dependency even though CI builds those products. A successful P2P SBOM was
therefore an incomplete inventory of the combined candidate.

The default command now reads all three committed Cargo locks, the npm lock,
Python project manifest and hashed Python deployment lock. It deduplicates
shared crates by exact name/version and refuses conflicting checksums rather
than selecting one. Missing metadata, an unsupported Cargo source or a missing
required lock fails closed. All six lock/manifest byte hashes participate in
the output identity, and mutations during metadata resolution reject generation.

`docs/CRATE_LICENSE_PROVENANCE.json` retains actual public crates.io metadata
queried in this task, including per-version source URLs, licences and package
checksums. Every crate record must match the current lock checksum. CI consumes
this snapshot offline; a new dependency without corresponding reviewed metadata
fails rather than fetching changing data during the gate. The existing checked-in
Python licence evidence remains required for every deployment pin.

```bash
python scripts/test_generate_dependency_sbom.py
python scripts/generate_dependency_sbom.py --offline \
  --crate-cache docs/CRATE_LICENSE_PROVENANCE.json \
  --output /tmp/dependency-sbom.json \
  --provenance-output /tmp/dependency-sbom-provenance.json
```

The current graph contains **613 components**, including all three Rust
applications, `memmap2` and 24 hash-locked Python distributions. The previous
output had 610 components and lacked the native/risk roots. For these exact
locks and metadata, the SBOM SHA-256 is
`75bf80041097973cf25d91822978a04e4a2b32ed31e3c394cc3a8fe031c4d7ac`.
The retained source record independently identifies the declared candidate,
actual checkout commit/tree and dirty flag, generator and metadata digests,
all six inputs, output digest and CI run/attempt. CI retains the SBOM, SHA-256
sidecar and source record for fourteen days. A PR merge checkout is recorded
as such; compare its complete tree with the candidate before using its evidence.
The source-bound engineering profile now includes this offline generation as
its twenty-fifth check and requires it for T01's local coverage.

To refresh metadata after a deliberate dependency change, run the generator
without `--offline`, using the same `--crate-cache` path, then review the actual
changed source records and checksums. Network access is only to public package
metadata; no package index credential is requested. The cache and evidence are
metadata-level records, not a source licence-text audit or an independent legal
decision. The source record is explicitly unsigned and never represents an
authenticated independent review or release authorization.

Tests cover multi-root inclusion/deduplication, conflicts, missing metadata and
locks, byte-level identity changes, mutation during generation, default CLI
coverage and truthful provenance. The residual `paste` maintenance advisory and
independent acceptance of the final audit/SBOM/provenance remain pending. T01
stays Partial; the release inventory remains 3 evidenced / 14 partial / 3 remaining.
This change does not alter execution or authorize live trading.
