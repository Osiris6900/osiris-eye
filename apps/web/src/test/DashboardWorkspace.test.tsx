import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { useState } from 'react';
import { App } from '../main';
import { DashboardWorkspace } from '../DashboardWorkspace';
import { parseBars, parseDashboard } from '../dashboardData';

vi.mock('../LiveCryptoTape', () => ({ default: () => <section aria-label="Live crypto prices"/> }));
const snapshot = () => ({
  schema: 'osiris.dashboard.v1', generated_at: new Date().toISOString(), role: 'analyst',
  live_trading_authorized: false, research_access: 'allowed',
  network: { ingestion_enabled: false, local_live_node: { status: 'unavailable' }, peer_health: { status: 'unavailable' } },
  feeds: { adapter_count: 0, suspended_count: 0, stale_review_count: 0, adapters: [] },
  providers: [{ provider: 'coinbase-public', mode: 'synthetic', available: true, supported_asset_classes: ['crypto'] }],
  paper: { source_ledger: 'canonical_execution', initialized: true, reconciliation: { passed: true },
    cash: '798', pnl: { realized: '0', unrealized: '18', equity: '1018' }, market_value: '220', marks_available: true,
    fill_count: 1, positions: [{ symbol: 'BTC-USD', quantity: '2', average_cost: '101' }], recent_fills: [] },
  risk: { flag_count: 0, flags: [] as { severity: string; message: string }[], risk_gate: 'enforced', market_value: '220', cost_basis_gross: '202' },
  pipeline: { instrument: 'BTC-USD', proposal: 'approved', risk: 'pass', feed: 'stale', order: 'filled',
    source: 'offline-fixture', timestamp: new Date().toISOString(), strategy: { id: 'momentum-v1', type: 'momentum', side: 'BUY' } },
});
const response = (data: unknown, status = 200) => ({ ok: status === 200, status, json: async () => data });
const fetcher = vi.fn();
beforeEach(() => { fetcher.mockReset(); vi.stubGlobal('fetch', fetcher); localStorage.clear(); sessionStorage.clear(); });

function Connected({ page = 'Dashboard' }: { page?: string }) {
  const [auth, setAuth] = useState('fixture-token');
  return <DashboardWorkspace page={page} authorization={auth} onAuthorization={setAuth}/>;
}

it('never claims connection or shows financial demo values before authorization', () => {
  render(<App/>);
  expect(fetcher).not.toHaveBeenCalled();
  expect(screen.getByText(/Connect with configured authorization/)).toBeInTheDocument();
  for (const value of ['24,198.60', '+₹7,42,360', 'AI Research · Ready', 'Database · Connected', '82%']) {
    expect(screen.queryByText(value)).not.toBeInTheDocument();
  }
});

it('uses shared in-memory authorization and renders canonical values with provenance', async () => {
  fetcher.mockResolvedValue(response(snapshot()));
  render(<App/>);
  fireEvent.change(screen.getByLabelText('Dashboard authorization'), { target: { value: 'fixture-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Connect dashboard' }));
  const account = await screen.findByRole('region', { name: 'Canonical paper account' });
  expect(within(account).getByText('798')).toBeInTheDocument();
  expect(within(account).getByText('1018')).toBeInTheDocument();
  expect(screen.getByText(/Backend snapshot:/)).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledWith('/api/control-plane/dashboard', expect.objectContaining({
    headers: { Authorization: 'Bearer fixture-token' }, cache: 'no-store',
  }));
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Paper Trading' }));
  expect(screen.getByRole('region', { name: 'Canonical paper account' })).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Feed quality & sources' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect dashboard' }));
  expect(screen.queryByText('798')).not.toBeInTheDocument();
});

it.each([401, 403, 503])('clears old account values when refresh fails with HTTP %i', async status => {
  fetcher.mockResolvedValueOnce(response(snapshot())).mockResolvedValueOnce(response({}, status));
  render(<Connected/>);
  await screen.findByText('798');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh dashboard' }));
  expect(screen.queryByText('798')).not.toBeInTheDocument();
  await screen.findByRole('alert');
  expect(screen.queryByText('1018')).not.toBeInTheDocument();
});

