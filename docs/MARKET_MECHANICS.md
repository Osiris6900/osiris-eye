# Market Mechanics research lab

Ten deterministic diagnostics describe how positioning, liquidity and institutional flow rules can affect markets. The API calculates from supplied inputs; it does not connect new data feeds, identify hidden positions, submit orders or turn historical associations into validated predictions.

`GET /research/market-mechanics/catalog` returns the ten modules with their endpoint, evidence strength, mechanism, limitations and a runnable synthetic `sample_input`. All successful diagnostics include `research_only: true` and `execution_enabled: false`. The platform registry contains 107 modules after this addition.

## Evidence and endpoints

All POST paths below are relative to `/research/market-mechanics`.

| Mechanism | POST path | Evidence interpretation |
|---|---|---|
| Dealer Gamma Exposure (GEX) | `/dealer-gex` | Strong mechanical basis for hedging feedback when actual dealer positions and gamma are known; direction and pinning remain conditional |
| Options Expiry & Max Pain | `/expiry-max-pain` | Expiry-related effects are mixed; the minimum-payout Max Pain strike has weak predictive support |
| Liquidity Vacuum | `/liquidity-vacuum` | Strong mechanical basis for price jumps when aggressive orders exhaust available depth |
| Order Flow Imbalance | `/order-flow-imbalance` | Strong short-horizon association with price pressure; classification and market state matter |
| Dark Pool Trading | `/dark-pool` | Off-exchange activity is real, but aggregate volume alone does not reveal institutional direction |
| Funding Rate & Liquidation Clusters | `/funding-liquidations` | Strong mechanical basis for forced liquidation feedback; supplied clusters are scenarios |
| Treasury Liquidity & Repo Market | `/treasury-repo` | Important but complex financial-conditions channels; a balance-sheet proxy is incomplete |
| Index Rebalancing | `/index-rebalancing` | Passive tracking flows are well documented; actual timing and price response vary |
| Volatility-Control Funds | `/volatility-control` | Target-volatility rules can require selling as volatility rises; actual fund mandates differ |
| Cross-Asset Lead–Lag | `/cross-asset-lead-lag` | Historical relationships exist but are unstable and sensitive to sampling and lag selection |

Evidence strength describes the mechanism, not model calibration, data quality, forecast accuracy or a trading recommendation. None of these modules establishes an investment edge.

## Calculation conventions

Rates, weights and returns are fractions: `0.01` means 1%. Volatility inputs are annualized fractions, leverage is a multiple of AUM, and a basis point is `0.0001`. Prices and monetary values must use the same quote currency within a calculation unless a field explicitly specifies USD or USD billions. Contracts reject unknown fields, nonfinite numbers and invalid numeric ranges. Numeric strings and booleans are not numeric inputs.

### Dealer Gamma Exposure (GEX)

For each supplied option, dollar gamma exposure for a +1% spot move is:

```text
gex = gamma_per_underlying_unit × dealer_contracts
      × contract_multiplier × spot² × 0.01
```

Gamma is nonnegative and measures change in option delta per unit change in underlying price. `dealer_contracts` carries the position sign: positive for dealer long, negative for dealer short, independently of call/put type. The multiplier converts one contract into underlying units. Net GEX sums signed exposures; gross GEX sums their absolute values. `hedge_notional_change_per_1pct_move` is the negative of net GEX under a local delta-neutral, constant-gamma assumption. Results are in the underlying's quote currency.

The module aggregates by strike and labels net long gamma as potentially dampening and net short gamma as potentially amplifying. It returns no pinning forecast or gamma-flip level. One request represents a single underlying and expiry; duplicate strike/type contracts must be aggregated first. The catalog example produces net GEX of `-2000` and hypothetical hedge-notional change of `+2000`.

### Options Expiry & Max Pain

For every supplied strike considered as settlement price `S`, aggregate intrinsic payout is:

```text
payout(S) = multiplier × Σ[max(S − strike, 0) × call_open_interest
                           + max(strike − S, 0) × put_open_interest]
```

