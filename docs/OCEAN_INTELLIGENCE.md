# Ocean Intelligence — JMA bulletins, anomaly detection, correlation, shipping stress

Four additions on top of the ocean watch layer (see `OCEAN_WATCH.md`).

## 1. JMA bulletins (`jma-bulletins` provider)

Japan Meteorological Agency's keyless earthquake/volcano Atom feed
(`data.jma.go.jp/developer/xml/feed/eqvol.xml`). Categories are matched
by the Japanese title keywords: 津波 tsunami, 噴火 eruption, 降灰
ashfall, 震源 earthquake. Bulletins become `JMA:` events; tsunami
bulletins map to `^NKX`, `USD-JPY` in the exposure rules.

INCOIS (India) was probed and is unreachable/unstable as a keyless
endpoint — honestly deferred, not wired.

## 2. Ocean anomaly detection (`ocean_anomaly.py`)

The sea raises its own alarm: per `SEA:`/`BUOY:` instrument and metric
(wave height, sea-surface/water temperature, current, wind), the newest
reading is z-scored against its own recent baseline (min 8 readings,
max 96). `|z| >= 3` flags an `OCEAN:` anomaly event that flows through
the same ingestion gates, exposure rules and research cycle as measured
events.

Honest method: an outlier flag, not a climate model. Thin history is
reported `baseline_building` — never flagged on weak data. An anomaly
says "unusual for this instrument's own recent past", never "dangerous".

## 3. Cross-layer correlation (`event_correlation.py`)

One real thing, many senses: a hurricane appears as an EONET storm, an
NWS marine alert and a buoy wave spike. `cluster_events` unions events
that share a kind-affinity group, sit within 5° of each other (when
both carry coordinates) and overlap in time. `correlation_report` runs
this over the store's recent events and reports per-cluster
corroboration (`layers_seen / layers_possible`).

Report-only: clustering changes no ingestion or research behaviour.
Corroboration says "several independent senses saw something real" —
never proof of cause or market impact. Single-sightings stay visible,
honestly labelled.

## 4. Shipping stress index (`shipping_stress.py`)

One readable number per chokepoint lane: 0–100, documented weights —
wave height ≤ 50 (saturates at 6 m), ocean current ≤ 20 (saturates at
3 km/h), an active matching marine alert +30. Levels: calm / moderate /
high / severe. `fleet_stress` is the average over scored lanes.

Missing or stale readings are reported `no_data` — a blind lane is
never zero-scored (zero would mean "calm", which is a claim we cannot
make). The index is a context signal about shipping conditions, never
a forecast and never a trading signal.
