/** Research-only validation; never authorizes live execution. */
export type MarketObservation = { symbol: string; source: string; price: number; timestampMs: number };
export type MarketDataPolicy = { nowMs: number; maxAgeMs: number; trustedSources: readonly string[] };
export type MarketDataResult = { accepted: true; observation: MarketObservation } | { accepted: false; reason: string };

export function validateMarketObservation(observation: MarketObservation, policy: MarketDataPolicy): MarketDataResult {
  if (!observation || typeof observation !== 'object' || !policy || typeof policy !== 'object' ||
      !Array.isArray(policy.trustedSources) ||
      !policy.trustedSources.every(source => typeof source === 'string' && source.length > 0)) {
    return { accepted: false, reason: 'invalid_input' };
  }
  if (typeof observation.symbol !== 'string' || !/^[A-Z0-9._:-]{1,32}$/.test(observation.symbol)) {
    return { accepted: false, reason: 'invalid_symbol' };
  }
  if (typeof observation.source !== 'string' || !observation.source ||
      !policy.trustedSources.includes(observation.source)) {
    return { accepted: false, reason: 'untrusted_source' };
  }
  if (![observation.price, observation.timestampMs, policy.nowMs, policy.maxAgeMs].every(
    n => typeof n === 'number' && Number.isFinite(n)
  ) || observation.price <= 0 || observation.timestampMs < 0 || policy.nowMs < 0 || policy.maxAgeMs <= 0) {
    return { accepted: false, reason: 'invalid_numeric_input' };
  }
  if (observation.timestampMs > policy.nowMs || policy.nowMs - observation.timestampMs > policy.maxAgeMs) {
    return { accepted: false, reason: 'stale_or_future_data' };
  }
  return { accepted: true, observation };
}
