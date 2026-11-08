import { describe, expect, it } from 'vitest';
import { validateMarketObservation, type MarketObservation, type MarketDataPolicy } from './marketDataGate';
const observation: MarketObservation = { symbol: 'NIFTY', source: 'approved-feed', price: 24000, timestampMs: 1000 };
const policy: MarketDataPolicy = { nowMs: 1100, maxAgeMs: 500, trustedSources: ['approved-feed'] };
describe('market observation admission', () => {
  it('accepts a trusted fresh observation', () => expect(validateMarketObservation(observation, policy)).toMatchObject({ accepted: true }));
  it('rejects untrusted sources', () => expect(validateMarketObservation({ ...observation, source: 'unknown' }, policy)).toMatchObject({ accepted: false, reason: 'untrusted_source' }));
  it('rejects stale or future timestamps', () => {
    expect(validateMarketObservation(observation, { ...policy, nowMs: 2000 })).toMatchObject({ accepted: false });
    expect(validateMarketObservation(observation, { ...policy, nowMs: 900 })).toMatchObject({ accepted: false });
  });
  it('rejects nonfinite or nonpositive prices', () => {
    expect(validateMarketObservation({ ...observation, price: Infinity }, policy)).toMatchObject({ accepted: false });
    expect(validateMarketObservation({ ...observation, price: 0 }, policy)).toMatchObject({ accepted: false });
  });
  it('rejects malformed symbols and invalid policy', () => {
    expect(validateMarketObservation({ ...observation, symbol: 'BAD SYMBOL' }, policy)).toMatchObject({ accepted: false });
    expect(validateMarketObservation(observation, { ...policy, maxAgeMs: 0 })).toMatchObject({ accepted: false });
  });
  it('fails closed on malformed runtime payloads', () => {
    expect(validateMarketObservation(null as unknown as MarketObservation, policy)).toMatchObject({ accepted: false, reason: 'invalid_input' });
    expect(validateMarketObservation({ ...observation, symbol: 123 as unknown as string }, policy)).toMatchObject({ accepted: false, reason: 'invalid_symbol' });
    expect(validateMarketObservation({ ...observation, source: {} as unknown as string }, policy)).toMatchObject({ accepted: false, reason: 'untrusted_source' });
    expect(validateMarketObservation(observation, { ...policy, trustedSources: [42] as unknown as string[] })).toMatchObject({ accepted: false, reason: 'invalid_input' });
  });
});
