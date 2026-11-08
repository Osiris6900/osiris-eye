# Frontier Watch — rivers, radiation, satellites, volcanoes

`osiris_api.frontier_watch` adds four more keyless senses (no account,
no API key, no payment) to the network, each normalized into the signed
`osiris.observation.v1` contract and run through the same fail-closed
ingestion gates as every other observation.

## Sources (all live-probed before wiring)

| Provider | Source | What it sees | Prefix |
|---|---|---|---|
| `usgs-water` | USGS Water Services IV | Live discharge (cfs) + gage height at major US river gauges — a *measured* flood signal, not a report | `RIV:` |
| `safecast` | Safecast API | Newest citizen Geiger-counter measurement near monitored sites (Fukushima, Tokyo, Chernobyl, New York) | `RAD:` |
| `celestrak` | CelesTrak `gp.php` TLE files | Current orbital elements for the crewed-stations group | `SAT:` |
| `smithsonian-gvp` | Weekly Volcanic Activity Report RSS | New eruption/unrest items with aviation-relevant detail | `VOL:` |

### Default registries

- Gauges: `01646500` (Potomac, Washington DC), `11447650` (Sacramento),
  `07374000` (Mississippi, Baton Rouge).
- Radiation sites: Fukushima, Tokyo, Chernobyl, New York (50 km radius).
- CelesTrak groups: `stations` default; `visual`, `active`, `science`,
  `noaa`, `goes` known. Capped at 25 TLEs per cycle.

Every registry is operator-extensible; unknown sites/groups/feeds are
rejected, never fetched.

## Impact wiring

Exposure rules: Mississippi/Potomac/Sacramento gauges → `^SPX`;
Fukushima/Tokyo radiation → `^NKX`, `USD-JPY`; Chernobyl → `USD-EUR`;
volcano reports (Indonesia/Japan/Philippines) → `^NKX`, (Iceland/Italy)
→ `USD-EUR`. **Satellite TLEs deliberately have no exposure rule** —
an honest `no_known_exposure`. All four prefixes are scanned by the
research cycle. Causality is never established; live execution stays
disabled.

## Honest edges

- USGS gauges are US-only; India flood gauges (state portals / CWC) are
  a follow-up — their endpoints aren't reliably keyless yet.
- Safecast coverage is citizen-driven: gaps mean no volunteers
  reporting, reported honestly as `no_recent_measurements` — never
  filled in.
- TLEs describe orbits at their epoch (kept inside `tle_line1`); this
  layer does not propagate or predict re-entry — decay watch is a
  follow-up.
- GVP is a *weekly* report — fast eruptions appear in EONET/USGS first;
  this layer adds depth, not speed.
