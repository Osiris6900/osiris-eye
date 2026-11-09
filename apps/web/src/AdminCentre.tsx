import React, { useCallback, useRef, useState } from 'react';

/**
 * Authenticated read-only admin control centre (issue #180).
 *
 * Authorization is enforced server-side; this UI only presents what the API
 * returns. The bearer token lives in component state (memory) only — it is
 * never written to localStorage, cookies or the URL. Secret-shaped fields are
 * scrubbed before rendering as defense in depth. Research and paper
 * operations only: no live trading control exists here or in the API.
 */

export type JsonObject = Record<string, unknown>;
export type SectionStatus = 'idle' | 'loading' | 'ok' | 'denied' | 'error';
export type Section = { status: SectionStatus; data?: JsonObject };

export const SECTION_PATHS = {
  overview: '/admin/overview',
  network: '/admin/network/status',
  feeds: '/admin/feeds/status',
  paper: '/admin/paper/status',
  risk: '/admin/risk/status',
  alerts: '/admin/alerts',
} as const;
export type SectionKey = keyof typeof SECTION_PATHS;

const SECRET_MARKERS = [
  'token', 'secret', 'password', 'private_key', 'public_key', 'authorization',
  'credential', 'api_key', 'apikey', 'session',
];

/** Recursively drop credential-shaped keys and the in-memory bearer token. */
export function scrubForDisplay(value: unknown, secret?: string): unknown {
  if (typeof value === 'string') {
    if (secret && secret.length >= 8 && value.includes(secret)) {
      return value.split(secret).join('[redacted]');
    }
    return value;
  }
  if (Array.isArray(value)) return value.map(item => scrubForDisplay(item, secret));
  if (value && typeof value === 'object') {
    const out: JsonObject = {};
    for (const [key, item] of Object.entries(value as JsonObject)) {
      const lowered = key.toLowerCase();
      if (SECRET_MARKERS.some(marker => lowered.includes(marker))) continue;
      out[key] = scrubForDisplay(item, secret);
    }
    return out;
  }
  return value;
}

const SAFE_ERROR_DETAILS = new Set(['admin_response_redacted']);

function errorDetail(status: number, body: unknown): string {
  if (status === 401) return 'authentication_failed';
  if (status === 503) return 'admin_not_configured';
  const detail = body && typeof body === 'object' ? (body as JsonObject).detail : undefined;
  if (typeof detail === 'string' && SAFE_ERROR_DETAILS.has(detail)) return detail;
  return `http_${status}`;
}

function countText(value: unknown): string {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? String(value) : 'Unavailable';
}

function countOrUnavailable(outbox: JsonObject | undefined, key: string): string {
  if (!outbox || outbox.configured !== true || outbox.available !== true) return 'Unavailable';
  const value = outbox[key];
  return typeof value === 'number' ? String(value) : 'Unavailable';
}

function rejectionReasons(outbox: JsonObject | undefined): Array<{ reason: string; count: number }> {
  if (!outbox || outbox.available !== true || !Array.isArray(outbox.rejection_reasons)) return [];
  return outbox.rejection_reasons.flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const row = item as JsonObject;
    if (typeof row.reason !== 'string' || typeof row.count !== 'number') return [];
    if (!/^[a-z0-9_:-]{1,80}$/.test(row.reason)) return [];
    return [{ reason: row.reason, count: row.count }];
  });
}

const PEER_UNAVAILABLE_COPY: Record<string, string> = {
  not_configured: 'Identity registry is not configured. No peer list is shown.',
  registry_file_absent: 'Identity registry file is absent. No peer list is shown.',
  registry_schema_absent: 'Identity registry has no enrolled-node table.',
  registry_unreadable: 'Identity registry could not be read.',
  unavailable_in_quant_api_process: 'Peer count is not measured in this process, so none is shown.',
};

function peerUnavailableCopy(health: JsonObject | undefined): string {
  const reason = typeof health?.reason === 'string' ? health.reason : 'unavailable_in_quant_api_process';
  return PEER_UNAVAILABLE_COPY[reason] ?? 'Peer registry unavailable. No peer list is shown.';
}

const TRUST_LABELS: Record<string, string> = {
  trusted: 'trusted',
  public_untrusted: 'public untrusted',
  unclassified: 'unclassified',
};