it('withholds corrupt account values and unmarked valuations without hiding risk failures', async () => {
  const bad = snapshot();
  bad.paper.reconciliation.passed = false; bad.paper.cash = '999999';
  bad.risk.flags = [{ severity: 'critical', message: 'Paper reconciliation failed' }];
  bad.risk.flag_count = 1;
  fetcher.mockResolvedValue(response(bad));
  const view = render(<Connected/>);
  await screen.findByText(/Verified paper accounting unavailable/);
  expect(screen.queryByText('999999')).not.toBeInTheDocument();
  expect(screen.getByText(/Paper reconciliation failed/)).toBeInTheDocument();
  view.unmount();
  const unmarked = snapshot(); unmarked.paper.marks_available = false;
  fetcher.mockResolvedValue(response(unmarked)); render(<Connected/>);
  const account = await screen.findByRole('region', { name: 'Canonical paper account' });
  expect(within(account).getByText('798')).toBeInTheDocument();
  expect(within(account).queryByText('1018')).not.toBeInTheDocument();
  expect(within(account).queryByText('18')).not.toBeInTheDocument();
});

it('does not accept delayed data from the previous identity after an authorization change', async () => {
  let finish: (value: unknown) => void = () => {};
  fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }))
    .mockResolvedValueOnce(response({}, 403));
  const view = render(<DashboardWorkspace page="Dashboard" authorization="old-token" onAuthorization={() => {}}/>);
  view.rerender(<DashboardWorkspace page="Dashboard" authorization="new-token" onAuthorization={() => {}}/>);
  await screen.findByText('Access denied for this role.');
  await act(async () => { finish(response(snapshot())); });
  expect(screen.queryByText('798')).not.toBeInTheDocument();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});

