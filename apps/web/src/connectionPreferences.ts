export type ConnectionPreferences = {
  mode: 'ghost' | 'broker'; data_provider: 'coinbase-public' | 'binance-public' | 'stooq-daily';
  broker: 'none' | 'alpaca-paper';
};
export const connectionDefaults: ConnectionPreferences = { mode: 'ghost', data_provider: 'coinbase-public', broker: 'none' };
export const keylessProviders = [
  ['coinbase-public', 'Coinbase public · crypto'], ['binance-public', 'Binance public · crypto'],
  ['stooq-daily', 'Stooq public · daily equities'],
] as const;

export function parseConnectionPreferences(value: unknown): ConnectionPreferences {
  const p = value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
  if (!p || Object.keys(p).sort().join(',') !== 'broker,data_provider,mode' ||
      !keylessProviders.some(([id]) => id === p.data_provider) ||
      !((p.mode === 'ghost' && p.broker === 'none') || (p.mode === 'broker' && p.broker === 'alpaca-paper'))) {
    throw new Error('Invalid connection preferences.');
  }
  return p as ConnectionPreferences;
}
