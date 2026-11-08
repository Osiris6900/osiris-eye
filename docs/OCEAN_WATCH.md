# Ocean Watch — the network watches the world's seas

`osiris_api.ocean_watch` extends the watch layers (markets, world events,
celestial) to the oceans: four keyless public sources, zero accounts, zero
API keys, zero payment. Every reading is normalized into the signed
`osiris.observation.v1` contract and passes through the same fail-closed
ingestion gates (kill switch, provider allowlist, freshness, dedupe) as
every other observation on the mesh.

## Sources

| Provider | Source | What it sees | Cost |
|---|---|---|---|
| `open-meteo-marine` | Open-Meteo Marine API | Wave height/period, sea-surface temperature, ocean-current velocity at 8 shipping chokepoints | keyless |
| `noaa-ndbc` | NOAA NDBC `realtime2` buoy files | Newest observation per offshore buoy: wind, gust, wave height, pressure, water temperature | keyless |
| `noaa-coops` | NOAA CO-OPS datagetter | Latest water level (MSL datum) at reference tide stations | keyless |
| `nws-alerts` | NWS `api.weather.gov` | Active US marine/coastal alerts (tsunami, hurricane, storm, coastal flood, special marine) | keyless |
| `jma-bulletins` | JMA eqvol Atom feed | Japan tsunami/eruption/ashfall/earthquake bulletins (northwest Pacific) | keyless |

### Shipping chokepoints (marine provider)

Malacca, Hormuz, Suez, Panama, Bab el-Mandeb, English Channel,
Cape of Good Hope, Gibraltar — the sea lanes world trade physically
passes through. Rough seas or unusual currents here are what global
shipping actually feels.

### Default buoys and tide stations

- Buoys: `46059` (California offshore), `51001` (Hawaii NW), `42001`
  (Gulf of Mexico), `46002` (Oregon offshore).
- Tide stations: `8518750` (The Battery, NY), `9414290` (San Francisco),
  `8724580` (Key West).

Operators can extend every registry; validation stays fail-closed
(unknown sites/stations/event names are rejected, never fetched).

## Event kinds and instruments

| Kind | Instrument prefix | Example |
|---|---|---|
| `marine_conditions` | `SEA:` | `SEA:MALACCA` |
| `buoy_conditions` | `BUOY:` | `BUOY:46059` |
| `water_level` | `TIDE:` | `TIDE:8518750` |
| `marine_alert` | `ALRT:` | `ALRT:<nws-id>` |

These prefixes are scanned by the autonomous research cycle alongside
`EQ:`/`EONET:`/`WX:`/`ASTRO:`/`SWPC:`, so ocean events flow into
event-time-aligned impact checks, the hypothesis ledger and rule trust
labels without any extra wiring.

## Market impact hypotheses

`event_market_impact.DEFAULT_EXPOSURE_RULES` maps ocean events to the
economies their sea lanes feed, e.g. Hormuz/Bab el-Mandeb → `USD-JPY`,
`^SPX`; Malacca → `USD-CNY`, `^NKX`; Suez/Gibraltar/Channel → `USD-EUR`;
tsunami alerts → `^SPX`, `^NKX`, `USD-JPY`. As everywhere in OSIRIS EYE:
these are hypotheses for research — `causality` is always
`not_established`, and live execution stays disabled.

## Usage

```python
from osiris_api.data_store import ResearchStore
from osiris_api.ocean_watch import watch_and_ingest_ocean

store = ResearchStore("research.sqlite3")
result = watch_and_ingest_ocean(store)  # one bounded cycle, default watchlist
```

`OSIRIS_P2P_INGEST_ENABLED=1` must be set for ingestion (same kill switch
as every other feed). Source failures are collected in
`result["source_errors"]` and are never fatal; no reading is ever
fabricated.

## Honest edges

- Coverage is *measured points* (chokepoints, buoys, tide stations, US
  alerts), not a synthetic global ocean picture; unmeasured seas are
  unreported, not interpolated.
- NWS alerts cover US waters; JMA bulletins add the northwest Pacific.
  INCOIS (India) was probed and is not reachable as a stable keyless
  endpoint — deferred, not wired. PTWC/others remain a follow-up.
- Buoy text files are ~45-day rolling windows; gaps mean a dead station,
  reported as a source error, never filled in.
- `observed_at` is collection time; the measurement's own time stays in
  the payload. Nothing is backdated.
