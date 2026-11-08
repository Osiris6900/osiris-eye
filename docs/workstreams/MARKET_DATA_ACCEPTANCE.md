# Authorized market-data integration acceptance

Tracks #178. No provider access or redistribution rights are implied.

- [ ] Define adapters for equities, futures, FX, options and crypto with provider-specific entitlement records.
- [ ] Validate UTC event, receive and availability timestamps; reject future, stale and malformed observations.
- [ ] Normalize symbols using an instrument master, exchange calendar, corporate actions and contract rolls.
- [ ] Preserve raw payload digest, source, license, revision and transformation provenance.
- [ ] Exercise disconnect, duplicate, out-of-order, gap and provider-failover cases.
- [ ] Demonstrate deterministic point-in-time replay without lookahead and publish data-quality metrics.

Do not feed synthetic or unlicensed data into production decisions.