function peerNodes(health: JsonObject | undefined): Array<{ id: string; trust: string; enrolled: string }> {
  if (!health || health.status !== 'enrolled_registry' || !Array.isArray(health.nodes)) return [];
  return health.nodes.flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const row = item as JsonObject;
    if (typeof row.node_id !== 'string' || !/^[0-9a-f]{64}$/.test(row.node_id)) return [];
    const trust = typeof row.trust_level === 'string' ? TRUST_LABELS[row.trust_level] : undefined;
    if (!trust) return [];
    const enrolled = typeof row.enrolled_at === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(row.enrolled_at)
      ? row.enrolled_at
      : 'time unavailable';
    return [{ id: row.node_id, trust, enrolled }];
  });
}

function registryCount(health: JsonObject | undefined, key: string): string {
  if (!health || health.status !== 'enrolled_registry') return 'Unavailable';
  const value = health[key];
  return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? String(value) : 'Unavailable';
}

function fillRows(paper: JsonObject | undefined): Array<{ side: string; symbol: string; quantity: string; price: string }> {
  if (!paper || !Array.isArray(paper.recent_fills)) return [];
  return paper.recent_fills.flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const row = item as JsonObject;
    if (typeof row.side !== 'string' || typeof row.symbol !== 'string') return [];
    if (typeof row.quantity !== 'string' || typeof row.price !== 'string') return [];
    return [{ side: row.side, symbol: row.symbol, quantity: row.quantity, price: row.price }];
  });
}

