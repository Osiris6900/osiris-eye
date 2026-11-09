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

test('blank token, loading, errors, empty states and disconnect stay honest', async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  render(<AdminCentre />);
  fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
  expect(fetchMock).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent('Enter an admin bearer token');

  const resolvers: Array<{ url: string; resolve: (value: unknown) => void }> = [];
  vi.stubGlobal('fetch', (input: unknown) => new Promise(resolve => {
    resolvers.push({ url: String(input), resolve });
  }));
  connect();
  expect(await screen.findByText('Loading…')).toBeInTheDocument();
  expect(screen.queryByText('9800')).not.toBeInTheDocument();
  const first = resolvers.splice(0);
  fireEvent.click(screen.getByRole('button', { name: 'Disconnect' }));
  expect(screen.getByLabelText('Admin bearer token')).toBeInTheDocument();
  connect();
  await screen.findByText('Loading…');
  const second = resolvers.splice(0);
  const bodyFor = (url: string, cash: string, role: string) => {
    if (url.includes('/admin/overview')) return { ...OVERVIEW, role };
    if (url.includes('/admin/paper/status')) return { ...PAPER, cash };
    if (url.includes('/admin/alerts')) return {
      active: [], acknowledged: [], durable: false,
      storage: 'vtk-operator-7c1d55e0', active_count: 0, acknowledged_count: 0,
    };
    if (url.includes('/admin/risk/status')) return { ...RISK, flags: [], cost_basis_gross: null };
    if (url.includes('/admin/network/status')) return NETWORK;
    return FEEDS;
  };
  second.forEach(item => item.resolve({
    status: 200, ok: true, json: async () => bodyFor(item.url, '9800', 'operator'),
  }));
  fireEvent.click(screen.getByRole('button', { name: 'alerts' }));
  expect(await screen.findByText('No active alerts.')).toBeInTheDocument();
  expect(screen.getByText('No acknowledged alerts.')).toBeInTheDocument();
  expect(screen.getByText(/Alert durability unavailable/)).toBeInTheDocument();
  expect(screen.getByText(/cannot acknowledge alerts/)).toBeInTheDocument();
  expect(screen.queryByText(/lost on restart/)).not.toBeInTheDocument();
  expect(screen.queryByText('vtk-operator-7c1d55e0')).not.toBeInTheDocument();
  first.forEach(item => item.resolve({
    status: 200, ok: true, json: async () => bodyFor(item.url, '1111', 'viewer'),
  }));
  fireEvent.click(screen.getByRole('button', { name: 'paper' }));
  expect(await screen.findByText('9800')).toBeInTheDocument();
  expect(screen.queryByText('1111')).not.toBeInTheDocument();
  expect(screen.queryByText('viewer')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'risk' }));
  expect(await screen.findByText('No active risk flags.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'network' }));
  expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0);
  expect(screen.queryByText(/^0$/)).not.toBeInTheDocument();
});

test('backend and configuration errors do not render response secrets', async () => {
  stubFetch({
    '/admin/overview': reply(503, { detail: 'admin_not_configured', token: 'vtk-should-never-render-99' }),
    '/admin/network/status': reply(500, { detail: 'vtk-should-never-render-99' }),
    '/admin/feeds/status': reply(500, { detail: 'admin_response_redacted', leaked: 'vtk-should-never-render-99' }),
    '/admin/paper/status': reply(200, PAPER),
    '/admin/risk/status': reply(200, RISK),
    '/admin/alerts': reply(200, ALERTS),
  });
  render(<AdminCentre />);
  connect();
  expect(await screen.findByText(/admin_not_configured/)).toBeInTheDocument();
  expect(screen.queryByText('9800')).not.toBeInTheDocument();
  expect(screen.queryByText(/vtk-should-never-render-99/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'network' }));
  expect(await screen.findByText(/http_500/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'feeds' }));
  expect(await screen.findByText(/admin_response_redacted/)).toBeInTheDocument();
  expect(screen.queryByText(/vtk-should-never-render-99/)).not.toBeInTheDocument();
});

