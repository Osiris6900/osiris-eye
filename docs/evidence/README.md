# Evidence capture templates

These files are empty forms for the five blocking release steps. Filling one in does not by itself mark a tracker row implemented. A row moves only after the captured JSON is retained and a passing CI run or the signed artifact is linked from the inventory.

Put completed captures at the `retention` path inside `docs/evidence/captured/`. Do not put credentials, account numbers, or raw market payloads in that directory.

| Step | Tracker | Owners | Template |
| --- | --- | --- | --- |
| 100-peer independent-host soak | T04 | #176, #255 | [independent-host-soak.schema.json](independent-host-soak.schema.json) |
| 2/3-host E2EE with node binary SHA-256 | T03 | #176, #177 | [e2ee-binary-hashes.schema.json](e2ee-binary-hashes.schema.json) |
| Broker OMS reconciliation | T14 | #179 | [oms-reconciliation.schema.json](oms-reconciliation.schema.json) |
| Independent crypto and risk review | T06, T20 | #177, #179, #182 | [independent-review.schema.json](independent-review.schema.json) |
| Feed licence signoff | T09 | #178 | [feed-licence-signoff.schema.json](feed-licence-signoff.schema.json) |

The inventory checker confirms each template is present and names its retention path. It does not accept the template itself as a passing run.