async function fetchSection(apiBase: string, path: string, token: string): Promise<Section> {
  try {
    const response = await fetch(`${apiBase}${path}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (response.status === 403) return { status: 'denied' };
    if (!response.ok) {
      let body: unknown = null;
      try { body = await response.json(); } catch { body = null; }
      return { status: 'error', data: { detail: errorDetail(response.status, body) } };
    }
    return { status: 'ok', data: scrubForDisplay(await response.json(), token) as JsonObject };
  } catch {
    return { status: 'error', data: { detail: 'backend_unavailable' } };
  }
}

function Denied() {
  return <p role="note" className="notice">Access denied for this role.</p>;
}
function SectionError({ detail }: { detail?: string }) {
  return <p role="alert" className="notice">Section unavailable: {detail || 'unknown error'}</p>;
}

export function AdminCentre({ apiBase = '/api' }: { apiBase?: string }) {
  const [tokenInput, setTokenInput] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [sections, setSections] = useState<Record<SectionKey, Section>>(() => ({
    overview: { status: 'idle' }, network: { status: 'idle' }, feeds: { status: 'idle' },
    paper: { status: 'idle' }, risk: { status: 'idle' }, alerts: { status: 'idle' },
  }));
  const [view, setView] = useState<SectionKey>('overview');
  const [gateNotice, setGateNotice] = useState('');
  const requestId = useRef(0);

  const load = useCallback(async (bearer: string) => {
    const id = ++requestId.current;
    setSections(prev => {
      const next = { ...prev };
      (Object.keys(next) as SectionKey[]).forEach(key => { next[key] = { status: 'loading' }; });
      return next;
    });
    const entries = await Promise.all(
      (Object.entries(SECTION_PATHS) as [SectionKey, string][]).map(
        async ([key, path]) => [key, await fetchSection(apiBase, path, bearer)] as const,
      ),
    );
    if (id !== requestId.current) return;
    setSections(Object.fromEntries(entries) as Record<SectionKey, Section>);
  }, [apiBase]);

  const connect = (event: React.FormEvent) => {
    event.preventDefault();
    const next = tokenInput.trim();
    if (!next) {
      setGateNotice('Enter an admin bearer token. It stays in memory for this page only.');
      return;
    }
    setGateNotice('');
    setToken(next);
    setTokenInput('');
    void load(next);
  };
  const disconnect = () => {
    requestId.current += 1;
    setToken(null);
    setSections({
      overview: { status: 'idle' }, network: { status: 'idle' }, feeds: { status: 'idle' },
      paper: { status: 'idle' }, risk: { status: 'idle' }, alerts: { status: 'idle' },
    });
  };

  const overview = sections.overview.data;
  const network = sections.network.data;
  const feeds = sections.feeds.data;
  const paper = sections.paper.data;
  const risk = sections.risk.data;
  const alerts = sections.alerts.data;
  const adapters = Array.isArray(feeds?.adapters) ? feeds.adapters as JsonObject[] : null;
  const flags = Array.isArray(risk?.flags) ? risk.flags as JsonObject[] : null;
  const activeAlerts = Array.isArray(alerts?.active) ? alerts.active as JsonObject[] : null;
  const acknowledgedAlerts = Array.isArray(alerts?.acknowledged) ? alerts.acknowledged as JsonObject[] : null;
  const positions = Array.isArray(paper?.positions) ? paper.positions as JsonObject[] : null;
  const outbox = (network?.outbox as JsonObject | undefined);
  const peerHealth = network?.peer_health as JsonObject | undefined;
  const enrolledNodes = peerNodes(peerHealth);
  const role = typeof overview?.role === 'string' ? overview.role : null;

  const renderSection = (key: SectionKey, content: React.ReactNode) => {
    const section = sections[key];
    if (section.status === 'loading') return <p>Loading…</p>;
    if (section.status === 'idle') return <p>Not loaded.</p>;
    if (section.status === 'denied') return <Denied />;
    if (section.status === 'error') {
      return <SectionError detail={String(section.data?.detail || '')} />;
    }
    return <>{content}</>;
  };

  if (!token) {
    return <div className="shell"><main className="main" style={{ maxWidth: 720 }}>
      <div className="top"><div>
        <div className="eyebrow">OSIRIS EYE / Admin</div>
        <h1>Control Centre</h1>
        <p>Authenticated read-only operations dashboard</p>
      </div><div className="pill">◉ Awaiting authenticated API</div></div>
      <section className="panel" aria-label="Admin authentication">
        <h3>Operator authentication</h3>
        <form onSubmit={connect}>
          <label htmlFor="admin-token">Admin bearer token</label>
          <input id="admin-token" type="password" autoComplete="off"
            value={tokenInput} onChange={event => setTokenInput(event.target.value)}
            style={{ display: 'block', width: '100%', margin: '10px 0', padding: 10 }} />
          <button className="button" type="submit">Connect</button>
        </form>
        {gateNotice && <p role="alert">{gateNotice}</p>}
        <p className="notice">The token is held in memory only and is never stored, logged or displayed.
          Authorization is enforced by the API for every request.</p>
        <p className="muted">Research and paper operations only — no live trading. No fabricated metrics:
          sections show exactly what the authenticated API returns.</p>
      </section>
    </main></div>;
  }

  return <div className="shell">
    <aside className="side">
      <div className="logo">◉ OSIRIS <span>EYE</span></div>
      <div className="sub">CONTROL CENTRE</div>
      <nav aria-label="Admin sections">
        {(Object.keys(SECTION_PATHS) as SectionKey[]).map(key => (
          <button key={key} aria-current={view === key ? 'page' : undefined}
            onClick={() => setView(key)}>{key}</button>
        ))}
      </nav>
      <p className="muted">Role: <span className="status">{role || '…'}</span></p>
      <button className="button" onClick={() => void load(token)}>Refresh</button>
      <button className="button" onClick={disconnect} style={{ marginTop: 8 }}>Disconnect</button>
    </aside>
    <main className="main">
      <div className="top"><div>
        <div className="eyebrow">Authenticated / read-only / paper</div>
        <h1>{view}</h1>
      </div><div className="pill">◉ No live trading</div></div>

      {view === 'overview' && <section className="panel" aria-label="Overview">
        {renderSection('overview', <div className="settings">
          <div className="setting"><span>Ingestion kill switch</span><span>{String(overview?.ingestion_enabled)}</span></div>
          <div className="setting"><span>Feed adapters (suspended)</span><span>
            {String((overview?.feeds as JsonObject | undefined)?.adapter_count ?? '—')} ({String((overview?.feeds as JsonObject | undefined)?.suspended_count ?? '—')})
          </span></div>
          <div className="setting"><span>Stale quality reviews</span><span>{String((overview?.feeds as JsonObject | undefined)?.stale_review_count ?? '—')}</span></div>
          <div className="setting"><span>Paper ledger initialized</span><span>{String(overview?.paper_initialized)}</span></div>
          <div className="setting"><span>Active alerts</span><span>{String(overview?.active_alert_count ?? '—')}</span></div>
          <div className="setting"><span>Risk flags</span><span>{String(overview?.risk_flag_count ?? '—')}</span></div>
          <div className="setting"><span>Live trading authorized</span><span>{String(overview?.live_trading_authorized)}</span></div>
          <div className="setting"><span>Peer health</span><span>{overview?.peer_health === 'enrolled_registry' ? 'Enrolled registry' : 'unavailable'}</span></div>
          <div className="setting"><span>Enrolled peers</span><span>{overview?.peer_health === 'enrolled_registry' && typeof overview?.enrolled_peer_count === 'number' ? String(overview.enrolled_peer_count) : 'Unavailable'}</span></div>
          <div className="setting"><span>Outbox pending</span><span>{overview?.outbox_pending == null ? 'Unavailable' : String(overview.outbox_pending)}</span></div>
        </div>)}
      </section>}

      {view === 'network' && <section className="panel" aria-label="Network">
        {renderSection('network', <>
          {peerHealth?.status === 'enrolled_registry' ? <>
            <p className="muted">Enrolled identities from the local registry. This is not a live session or health probe.</p>
            <div className="settings">
              <div className="setting"><span>Enrolled nodes</span><span>{registryCount(peerHealth, 'peer_count')}</span></div>
              <div className="setting"><span>Trusted</span><span>{registryCount(peerHealth, 'trusted_count')}</span></div>
              <div className="setting"><span>Public untrusted</span><span>{registryCount(peerHealth, 'public_untrusted_count')}</span></div>
            </div>
            {peerHealth.nodes_truncated === true && <p className="muted">Node list truncated. The enrolled count includes nodes that are not shown.</p>}
            {enrolledNodes.map(node => <p key={node.id} className="muted">{node.trust} · {node.id} · enrolled {node.enrolled}</p>)}
            {Array.isArray(peerHealth.nodes) && enrolledNodes.length === 0 && <p className="muted">No enrolled nodes.</p>}
            {!Array.isArray(peerHealth.nodes) && <p className="muted">Node list unavailable.</p>}
          </> : <p className="muted">{peerUnavailableCopy(peerHealth)}</p>}
          <div className="settings">
            <div className="setting"><span>Ingestion enabled</span><span>{String(network?.ingestion_enabled)}</span></div>
            <div className="setting"><span>Outbox configured</span><span>{String(outbox?.configured ?? false)}</span></div>
            <div className="setting"><span>Outbox pending</span><span>{countOrUnavailable(outbox, 'pending')}</span></div>
            <div className="setting"><span>Outbox rejected</span><span>{countOrUnavailable(outbox, 'rejected')}</span></div>
            <div className="setting"><span>Outbox delivered</span><span>{countOrUnavailable(outbox, 'delivered')}</span></div>
            <div className="setting"><span>Peer telemetry</span><span>{network?.peer_telemetry === 'identity_enrollment_not_live_sessions' ? 'Enrollment registry only' : 'Unavailable'}</span></div>
          </div>
          {outbox?.delivered_meaning === 'local_node_process_not_remote_receipt' && <p className="muted">Delivered counts are local node handoff, not remote peer receipts.</p>}
          <h3>Rejection reasons</h3>
          {rejectionReasons(outbox).map(reason => <p key={reason.reason} className="muted">{reason.reason}: {reason.count}</p>)}
          {rejectionReasons(outbox).length === 0 && <p className="muted">{outbox?.available === true ? 'No rejected messages.' : 'Rejection reasons unavailable.'}</p>}
        </>)}
      </section>}

      {view === 'feeds' && <section className="panel" aria-label="Feeds">
        {renderSection('feeds', <>
          {adapters === null && <p className="muted">Adapters unavailable.</p>}
          {adapters?.map(adapter => {
            const review = adapter.quality_review as JsonObject | undefined;
            const name = String(adapter.adapter);
            return <div className="setting" key={name}>
              <span>{name} · {((adapter.instruments as string[]) || []).join(', ')} · review {String(review?.status)} ({review?.fresh ? 'fresh' : 'stale'}){adapter.suspended ? ' · SUSPENDED' : ''}</span>
            </div>;
          })}
          {adapters?.length === 0 && <p className="muted">No adapters registered.</p>}
        </>)}
      </section>}

      {view === 'paper' && <section className="panel" aria-label="Paper trading">
        {renderSection('paper', paper?.initialized ? <>
          <p className="muted">Open orders are not stored. Recent rows are synthetic fills only.</p>
          <div className="settings">
            <div className="setting"><span>Mode</span><span>{String(paper?.mode)}</span></div>
            <div className="setting"><span>Cash</span><span>{String(paper?.cash)}</span></div>
            <div className="setting"><span>Synthetic fills</span><span>{String(paper?.fill_count)}</span></div>
            <div className="setting"><span>Cost basis</span><span>{paper?.cost_basis_gross == null ? 'Unavailable' : String(paper.cost_basis_gross)}</span></div>
            <div className="setting"><span>Market value</span><span>{paper?.market_value == null ? 'Unavailable without marks' : String(paper.market_value)}</span></div>
            <div className="setting"><span>Realized P&amp;L</span><span>{String((paper?.pnl as JsonObject | undefined)?.realized)}</span></div>
            <div className="setting"><span>Unrealized P&amp;L</span><span>{String((paper?.pnl as JsonObject | undefined)?.unrealized ?? 'Unavailable without marks')}</span></div>
          </div>
          {positions === null && <p className="muted">Positions unavailable.</p>}
          {positions?.map(position => <p key={String(position.symbol)} className="muted">
            {String(position.symbol)}: {String(position.quantity)} units · average cost {String(position.average_cost)}
          </p>)}
          {positions?.length === 0 && <p className="muted">No open paper positions.</p>}
          <h3>Recent synthetic fills</h3>
          {fillRows(paper).map(fill => <p key={`${fill.side}-${fill.symbol}-${fill.quantity}-${fill.price}`} className="muted">
            {fill.side} {fill.symbol} {fill.quantity} @ {fill.price}
          </p>)}
          {fillRows(paper).length === 0 && <p className="muted">{Array.isArray(paper?.recent_fills) ? 'No synthetic fills recorded.' : 'Recent fills unavailable.'}</p>}
        </> : <p className="muted">Paper ledger not initialized · cash, positions and P&amp;L unavailable</p>)}
      </section>}

      {view === 'risk' && <section className="panel" aria-label="Risk">
        {renderSection('risk', <>
          <div className="settings">
            <div className="setting"><span>Risk gate</span><span>{String(risk?.risk_gate)}</span></div>
            <div className="setting"><span>Exposure</span><span>{risk?.exposure === 'unavailable_without_marks' ? 'Unavailable without marks' : String(risk?.exposure ?? 'Unavailable')}</span></div>
            <div className="setting"><span>Cost basis</span><span>{risk?.cost_basis_gross == null ? 'Unavailable' : String(risk.cost_basis_gross)}</span></div>
          </div>
          {flags === null && <p className="muted">Risk flags unavailable.</p>}
          {flags?.map(flag => <p key={`${flag.kind}:${flag.key}`} className="notice">
            [{String(flag.severity)}] {String(flag.message)}
          </p>)}
          {flags?.length === 0 && <p className="status">No active risk flags.</p>}
        </>)}
      </section>}

      {view === 'alerts' && <section className="panel" aria-label="Alerts">
        {renderSection('alerts', <>
          <p className="muted">Storage: {alerts?.storage === 'sqlite' && alerts?.durable === true
            ? 'local alert database'
            : alerts?.storage === 'process_memory_not_durable' && alerts?.durable === false
              ? 'process memory, not durable' : 'unavailable'}. This view cannot acknowledge alerts.</p>
          <p className="muted">{alerts?.storage === 'sqlite' && alerts?.durable === true
            ? (alerts?.initialized === false
              ? 'No alert has been stored yet. Raised alerts are kept in the local database across restarts.'
              : 'Raised alerts are kept in the local database across restarts. This is not a compliance archive.')
            : alerts?.storage === 'process_memory_not_durable' && alerts?.durable === false
              ? 'Alerts are lost on restart. They are not a durable audit log.'
              : 'Alert durability unavailable.'}</p>
          <p className="muted">Active count: {countText(alerts?.active_count)} · Acknowledged count: {countText(alerts?.acknowledged_count)}</p>
          {activeAlerts === null && <p className="muted">Active alerts unavailable.</p>}
          {activeAlerts?.map(alert => <div className="setting" key={String(alert.alert_id)}>
            <span>[{String(alert.severity)}] {String(alert.message)} · {String(alert.raised_at ?? 'time unavailable')}</span>
          </div>)}
          {activeAlerts?.length === 0 && <p className="status">No active alerts.</p>}
          {acknowledgedAlerts === null && <p className="muted">Acknowledged alerts unavailable.</p>}
          {acknowledgedAlerts?.map(alert => <p key={`ack-${String(alert.alert_id)}`} className="muted">
            Acknowledged: {String(alert.alert_id)} by {String(alert.acknowledged_by)}
          </p>)}
          {acknowledgedAlerts?.length === 0 && <p className="muted">No acknowledged alerts.</p>}
        </>)}
      </section>}

      <p className="notice">Read-only paper dashboard. It does not place orders or change ingestion, feeds, or alerts.
        Live trading is not authorized.</p>
    </main>
  </div>;
}

export default AdminCentre;