`max_pain_candidates` contains all supplied strikes tied for minimum payout. Candidate evaluation is restricted to the supplied strikes; it is not a continuous settlement-price optimization. Open interest is in contracts and payout is in quote currency. If all open interest is zero, candidates are empty and minimum payout is `null`. The catalog example has minimum payout `10000` at strike `100`.

`hours_to_expiry` is reported as context; it does not create a timed forecast. Inputs represent one underlying, expiry and common multiplier, with unique strikes. Premiums, actual dealer inventory, physical-delivery details and price-impact calibration are excluded. `price_target` remains `null`.

### Liquidity Vacuum

A hypothetical buy consumes asks from lowest to highest price; a sell consumes bids from highest to lowest. Each fill is capped by visible level quantity, and unfilled quantity remains explicit if the supplied book runs out. Filled VWAP is `Σ(price × filled quantity) / filled quantity`.

Adverse slippage relative to the best price on the consumed side is `10000 × (VWAP / best − 1)` for a buy and its negative for a sell. `max_visible_gap_bps` is the largest adjacent price gap across the supplied side, normalized by its best price; it can include levels the order does not reach. `depth_exhausted` means some requested quantity remains unfilled. The module never invents a price for unfilled quantity. The catalog buy fills 10 of 15 units at VWAP `103`, leaving 5 unfilled.

Both sides of the book are required and must be uncrossed, with unique positive price levels and quantities. The diagnostic is a static sweep, omitting hidden liquidity, order-book changes and replenishment.

### Order Flow Imbalance

This endpoint measures executed trade-flow imbalance, not displayed-depth imbalance or order-event OFI:

```text
imbalance = (buy_aggressor_volume − sell_aggressor_volume)
            / (buy_aggressor_volume + sell_aggressor_volume)
```

Trades marked `unknown` are reported separately. `classified_fraction` is classified volume divided by total volume; unknown trades do not enter the imbalance denominator. If every trade is unknown, imbalance is `null` and pressure is `unclassified`. Otherwise pressure follows the sign of the imbalance. The catalog example yields imbalance `0.5` and classified fraction `100 / 120`. `predicted_return` remains `null`.

### Dark Pool Trading

The module sums supplied venue-identified dark-pool quantities and notionals, calculates their VWAP, divides dark-pool volume by matching consolidated volume, and reports the largest print's share of dark-pool volume. VWAP versus the supplied reference price is expressed in basis points. The catalog example has volume `150`, consolidated share `0.15`, notional `15100` and VWAP approximately `100.6667`.

Consolidated volume must cover the same instrument, quantity units and time window and cannot be smaller than supplied dark-pool volume. Empty dark-pool prints are allowed; VWAP and concentration are then `null`. Off-exchange volume is not automatically dark-pool volume. Both `inferred_direction` and `institutional_intent` remain `unknown`, even if VWAP exceeds the reference price.

### Funding Rate & Liquidation Clusters

Funding payments use the supplied rate for the supplied interval, without annualization or compounding:

```text
long_payment_usd = position_notional_usd × funding_rate
short_payment_usd = −long_payment_usd
cluster_distance = liquidation_price / spot − 1
```

Positive payment denotes an outflow; positive funding means longs pay shorts. `funding_interval_hours` makes the rate's period explicit. Long cluster thresholds must be at or below spot and correspond to hypothetical forced selling; short thresholds must be at or above spot and correspond to hypothetical forced buying. The module reports scenario notionals and signed distances without assigning cascade probabilities. The example charges longs `1` USD per eight-hour interval and places long/short scenarios at −5%/+5% from spot. No live liquidation inventory is connected.

### Treasury Liquidity & Repo Market

For each supplied snapshot, the proxy is `Fed assets − TGA − reverse repo`, with all three inputs in USD billions. The output reports previous/current values and their difference. The repo-reference spread is `(repo_rate − reference_rate) × 10000` basis points; the rates are fractions and must use comparable tenor and collateral conventions.

The catalog example increases the proxy from `6000` to `6050` USD billions and has a repo-reference spread of `20` bps. This proxy is not measured bank reserves and does not capture every liquidity channel. Publication cutoffs must be aligned by the caller. `measured_bank_reserves` is false and `price_direction_forecast` is `null`.

