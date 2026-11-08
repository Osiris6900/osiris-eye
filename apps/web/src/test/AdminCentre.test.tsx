import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { AdminCentre, scrubForDisplay } from '../AdminCentre';

type MockReply = { status: number; body: unknown };

function reply(status: number, body: unknown): MockReply {
  return { status, body };
}

function stubFetch(routes: Record<string, MockReply>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const mock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length) // most specific route wins
      .find(candidate => url.includes(candidate));
    const route = key ? routes[key] : reply(404, {});
    return { status: route.status, ok: route.status < 300, json: async () => route.body };
  });
  vi.stubGlobal('fetch', mock);
  return calls;
}

const OVERVIEW = {
  schema: 'osiris.admin_overview.v1', generated_at: '2026-11-08T12:00:00Z',
  service: 'osiris-quant-api', api_version: '0.2.0', role: 'operator',
  roles_configured: ['operator'], ingestion_enabled: false,
  feeds: { adapter_count: 1, suspended_count: 0, stale_review_count: 0 },
  paper_initialized: true, active_alert_count: 1, risk_flag_count: 1,
  live_trading_authorized: false,
};
const NETWORK = {
  schema: 'osiris.admin_network_status.v1', generated_at: '2026-11-08T12:00:00Z',
  ingestion_enabled: false, kill_switch_env: 'OSIRIS_P2P_INGEST_ENABLED',
  outbox: { configured: false }, peer_telemetry: 'unavailable_in_quant_api_process',
  live_trading_authorized: false,
};
const FEEDS = {
  schema: 'osiris.admin_feeds_status.v1', generated_at: '2026-11-08T12:00:00Z',
  adapter_count: 1, suspended_count: 0, stale_review_count: 0,
  quality_review_max_age_seconds: 2592000,
  adapters: [{
    adapter: 'synthetic-authorized', entitled: true, suspended: false,
    instruments: ['SYNTH-PERP'],
    quality_review: { status: 'passed', reviewer: 'workstream-178-code-review',
      reviewed_at: '2026-11-08T00:00:00Z', age_seconds: 43200, fresh: true },
  }],
};
const PAPER = {
  schema: 'osiris.admin_paper_status.v1', generated_at: '2026-11-08T12:00:00Z',
  mode: 'synthetic-paper-only', initialized: true, cash: '9800',
  positions: [{ symbol: 'SYNTH-PERP', quantity: '2', average_cost: '100', mark: null }],
  fill_count: 1, pnl: { realized: '0', unrealized: null, equity: null },
  marks_available: false, live_trading_authorized: false,
};
const RISK = {
  schema: 'osiris.admin_risk_status.v1', generated_at: '2026-11-08T12:00:00Z',
  flag_count: 1,
  flags: [{ kind: 'ingestion_disabled', key: 'global', severity: 'warning',
    message: 'P2P ingestion kill switch is disabled' }],
  exposure: 'unavailable_without_marks', risk_gate: 'enforced',
  live_trading_authorized: false,
};
const ALERTS = {
  schema: 'osiris.admin_alert_list.v1',
  active: [{ schema: 'osiris.admin_alert.v1', alert_id: 'ingestion_disabled:global',
    kind: 'ingestion_disabled', severity: 'warning',
    message: 'P2P ingestion kill switch is disabled', status: 'active',
    raised_at: '2026-11-08T12:00:00Z', acknowledged_at: null, acknowledged_by: null }],
  acknowledged: [],
};

function allOk(overrides: Record<string, MockReply> = {}) {
  return {
    '/admin/overview': reply(200, OVERVIEW),
    '/admin/network/status': reply(200, NETWORK),
    '/admin/feeds/status': reply(200, FEEDS),
    '/admin/paper/status': reply(200, PAPER),
    '/admin/risk/status': reply(200, RISK),
    '/admin/alerts': reply(200, ALERTS),
    ...overrides,
  };
}

