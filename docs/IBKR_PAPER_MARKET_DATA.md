# Interactive Brokers (IBKR) Paper Market Data

## Scope

OSIRIS EYE includes an optional read-only IBKR adapter for historical OHLCV bars.
It connects to Trader Workstation or IB Gateway only when the operator explicitly
configures paper mode. It does not place, modify, or cancel orders.

The adapter accepts explicit contracts rather than guessing from ticker symbols:

| Instrument | Contract notation |
|---|---|
| US stock | `STK:AAPL:SMART:USD` |
| FX pair | `CASH:EURUSD:IDEALPRO` |
| Future | `FUT:ES:202612:CME:USD:50` |
| Option | `OPT:AAPL:20261218:250:C:SMART:100:USD` |
| Index | `INDX:SPX:CBOE:USD` |

The first implementation supports equity, FX, futures, options and indices.
Market-data availability depends on IBKR account permissions, exchange subscriptions,
contract validity, API settings, and the selected data type. The adapter does not
independently verify the user's market-data licensing.

## Install

Install the API with the optional IBKR dependency:

```bash
pip install -e "services/quant-api[ibkr,dev]"
```

Run IB Gateway or Trader Workstation locally and enable its socket/API connection.
For paper trading, use the paper TWS/Gateway port (commonly 7497 for TWS or
4002 for IB Gateway). Confirm the actual port in the running application.

Configure environment variables in the deployment secret/configuration manager:

```bash
OSIRIS_IBKR_MODE=paper
OSIRIS_IBKR_HOST=127.0.0.1
OSIRIS_IBKR_PORT=7497
OSIRIS_IBKR_CLIENT_ID=7
OSIRIS_IBKR_ACCOUNT=DUxxxxxxxx
OSIRIS_IBKR_ENTITLEMENT_REFERENCE=operator-confirmed-ibkr-data-entitlement
OSIRIS_IBKR_BAR_SIZE=1 day
OSIRIS_IBKR_TIMEOUT_SECONDS=5
```

The account identifier must be a paper account beginning with `DU`, and the
configured port must be one of the paper ports accepted by this adapter. The adapter
connects with the IB API `readonly=True` flag and verifies that the configured
paper account is among the accounts returned by the gateway. Do not expose TWS
or Gateway ports to the public internet.

## API

`GET /market-data/providers` shows whether the adapter is registered.
`GET /market-data/provider-readiness` shows required configuration presence.

Example historical bars request:

```text
GET /market-data/bars?provider=interactive_brokers&symbol=STK%3AAAPL%3ASMART%3AUSD&start=2026-11-02T09%3A00%3A00Z&end=2026-11-02T21%3A00%3A00Z&asset_class=equity
```

Requests are capped at 365 calendar days and 50,000 returned bars by the shared
gateway. The configured bar size defaults to daily. Historical data permissions,
pacing limits, exchange hours and upstream availability still apply.

## Safety and limitations

- Paper account only; live ports and non-`DU` accounts are rejected.
- The IB API connection is read-only.
- No order-placement API is implemented or exposed.
- The adapter is not considered connected until an operator runs it against their
  own IB Gateway/TWS instance and verifies the returned data.
- The entitlement reference is an operator assertion, not proof of a data license.
- This is historical-bar polling, not streaming market data.
- Never commit account identifiers, passwords, tokens, certificates, or other secrets.
- A paper account is not proof of strategy profitability or production readiness.