### Index Rebalancing

For each constituent:

```text
flow_notional = tracking_aum × (target_weight − current_weight)
flow_shares = flow_notional / price
adv_participation_fraction = abs(flow_shares) / adv_shares
```

Positive flow represents hypothetical buying; negative flow represents selling. Tracking AUM and prices use the declared three-letter common currency; ADV is shares per day. Zero ADV produces `null` participation and `adv_available: false`. The example implies buying `5000000` USD, or `50000` shares, equal to `0.05` of ADV.

Unique constituent symbols are required. A constituent subset is allowed, so current/target weights are not required to sum to one, but neither total may exceed one (within a numerical tolerance of `1e-9`). Net and gross notional totals cover only the supplied subset. The estimate omits anticipatory positioning, derivatives, cash management, timing and actual execution response.

### Volatility-Control Funds

The illustrative single-asset rule is:

```text
target_leverage = min(max_leverage, target_annual_volatility / observed_annual_volatility)
rebalance_notional = aum × (target_leverage − current_leverage)
```

Volatility inputs must be positive and share the same annualization convention. Positive rebalance is buying; negative is selling. Current leverage is supplied independently and may exceed the newly specified cap, permitting a deleveraging scenario. The example reduces leverage from `1.0` to `0.5` and implies selling `50000000` USD. Actual fund rules, smoothing, volatility forecasts and rebalance delays are not modeled.

### Cross-Asset Lead–Lag

Each source and target return has a timezone-aware ISO 8601 timestamp. The two series must have identical timestamps in strictly increasing, equally spaced order, and distinct asset names. Returns are fractions. Positive lag `k` correlates `source[t]` with `target[t+k]`, so **positive lag means source leads target**. Negative lag reverses the lead direction; zero lag is contemporaneous. Lags count observations, not days unless observations are daily.

The module scans `−max_lag` through `+max_lag`, reports Pearson correlation and paired-observation count at each lag, and compares first/second chronological halves as a simple instability diagnostic. A whole-series minimum of six observations and at least three pairs per scanned lag are required. Correlation is `null` for constant/degenerate series; half-sample values are also `null` when their samples are too short. The synthetic catalog example shifts the source by one observation, giving correlation `1.0` at lag `+1`; it does not demonstrate predictive performance.

This is an exploratory historical scan. It does not align exchange calendars, correct asynchronous publication times, select an unbiased lag, establish causality or perform holdout validation. Availability-time alignment remains the caller's responsibility.

## Running a diagnostic

Start the API and web app using the repository's quick-start instructions. Open the dashboard's Market Mechanics panel, select a module, inspect its assumptions, edit the synthetic JSON example, and run the diagnostic. Requests use the Vite `/api` proxy to reach the API. A deployed web server must route that prefix to the API separately.

To exercise an example directly against the local API without copying a request schema:

```python
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000") as client:
    catalog = client.get("/research/market-mechanics/catalog")
    catalog.raise_for_status()
    module = catalog.json()["modules"][0]
    response = client.post(module["endpoint"], json=module["sample_input"])
    response.raise_for_status()
    print(module["name"], response.json())
```

If `OSIRIS_API_TOKEN` is configured, pass `Authorization: Bearer <token>` on catalog and diagnostic requests. The existing production API authentication rules also apply to these endpoints.

## Data and interpretation

Use licensed or otherwise authorized inputs with matching instruments, units, observation windows and currencies. Retain source and availability timestamps outside these calculations, and query data as known at the research cutoff. Synthetic examples are illustrative inputs, not current market observations. A valid request does not establish source truth or freshness.

The API validates its bounded input contracts and rejects invalid values with HTTP 422. Endpoint request schemas are exposed through the API's OpenAPI documentation. Review each catalog entry's limitations alongside its numeric output.

For research evaluation, select hypotheses and lags before testing, preserve chronological train/test splits, account for overlapping samples and multiple testing, and measure performance after realistic costs. Static sensitivity, flow estimates and in-sample correlations do not substitute for that validation.
