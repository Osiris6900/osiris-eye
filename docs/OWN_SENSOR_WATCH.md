# Own-Sensor Watch — the network measures the world itself

> Every other watch layer trusts someone else's instruments — USGS
> seismographs, NOAA satellites, exchange feeds. This layer lets a node
> publish what its **own hardware** measured, through the same contract
> and the same fail-closed gates. No attached sensor = no observation;
> nothing is ever invented.

## Why

An observer who cannot afford to trust anyone measures things directly.
A phone already carries a sensor suite: the accelerometer is a pocket
seismograph, the magnetometer a space-weather detector, the barometer a
pressure station. Cheap add-ons go further — a Raspberry Shake is a real
seismograph, an RTL-SDR dongle is a radio telescope's little sibling.
When several nodes self-measure, the mesh consensus layer turns noisy
citizen instruments into a cross-checked observation network.

## Sensor kinds (bounded, fail-closed)

| kind | reading | bounds | typical hardware |
|---|---|---|---|
| `seismic` | acceleration magnitude (+ optional x/y/z), m/s² | 0–60 | phone accelerometer, Raspberry Shake |
| `magnetic` | total field, µT | 0–1000 (Earth ≈ 25–65) | phone magnetometer |
| `pressure` | barometric, hPa | 300–1200 | phone barometer |
| `radio` | frequency MHz + strength dB | 0.01–6000 / −130–10 | RTL-SDR (`rtl_power`) |

Out-of-bounds or malformed readings are **rejected, never clamped** —
outside the physical sanity range a reading is broken, not interesting.

## Wiring a reader

Readers are injected callables — the module never touches hardware
itself. A reader takes no arguments and returns the raw fields:

```python
import json, subprocess

def android_accelerometer():
    # Termux:API on Android: one sample from the accelerometer
    out = subprocess.run(
        ["termux-sensor", "-s", "accelerometer", "-n", "1"],
        capture_output=True, text=True, check=True).stdout
    sample = json.loads(out)["accelerometer"]["data"][0]["values"]
    x, y, z = sample
    return {"value": abs((x*x + y*y + z*z) ** 0.5 - 9.81), "x": x, "y": y, "z": z}

report = watch_and_ingest_sensors(
    store, readers={"seismic": android_accelerometer}, node_label="phone-1",
)
```

Equivalent readers: Raspberry Shake (local UDP stream → magnitude),
`rtl_power` CSV row → `{"frequency_mhz": ..., "strength_db": ...}`.

## Trust model

- The observation provider is `node-self:<label>`; ingestion allowlists
  default to exactly this node's own provider. Trusting *other* nodes'
  self-measured data is the operator's explicit choice.
- **Self-measured is not self-trusted.** One node's reading is one
  observation; meaning emerges when independent nodes agree through the
  existing multi-observer consensus gates.
- Every payload carries `measurement: "self_measured"`,
  `instrument_grade: "citizen_sensor"` and an honest `precision_note`.
- The ingestion kill switch, provenance, freshness and contract gates
  all apply — this layer has no side doors. Live trading stays disabled.

## Verification

14 tests: contract shape + provider/instrument naming, axes preserved,
reader/kind/label validation, too-many-readers bound, reader-failure
isolation, out-of-bounds and malformed readings rejected (including
NaN), end-to-end ingest with duplicate skip, foreign-provider rejection,
kill-switch fail-closed, sensor-error reporting.
