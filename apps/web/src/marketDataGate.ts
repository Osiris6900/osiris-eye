/** Research-only validation; never authorizes live execution. */
export type MarketObservation = { symbol: string; source: string; price: number; timestampMs: number };
export type MarketDataPolicy = { nowMs: number; maxAgeMs: number; trustedSources: readonly string[] };
export type MarketDataResult = { accepted: true; observation: MarketObservation } | { accepted: false; reason: string };

export function validateMarketObservation(observation: MarketObservation, policy: MarketDataPolicy): MarketDataResult {
  if (!observation || !policy || !Array.isArray(policy.trustedSources)) return { accepted: false, reason: 'invalid_input' };
  if (!/^[A-Z0-9._:-]{1,32}$/.test(observation.symbol ?? '')) return { accepted: false, reason: 'invalid_symbol' };
  if (!observation.source || !policy.trustedSources.includes(observation.source)) return { accepted: false, reason: 'untrusted_source' };
  if (![observation.price, observation.timestampMs, policy.nowMs, policy.maxAgeMs].every(Number.isFinite) || observation.price <= 0 || observation.timestampMs < 0 || policy.nowMs < 0 || policy.maxAgeMs <= 0) return { accepted: false, reason: 'invalid_numeric_input' };
  if (observation.timestampMs > policy.nowMs || policy.nowMs - observation.timestampMs > policy.maxAgeMs) return { accepted: false, reason: 'stale_or_future_data' };
  return { accepted: true, observation };
}
