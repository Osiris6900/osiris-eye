# Celestial Watch — प्राकृतिक और ब्रह्मांडीय प्रभाव

> The network watches natural and cosmic activity itself — Sun, solar
> storms, geomagnetic activity, Moon phases (amavasya/poornima), rashi and
> nakshatra, all planets, their close approaches to Earth, and eclipse
> windows — and checks each one's impact on the market.

Two families of sources, both keyless (no account, no key, no payment):

## 1. `noaa-swpc` — measured space weather (physical channel)

Real measurements from NOAA Space Weather Prediction Center public JSON:

| Topic | Endpoint | What it sees |
|---|---|---|
| `kp-index` | `noaa-planetary-k-index.json` | Planetary K-index → G1–G5 geomagnetic storm scale |
| `solar-wind` | `solar-wind/plasma-1-day.json` | Solar wind speed/density, high-speed streams |
| `xray-flux` | `json/goes/primary/xrays-1-day.json` | GOES X-ray flux → C/M/X solar flare class |
| `alerts` | `alerts.json` | SWPC warnings/watches (≤10 per cycle) |

**Physical market channel** (`market_channel: "infrastructure"`):
geomagnetic storms measurably disturb power grids, satellites, GNSS and HF
radio — this is the one celestial family with a documented physical
mechanism for market impact.

## 2. `astro-computed` — the network computes the sky itself

No network, no ephemeris download — pure deterministic astronomy (Moon
via Meeus chapter 47 periodic terms, Sun low-precision, planets via JPL
Keplerian elements 1800–2050):

| Topic | Computes |
|---|---|
| `lunar` | Moon elongation, illumination, **tithi (1–30)**, **amavasya** (tithi 30), **poornima** (tithi 15), shukla/krishna paksha, sidereal **rashi** (Mesha…Meena), **nakshatra** (Ashwini…Revati + pada), Moon distance + perigee (Earth के सबसे पास) |
| `planets` | All 7 planets (Mercury…Neptune): geocentric longitude, Earth distance (AU), elongation — flags **opposition** (>170°), **conjunction** (<10°), **close approach to Earth** (nearest 15% of possible range) |
| `eclipse` | Approximate **solar/lunar eclipse windows** (syzygy + ecliptic-latitude + Sun–node distance limits, with nutation) |

`market_channel: "behavioral_research"` — these are astronomical facts;
any market link is correlation research only.

## Honesty contract (read this)

- **Causality is never established.** Every impact check runs through
  `event_market_impact.assess_event_impact` and carries
  `causality: "not_established"`, `hypothesis: true`, `live_execution: false`.
- Lunar/planetary/nakshatra observations are computed geometry, not
  predictions of market moves. Amavasya/poornima exposure is a behavioural
  research hypothesis, nothing more.
- **Precision limits are stated in every payload** (`precision_note`):
  Moon longitude ~0.01° (Meeus ch.47 periodic terms — verified against
  real sky events: the 2000-01-06 new moon lands within 0.01° of exact
  conjunction), eclipse flags combine syzygy + Moon latitude + Sun–node
  distance limits and remain approximate *windows* (not exact eclipse
  predictions), planetary flags are windows (not exact ephemeris
  moments). Ritual-grade timing needs a full ephemeris — out of scope.
- Nothing is fabricated: malformed NOAA responses are recorded as errors.

## Exposure map wiring (event_market_impact)

| Event kind | Keyword in title | Instruments |
|---|---|---|
| `geomagnetic` | "storm" (only when Kp ≥ 5) | ^SPX, ^NDQ, ^DJI |
| `solar_wind` | "high-speed" | ^SPX, ^NDQ |
| `xray_flare` | "m-class" / "x-class" | ^SPX, ^NDQ |
| `lunar` | "amavasya" / "poornima" | ^SPX, ^NDQ, BTCUSDT |
| `planetary` | "opposition" / "close approach" / "conjunction" | ^SPX, ^NDQ, BTCUSDT |
| `eclipse` | "eclipse" | ^SPX, ^NDQ, ^DJI, BTCUSDT |

Quiet Kp, nominal planetary geometry and sub-C flares match nothing — an
honest "no known exposure", not an error.

## Usage

```python
from osiris_api.celestial_watch import watch_and_ingest_celestial
from osiris_api.event_market_impact import (
    assess_event_impact, market_returns_from_store,
)

report = watch_and_ingest_celestial(store)      # one celestial cycle
series = market_returns_from_store(store, ["^SPX", "^NDQ", "BTCUSDT"])
impact = assess_event_impact(celestial_observation, series)
```

Same gates as every other watcher: `OSIRIS_P2P_INGEST_ENABLED=1` + provider
allowlist, freshness bounds, immutable dedup.

## Bounds

| Bound | Value |
|---|---|
| SWPC alerts per cycle | ≤ 10 |
| Response size | ≤ 2 MiB |
| HTTP timeout | 1–30 s |
| Planet-event strings | ≤ 6 (opposition > close approach > conjunction) |
| JPL elements validity | years 1800–2050 |

## What this layer does NOT do

- No market-move predictions from planets, nakshatras or eclipses
- No causality claims, ever (`causality: "not_established"`)
- No live trading (`live_execution: false` — permanently disabled)
- No ritual-grade timing (~0.01° Moon is research-grade, bounds documented)
- No API keys, accounts or paid feeds
