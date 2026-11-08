import { describe, expect, it } from 'vitest';
import { validatePaperOrder, type PaperLimits, type PaperOrder } from './paperRiskGate';
const order: PaperOrder = { symbol: 'NIFTY', quantity: 2, price: 100, orderId: 'paper-1' };
const limits: PaperLimits = { maxOrderNotional: 500, maxQuantity: 5, killSwitch: false, dataTimestampMs: 1000, nowMs: 1100, maxDataAgeMs: 500 };
describe('paper risk gate (research only)', () => {
  it('fails closed on malformed runtime payloads', () => {
    expect(validatePaperOrder(null as unknown as PaperOrder, limits)).toMatchObject({ allowed: false, reason: 'invalid_input' });
    expect(validatePaperOrder(order, null as unknown as PaperLimits)).toMatchObject({ allowed: false, reason: 'invalid_input' });
    expect(validatePaperOrder({ ...order, orderId: null as unknown as string }, limits)).toMatchObject({ allowed: false, reason: 'invalid_input' });
  });
  it('accepts a bounded fresh proposal', () => expect(validatePaperOrder(order, limits)).toEqual({ allowed: true, notional: 200 }));
  it('rejects kill switch', () => expect(validatePaperOrder(order, { ...limits, killSwitch: true })).toMatchObject({ allowed: false, reason: 'kill_switch' }));
  it('rejects stale and future timestamps', () => {
    expect(validatePaperOrder(order, { ...limits, nowMs: 2000 })).toMatchObject({ allowed: false });
    expect(validatePaperOrder(order, { ...limits, nowMs: 900 })).toMatchObject({ allowed: false });
  });
  it('rejects nonfinite and excessive orders', () => {
    expect(validatePaperOrder({ ...order, price: Infinity }, limits)).toMatchObject({ allowed: false });
    expect(validatePaperOrder({ ...order, quantity: 6 }, limits)).toMatchObject({ allowed: false });
    expect(validatePaperOrder({ ...order, price: 300 }, limits)).toMatchObject({ allowed: false });
  });
  it('rejects missing identifiers and invalid limits', () => {
    expect(validatePaperOrder({ ...order, orderId: ' ' }, limits)).toMatchObject({ allowed: false });
    expect(validatePaperOrder(order, { ...limits, maxQuantity: 0 })).toMatchObject({ allowed: false });
  });
});
