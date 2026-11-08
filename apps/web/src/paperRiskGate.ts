/** Pure, fail-closed validation for research-only paper order proposals. */
export type PaperOrder = { symbol: string; quantity: number; price: number; orderId: string };
export type PaperLimits = { maxOrderNotional: number; maxQuantity: number; killSwitch: boolean; dataTimestampMs: number; nowMs: number; maxDataAgeMs: number };
export type GateResult = { allowed: true; notional: number } | { allowed: false; reason: string };

export function validatePaperOrder(order: PaperOrder, limits: PaperLimits): GateResult {
  if (!order || !limits || typeof order.symbol !== 'string' || typeof order.orderId !== 'string' || typeof limits.killSwitch !== 'boolean') return { allowed: false, reason: 'invalid_input' };
  if (limits.killSwitch) return { allowed: false, reason: 'kill_switch' };
  if (!order.symbol || !/^[A-Z0-9._:-]{1,32}$/.test(order.symbol) || !order.orderId.trim()) return { allowed: false, reason: 'invalid_identity' };
  const numbers = [order.quantity, order.price, limits.maxOrderNotional, limits.maxQuantity, limits.dataTimestampMs, limits.nowMs, limits.maxDataAgeMs];
  if (numbers.some(n => !Number.isFinite(n))) return { allowed: false, reason: 'non_finite_input' };
  if (order.quantity <= 0 || order.price <= 0 || limits.maxOrderNotional <= 0 || limits.maxQuantity <= 0 || limits.maxDataAgeMs <= 0 || limits.dataTimestampMs < 0 || limits.nowMs < 0) return { allowed: false, reason: 'invalid_limit' };
  if (limits.dataTimestampMs > limits.nowMs || limits.nowMs - limits.dataTimestampMs > limits.maxDataAgeMs) return { allowed: false, reason: 'stale_or_future_data' };
  const notional = order.quantity * order.price;
  if (!Number.isFinite(notional) || order.quantity > limits.maxQuantity || notional > limits.maxOrderNotional) return { allowed: false, reason: 'exceeds_limit' };
  return { allowed: true, notional };
}