test('enrolled registry lists nodes and hides key material', async () => {
  const trusted = 'ab'.repeat(32);
  const untrusted = 'cd'.repeat(32);
  stubFetch(allOk({
    '/admin/overview': reply(200, {
      ...OVERVIEW, peer_health: 'enrolled_registry', enrolled_peer_count: 2,
    }),
    '/admin/network/status': reply(200, {
      ...NETWORK,
      peer_telemetry: 'identity_enrollment_not_live_sessions',
      peer_health: {
        status: 'enrolled_registry',
        peer_count: 2,
        trusted_count: 1,
        public_untrusted_count: 1,
        nodes_truncated: false,
        nodes: [
          {
            node_id: trusted, trust_level: 'trusted',
            enrolled_at: '2026-11-08T12:00:00+00:00', public_key: 'should-not-render',
          },
          {
            node_id: untrusted, trust_level: 'public_untrusted',
            enrolled_at: '2026-11-08T12:01:00+00:00',
          },
          { node_id: 'not-a-node', trust_level: 'token=supersecretvalue', enrolled_at: 'nope' },
        ],
      },
    }),
  }));
  render(<AdminCentre />);
  connect();
  expect(await screen.findByText('Enrolled registry')).toBeInTheDocument();
  expect(screen.getByText('2')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'network' }));
  expect(await screen.findByText(/not a live session or health probe/)).toBeInTheDocument();
  expect(screen.getByText(new RegExp(`trusted · ${trusted}`))).toBeInTheDocument();
  expect(screen.getByText(new RegExp(`public untrusted · ${untrusted}`))).toBeInTheDocument();
  expect(screen.queryByText(/should-not-render/)).not.toBeInTheDocument();
  expect(screen.queryByText(/supersecretvalue/)).not.toBeInTheDocument();
  expect(screen.queryByText(/not-a-node/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Revoke' })).not.toBeInTheDocument();
});

test('durable alert history is distinct from an empty store', async () => {
  stubFetch(allOk({
    '/admin/alerts': reply(200, {
      storage: 'sqlite', durable: true, initialized: true,
      active_count: 1, acknowledged_count: 0,
      active: [{
        alert_id: 'ingestion_disabled:global', severity: 'warning',
        message: 'P2P ingestion kill switch is disabled',
        raised_at: '2026-11-08T12:00:00+00:00',
        acknowledged_by: 'token=should-not-render',
      }],
      acknowledged: [],
    }),
  }));
  render(<AdminCentre />);
  connect();
  await screen.findByText('operator');
  fireEvent.click(screen.getByRole('button', { name: 'alerts' }));
  expect(await screen.findByText(/kept in the local database across restarts/)).toBeInTheDocument();
  expect(screen.getByText(/P2P ingestion kill switch is disabled/)).toBeInTheDocument();
  expect(screen.getByText(/cannot acknowledge alerts/)).toBeInTheDocument();
  expect(screen.queryByText(/should-not-render/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Ack' })).not.toBeInTheDocument();
});

test('uninitialized paper is distinct from an empty position book', async () => {
  stubFetch(allOk({
    '/admin/paper/status': reply(200, { ...PAPER, initialized: false, cash: null, positions: [], recent_fills: [] }),
  }));
  render(<AdminCentre />);
  connect();
  await screen.findByText('operator');
  fireEvent.click(screen.getByRole('button', { name: 'paper' }));
  expect(await screen.findByText(/Paper ledger not initialized/)).toBeInTheDocument();
  expect(screen.queryByText('9800')).not.toBeInTheDocument();
  expect(screen.queryByText('No open paper positions.')).not.toBeInTheDocument();
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
  const redacted = scrubForDisplay({ note: 'leak vtk-operator-7c1d55e0' }, 'vtk-operator-7c1d55e0');
  expect(JSON.stringify(redacted)).not.toContain('vtk-operator-7c1d55e0');
  expect(JSON.stringify(redacted)).toContain('[redacted]');
});