it('aborts slow requests and reports a timeout instead of retaining balances', async () => {
  vi.useFakeTimers();
  fetcher.mockImplementation((_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  render(<Connected/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(12_001); });
  expect(screen.getByRole('alert')).toHaveTextContent('timed out');
});

it('prevents overlapping polling and clears the previous snapshot when polling starts', async () => {
  vi.useFakeTimers(); fetcher.mockResolvedValue(response(snapshot()));
  render(<Connected/>);
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(screen.getByText('798')).toBeInTheDocument();
  fetcher.mockImplementation(() => new Promise(() => {}));
  await act(async () => { await vi.advanceTimersByTimeAsync(30_001); });
  expect(screen.queryByText('798')).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it('rejects stored stale peer metrics as current and displays recent receipt counters', async () => {
  const metrics = { captured_ms: Date.now(), report: {
    schema: 'osiris.peer-metrics.v1', scope: 'local-node-process', latency_kind: 'verified_receipt_admission_ack',
    peers: [{ peer_id: 'fixture-peer', connected: true, cryptographically_verified_receipts: 7, unconfirmed_timeouts: 2, disconnects: 1 }],
  }};
  fetcher.mockResolvedValue(response({ ...snapshot(), peer_metrics: metrics }));
  const view = render(<Connected/>);
  const table = await screen.findByRole('table', { name: 'Recorded local peer metrics' });
  expect(within(table).getByText('fixture-peer')).toBeInTheDocument();
  expect(within(table).getByText('7')).toBeInTheDocument();
  view.unmount();
  fetcher.mockResolvedValue(response({ ...snapshot(), peer_metrics: { ...metrics, captured_ms: Date.now() - 60_001 } }));
  render(<Connected/>);
  await screen.findByRole('region', { name: 'Network telemetry' });
  expect(screen.queryByText('fixture-peer')).not.toBeInTheDocument();
  expect(screen.getByText(/Recent per-peer telemetry unavailable/)).toBeInTheDocument();
});

it.each([
  ['Options Chain', 'Options chain source', /Options chain unavailable/],
  ['Market Regime', 'Market regime evidence', /Market regime unavailable/],
  ['News & Sentiment', 'News & sentiment sources', /News and sentiment unavailable/],
] as const)('opens the %s view with truthful missing-source state', async (page, region, message) => {
  fetcher.mockResolvedValue(response(snapshot()));
  render(<Connected page={page}/>);
  const section = await screen.findByRole('region', { name: region });
  expect(within(section).getByText(message)).toBeInTheDocument();
  expect(screen.queryByRole('region', { name: 'Canonical paper account' })).not.toBeInTheDocument();
});

it('renders viewer health while withholding all research views and market requests', async () => {
  const data = { ...snapshot(), role: 'viewer', research_access: 'denied', paper: null, risk: null, pipeline: null };
  fetcher.mockResolvedValue(response(data)); render(<Connected/>);
  await screen.findByRole('region', { name: 'Network telemetry' });
  expect(screen.getAllByText('Access denied for this role.')).toHaveLength(3);
  expect(screen.queryByRole('button', { name: 'Load price history' })).not.toBeInTheDocument();
});

function marketResponse(url: string) {
  const params = new URL(url, 'https://local.invalid').searchParams;
  return { provider: params.get('provider'), symbol: params.get('symbol'), mode: 'synthetic',
    entitlement: 'offline_fixture', live_execution: false, window_start: params.get('start'), window_end: params.get('end'), bars_returned: 1,
    bars: [{ symbol: params.get('symbol'), event_time: params.get('start'), available_at: params.get('start'), open: 100, high: 102, low: 99, close: 101, volume: 4 }] };
}
it('loads actual backend bars, identifies synthetic fixtures and clears history when symbol changes', async () => {
  fetcher.mockImplementation((url: string) => Promise.resolve(response(url.includes('/market/bars?') ? marketResponse(url) : snapshot())));
  render(<Connected/>);
  fireEvent.click(await screen.findByRole('button', { name: 'Load price history' }));
  await screen.findByRole('img', { name: 'BTC-USD recorded closing prices' });
  expect(screen.getByText(/SYNTHETIC · BTC-USD/)).toBeInTheDocument();
  expect(screen.getAllByText('101').length).toBeGreaterThan(0);
  fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'ETH-USD' } });
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.getByText('No price history loaded.')).toBeInTheDocument();
});

it('rejects invalid snapshot and bar data, duplicate times, mismatched symbol and future availability', () => {
  expect(() => parseDashboard({ ...snapshot(), live_trading_authorized: true })).toThrow();
  expect(() => parseDashboard({ ...snapshot(), generated_at: '2020-01-01T00:00:00Z' })).toThrow();
  expect(() => parseDashboard({ ...snapshot(), paper: undefined })).toThrow();
  const start = new Date(Date.now() - 3600_000).toISOString(), end = new Date().toISOString();
  const data = marketResponse(`/market/bars?provider=fixture&symbol=BTC-USD&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`);
  for (const invalid of [
    { ...data, symbol: 'OTHER' }, { ...data, live_execution: true },
    { ...data, bars_returned: 2, bars: [...data.bars, ...data.bars] },
    { ...data, bars: [{ ...data.bars[0], close: NaN }] },
    { ...data, bars: [{ ...data.bars[0], high: 1 }] },
    { ...data, bars: [{ ...data.bars[0], available_at: '2100-01-01T00:00:00Z' }] },
  ]) expect(() => parseBars(invalid, 'fixture', 'BTC-USD', start, end)).toThrow();
});

it('hides the current snapshot when a malformed response follows a successful refresh', async () => {
  fetcher.mockResolvedValueOnce(response(snapshot())).mockResolvedValueOnce(response({ role: 'analyst' }));
  render(<Connected/>); await screen.findByText('798');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh dashboard' }));
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Invalid or expired'));
  expect(screen.queryByText('798')).not.toBeInTheDocument();
});
