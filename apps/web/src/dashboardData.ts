import { useEffect, useState } from 'react';

export type Row = Record<string, unknown>;
export type Provider = { provider: string; mode: string; available: boolean; supported_asset_classes: string[] };
export type Dashboard = {
  schema: 'osiris.dashboard.v1'; generated_at: string; role: string;
  live_trading_authorized: false; research_access: 'allowed' | 'denied';
  network: Row; feeds: Row; paper: Row | null; risk: Row | null; pipeline: Row | null;
  peer_metrics?: Row | null;
  providers: Provider[];
};
export type Bar = { symbol: string; event_time: string; available_at: string; open: number; high: number; low: number; close: number; volume: number };
export type Bars = { provider: string; symbol: string; mode: 'public' | 'synthetic' | 'sandbox'; bars: Bar[]; entitlement: string; window_start: string; window_end: string; live_execution: false };

export function object(value: unknown): Row | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Row : null;
}
export function rows(value: unknown): Row[] {
  return Array.isArray(value) ? value.flatMap(item => object(item) ? [item as Row] : []) : [];
}
export function textValue(value: unknown, fallback = 'Unavailable'): string {
  return typeof value === 'string' && value.trim() ? value : fallback;
}
export function amount(value: unknown): string {
  // Preserve the canonical ledger's decimal precision; reject coercions/NaN/Infinity.
  return typeof value === 'string' && /^-?\d+(?:\.\d+)?(?:[eE][+-]?\d{1,4})?$/.test(value) && value.length <= 300 ? value : 'Unavailable';
}
export function count(value: unknown): string {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? String(value) : 'Unavailable';
}
export function authorizationHeader(value: string): string {
  return /^(Bearer|Session) /.test(value.trim()) ? value.trim() : `Bearer ${value.trim()}`;
}
function instant(value: unknown): number {
  return typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? Date.parse(value) : NaN;
}
export function parseDashboard(value: unknown): Dashboard {
  const data = object(value);
  if (!data || data.schema !== 'osiris.dashboard.v1' || data.live_trading_authorized !== false ||
      typeof data.role !== 'string' || !['allowed', 'denied'].includes(String(data.research_access)) ||
      !object(data.network) || !object(data.feeds) ||
      !Array.isArray(data.providers) || !data.providers.every(item => {
        const p = object(item);
        return p && typeof p.provider === 'string' && typeof p.mode === 'string' && typeof p.available === 'boolean' &&
          Array.isArray(p.supported_asset_classes) && p.supported_asset_classes.every(c => typeof c === 'string');
      }) || !Number.isFinite(instant(data.generated_at)) || Math.abs(Date.now() - instant(data.generated_at)) > 60_000 ||
      (data.research_access === 'allowed' && ![data.paper, data.risk, data.pipeline].every(object)) ||
      (data.research_access === 'denied' && [data.paper, data.risk, data.pipeline].some(item => item !== null))) {
    throw new Error('Invalid or expired dashboard response.');
  }
  return data as Dashboard;
}
export function parseBars(value: unknown, provider: string, symbol: string, start: string, end: string): Bars {
  const data = object(value);
  let previous = -Infinity;
  if (!data || data.provider !== provider || data.symbol !== symbol || data.live_execution !== false ||
      !['public', 'synthetic', 'sandbox'].includes(String(data.mode)) || typeof data.entitlement !== 'string' ||
      data.window_start !== start || data.window_end !== end ||
      !Array.isArray(data.bars) || data.bars.length > 50_000 || data.bars_returned !== data.bars.length ||
      !data.bars.every(value => {
        const bar = object(value);
        if (!bar) return false;
        const stamp = instant(bar.event_time), available = instant(bar.available_at);
        const valid = bar.symbol === symbol && Number.isFinite(stamp) && Number.isFinite(available) &&
          available >= stamp && available <= Date.now() + 5000 && stamp > previous && stamp >= Date.parse(start) && stamp <= Date.parse(end) &&
          [bar.open, bar.high, bar.low, bar.close].every(n => typeof n === 'number' && Number.isFinite(n) && n > 0 && n <= 1e100) &&
          typeof bar.volume === 'number' && Number.isFinite(bar.volume) && bar.volume >= 0 &&
          Number(bar.low) <= Math.min(Number(bar.open), Number(bar.close)) &&
          Number(bar.high) >= Math.max(Number(bar.open), Number(bar.close));
        previous = stamp;
        return valid;
      })) throw new Error('Invalid market bars; chart withheld.');
  return data as Bars;
}

export async function readApi(path: string, authorization: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(`/api/control-plane${path}`, {
    headers: { Authorization: authorizationHeader(authorization) }, signal, cache: 'no-store',
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error('Authorization expired or invalid. Reconnect in Settings.');
    if (response.status === 403) throw new Error('Access denied for this role.');
    if (response.status === 503) throw new Error('Backend or data source unavailable.');
    throw new Error(`Backend request failed (${response.status}).`);
  }
  return response.json();
}

export function useDashboard(authorization: string) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ identity: string; data: Dashboard | null; error: string; loading: boolean; received: number }>(
    { identity: '', data: null, error: '', loading: false, received: 0 });
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(clock);
  }, []);
  useEffect(() => {
    if (!authorization.trim()) return;
    let active = true;
    let controller: AbortController;
    let refresh: ReturnType<typeof setTimeout>;
    let timeout: ReturnType<typeof setTimeout>;
    async function load() {
      controller = new AbortController();
      setState({ identity: authorization, data: null, error: '', loading: true, received: 0 });
      timeout = setTimeout(() => controller.abort(), 12_000);
      try {
        const data = parseDashboard(await readApi('/dashboard', authorization, controller.signal));
        if (active && !controller.signal.aborted) setState({ identity: authorization, data, error: '', loading: false, received: Date.now() });
      } catch (error) {
        if (active) setState({ identity: authorization, data: null, loading: false, received: 0,
          error: controller.signal.aborted ? 'Backend request timed out.' : error instanceof Error ? error.message : 'Backend unavailable.' });
      } finally {
        clearTimeout(timeout);
        if (active) refresh = setTimeout(() => { void load(); }, 30_000);
      }
    }
    void load();
    return () => { active = false; clearTimeout(refresh); clearTimeout(timeout); controller?.abort(); };
  }, [authorization, revision]);
  const current = authorization.trim() && state.identity === authorization;
  const expired = current && state.data !== null && now - state.received > 45_000;
  return {
    data: current && !expired ? state.data : null,
    error: expired ? 'Dashboard snapshot expired. Refresh to reconnect.' : current ? state.error : '',
    loading: !!current && state.loading,
    refresh: () => { setState(s => ({ ...s, data: null, error: '', loading: true })); setRevision(n => n + 1); },
  };
}