function connect() {
  fireEvent.change(screen.getByLabelText('Admin bearer token'), {
    target: { value: 'vtk-operator-7c1d55e0' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
}

test('token gate renders first and never persists credentials', () => {
  const mock = vi.fn();
  vi.stubGlobal('fetch', mock);
  render(<AdminCentre />);
  expect(screen.getByLabelText('Admin bearer token')).toBeInTheDocument();
  expect(screen.getByText(/never stored, logged or displayed/)).toBeInTheDocument();
  expect(screen.getByText(/no live trading/i)).toBeInTheDocument();
  expect(mock).not.toHaveBeenCalled();
  expect(window.localStorage.length).toBe(0);
});

test('successful connect loads every section with the bearer token', async () => {
  const calls = stubFetch(allOk());
  render(<AdminCentre />);
  connect();
  expect(await screen.findByText('operator')).toBeInTheDocument();
  expect(calls).toHaveLength(6);
  for (const call of calls) {
    expect(call.url).toContain('/admin/');
    expect((call.init?.headers as Record<string, string>).Authorization)
      .toBe('Bearer vtk-operator-7c1d55e0');
  }
  expect(screen.getAllByText('false').length).toBeGreaterThan(0); // ingestion state
  fireEvent.click(screen.getByRole('button', { name: 'paper' }));
  expect(await screen.findByText('9800')).toBeInTheDocument();
  expect(screen.getByText(/SYNTH-PERP: 2 units/)).toBeInTheDocument();
  expect(screen.getAllByText(/Unavailable without marks/).length).toBeGreaterThan(0);
  expect(screen.getByText('Recent fills unavailable.')).toBeInTheDocument();
  expect(window.localStorage.length).toBe(0);
});

test('authentication failure is surfaced without leaking state', async () => {
  stubFetch({
    '/admin/overview': reply(401, { detail: 'admin_authentication_required' }),
    '/admin/network/status': reply(401, { detail: 'admin_authentication_required' }),
    '/admin/feeds/status': reply(401, { detail: 'admin_authentication_required' }),
    '/admin/paper/status': reply(401, { detail: 'admin_authentication_required' }),
    '/admin/risk/status': reply(401, { detail: 'admin_authentication_required' }),
    '/admin/alerts': reply(401, { detail: 'admin_authentication_required' }),
  });
  render(<AdminCentre />);
  connect();
  expect(await screen.findAllByText(/authentication_failed/)).not.toHaveLength(0);
  expect(screen.queryByText('9800')).not.toBeInTheDocument();
  expect(window.localStorage.length).toBe(0);
});

test('per-section denial does not hide sections the role may read', async () => {
  stubFetch(allOk({
    '/admin/paper/status': reply(403, { detail: 'admin_permission_denied' }),
    '/admin/risk/status': reply(403, { detail: 'admin_permission_denied' }),
  }));
  render(<AdminCentre />);
  connect();
  expect(await screen.findByText('operator')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'paper' }));
  expect(await screen.findByRole('note')).toHaveTextContent('Access denied for this role.');
  fireEvent.click(screen.getByRole('button', { name: 'feeds' }));
  expect(await screen.findByText(/synthetic-authorized/)).toBeInTheDocument();
});

test('secret-shaped fields are scrubbed before rendering', async () => {
  const poisoned = {
    ...FEEDS,
    adapters: [{ ...FEEDS.adapters[0], api_token: 'vtk-should-never-render-99' }],
    dashboard_token: 'vtk-should-never-render-99',
  };
  stubFetch(allOk({ '/admin/feeds/status': reply(200, poisoned) }));
  render(<AdminCentre />);
  connect();
  await screen.findByText('operator');
  fireEvent.click(screen.getByRole('button', { name: 'feeds' }));
  expect(await screen.findByText(/synthetic-authorized/)).toBeInTheDocument();
  expect(screen.queryByText(/vtk-should-never-render-99/)).not.toBeInTheDocument();
  expect(screen.queryByText(/api_token/)).not.toBeInTheDocument();
});

test('network, paper, risk and alerts stay read-only and show supported fields', async () => {
  const calls = stubFetch(allOk({
    '/admin/network/status': reply(200, {
      ...NETWORK,
      outbox: {
        configured: true, available: true, pending: 2, rejected: 1, delivered: 4,
        delivered_meaning: 'local_node_process_not_remote_receipt',
        rejection_reasons: [{ reason: 'observation_stale', count: 1 }],
      },
      peer_health: { status: 'unavailable', reason: 'unavailable_in_quant_api_process', peer_count: null },
    }),
    '/admin/paper/status': reply(200, {
      ...PAPER,
      cost_basis_gross: '200',
      market_value: null,
      open_orders: null,
      recent_fills: [{ side: 'BUY', symbol: 'SYNTH-PERP', quantity: '2', price: '100' }],
    }),
    '/admin/risk/status': reply(200, { ...RISK, cost_basis_gross: '200', exposure: 'unavailable_without_marks' }),
  }));
  render(<AdminCentre />);
  connect();
  expect(await screen.findByText('operator')).toBeInTheDocument();
  expect(screen.getByText('unavailable')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'network' }));
  expect(await screen.findByText(/local node handoff, not remote peer receipts/)).toBeInTheDocument();
  expect(screen.getByText('4')).toBeInTheDocument();
  expect(screen.getByText('observation_stale: 1')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'paper' }));
  expect(await screen.findByText('BUY SYNTH-PERP 2 @ 100')).toBeInTheDocument();
  expect(screen.getByText('200')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'risk' }));
  expect(await screen.findByText(/P2P ingestion kill switch is disabled/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'alerts' }));
  expect(await screen.findByText(/cannot acknowledge alerts/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Ack' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument();
  expect(calls.every(call => call.init?.method === undefined)).toBe(true);
});

test('scrubForDisplay drops credential keys at any depth', () => {
  const cleaned = scrubForDisplay({
    keep: 'value',
    nested: [{ api_token: 'x' }, { deep: { Authorization: 'y' }, fine: 1 }],
  }) as Record<string, unknown>;
  expect(cleaned.keep).toBe('value');
  expect(JSON.stringify(cleaned)).not.toContain('api_token');
  expect(JSON.stringify(cleaned)).not.toContain('Authorization');
  expect(JSON.stringify(cleaned)).toContain('fine');
});
