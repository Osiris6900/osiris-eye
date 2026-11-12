import { useEffect, useRef, useState } from 'react';
import { amount, Bars, count, Dashboard, object, Provider, readApi, parseBars, rows, textValue, useDashboard } from './dashboardData';
import LiveCryptoTape from './LiveCryptoTape';
import './dashboardWorkspace.css';

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="panel dashboard-panel" aria-label={title}>
    <div className="panel-head"><h2>{title}</h2></div>{children}
  </section>;
}
function Metrics({ values }: { values: [string, string][] }) {
  return <dl className="dashboard-metrics">{values.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}
function PaperAccount({ data }: { data: Dashboard }) {
  const paper = data.paper;
  const verified = paper?.source_ledger === 'canonical_execution' && paper.initialized === true && object(paper.reconciliation)?.passed === true;
  const pnl = verified ? object(paper.pnl) : null;
  return <Panel title="Canonical paper account">
    {data.research_access === 'denied' ? <p>Access denied for this role.</p> : !verified ?
      <p role="status">Verified paper accounting unavailable. Configure a canonical account and resolve reconciliation before balances can be shown.</p> : <>
      <Metrics values={[
        ['Paper cash', amount(paper.cash)], ['Realized P&L', amount(pnl?.realized)],
        ['Unrealized P&L', paper.marks_available === true ? amount(pnl?.unrealized) : 'Unavailable'],
        ['Equity', paper.marks_available === true ? amount(pnl?.equity) : 'Unavailable'],
        ['Market value', paper.marks_available === true ? amount(paper.market_value) : 'Unavailable'], ['Paper fills', count(paper.fill_count)],
      ]}/>
      <p>Ledger reconciliation: PASS · {paper.kill_switch_active === true ? 'Paper execution halted' : 'Paper account only'}</p>
      {paper.marks_available !== true && <p>Fresh marks unavailable. Unrealized P&amp;L and equity are withheld.</p>}
      {typeof paper.valuation_as_of === 'string' && <p>Valuation as of: {paper.valuation_as_of}</p>}
      <div className="dashboard-table"><table><caption>Canonical positions</caption><thead><tr><th>Symbol</th><th>Quantity</th><th>Average cost</th></tr></thead>
        <tbody>{rows(paper.positions).map((position, i) => <tr key={i}><td>{textValue(position.symbol)}</td><td>{amount(position.quantity)}</td><td>{amount(position.average_cost)}</td></tr>)}</tbody></table></div>
      {rows(paper.positions).length === 0 && <p>No paper positions recorded.</p>}
      <div className="dashboard-table"><table><caption>Recent paper fills</caption><thead><tr><th>Symbol</th><th>Side</th><th>Quantity</th><th>Price</th></tr></thead>
        <tbody>{rows(paper.recent_fills).map((fill, i) => <tr key={i}><td>{textValue(fill.symbol)}</td><td>{textValue(fill.side)}</td><td>{amount(fill.quantity)}</td><td>{amount(fill.price)}</td></tr>)}</tbody></table></div>
    </>}
    <p className="muted">Simulated ledger values. No broker account or real money is represented.</p>
  </Panel>;
}
function Risk({ data }: { data: Dashboard }) {
  const verified = data.paper?.source_ledger === 'canonical_execution' && data.paper.initialized === true && object(data.paper.reconciliation)?.passed === true;
  return <Panel title="Risk & alerts">
    {!data.risk ? <p>Access denied for this role.</p> : <>
      <Metrics values={[
        ['Risk flags', count(data.risk.flag_count)], ['Marked exposure', verified && data.paper?.marks_available === true ? amount(data.risk.market_value) : 'Unavailable'],
        ['Cost basis gross', verified ? amount(data.risk.cost_basis_gross) : 'Unavailable'], ['Risk gate', textValue(data.risk.risk_gate)],
      ]}/>
      {rows(data.risk.flags).map((flag, i) => <p key={i}><strong>{textValue(flag.severity)}:</strong> {textValue(flag.message)}</p>)}
      {data.risk.flag_count === 0 && <p>No flags reported by this snapshot.</p>}
      <p className="muted">Derived backend flags; independent risk approval and a live risk score are not available.</p>
    </>}
  </Panel>;
}
function Feeds({ data }: { data: Dashboard }) {
  return <Panel title="Feed quality & sources">
    <Metrics values={[
      ['Registered feed entitlements', count(data.feeds.adapter_count)],
      ['Suspended feeds', count(data.feeds.suspended_count)], ['Stale quality reviews', count(data.feeds.stale_review_count)],
    ]}/>
    {rows(data.feeds.adapters).map((feed, i) => <p key={i}>{textValue(feed.adapter)} · {feed.suspended === true ? 'Suspended' : 'Registered'} · quality review {object(feed.quality_review)?.fresh === true ? 'fresh' : 'stale or unavailable'}</p>)}
    <div className="dashboard-table"><table><caption>Configured market adapters</caption><thead><tr><th>Provider</th><th>Source mode</th><th>Asset classes</th><th>Request capability</th></tr></thead>
      <tbody>{data.providers.map(p => <tr key={p.provider}><td>{p.provider}</td><td>{p.mode}</td><td>{p.supported_asset_classes.join(', ')}</td><td>{p.available ? 'Configured' : 'Unavailable'}</td></tr>)}</tbody></table></div>
    <p className="muted">Adapter configuration does not verify upstream connectivity, real-time coverage or redistribution rights.</p>
  </Panel>;
}
function Network({ data }: { data: Dashboard }) {
  const node = object(data.network.local_live_node), peers = object(data.network.peer_health), outbox = object(data.network.outbox);
  const captured = data.peer_metrics?.captured_ms, report = object(data.peer_metrics?.report);
  const recent = typeof captured === 'number' && Number.isSafeInteger(captured) &&
    captured <= Date.now() && Date.now() - captured <= 60_000 && report?.schema === 'osiris.peer-metrics.v1' &&
    report.scope === 'local-node-process' && report.latency_kind === 'verified_receipt_admission_ack';
  return <Panel title="Network telemetry">
    <Metrics values={[
      ['Local node', textValue(node?.status)], ['Telemetry source', textValue(data.network.peer_telemetry)],
      ['Enrolled peers', peers?.status === 'enrolled_registry' ? count(peers.peer_count) : 'Unavailable'],
      ['Local connected peers', node?.status === 'observed' ? count(node.connected_peers) : 'Unavailable'],
      ['Pending observations', outbox?.available === true ? count(outbox.pending) : 'Unavailable'],
      ['P2P ingestion', typeof data.network.ingestion_enabled === 'boolean' ? data.network.ingestion_enabled ? 'Enabled' : 'Disabled' : 'Unavailable'],
    ]}/>
    {recent ? <>
      <p>Local peer report captured: {new Date(captured).toISOString()}</p>
      <div className="dashboard-table"><table><caption>Recorded local peer metrics</caption><thead><tr><th>Peer</th><th>Connected at capture</th><th>Verified receipts</th><th>Timeouts</th><th>Disconnects</th></tr></thead>
        <tbody>{rows(report.peers).map((peer, i) => <tr key={i}><td>{textValue(peer.peer_id)}</td><td>{typeof peer.connected === 'boolean' ? peer.connected ? 'Yes' : 'No' : 'Unavailable'}</td><td>{count(peer.cryptographically_verified_receipts)}</td><td>{count(peer.unconfirmed_timeouts)}</td><td>{count(peer.disconnects)}</td></tr>)}</tbody></table></div>
    </> : <p>Recent per-peer telemetry unavailable. Stored or uncollected metrics are not shown as current.</p>}
    <p className="muted">Enrollment is not a live peer count. Independent-host connectivity and acceptance remain unverified.</p>
  </Panel>;
}
function Signals({ data }: { data: Dashboard }) {
  const pipeline = data.pipeline, strategy = object(pipeline?.strategy), intent = object(pipeline?.intent);
  return <Panel title="Recorded research signal">
    {!pipeline ? <p>Access denied for this role.</p> : !pipeline.instrument || pipeline.proposal === 'none' ?
      <p>No recorded research signal available. Run an authorized paper research cycle before a signal can be shown.</p> : <>
      <Metrics values={[
        ['Instrument', textValue(pipeline.instrument)], ['Source', textValue(pipeline.source)],
        ['Decision time', textValue(pipeline.timestamp)], ['Feed state now', textValue(pipeline.feed)],
        ['Research proposal', textValue(pipeline.proposal)], ['Risk state now', textValue(pipeline.risk)],
        ['Paper order', textValue(pipeline.order)], ['Direction', textValue(strategy?.side ?? intent?.side)],
        ['Strategy', textValue(strategy?.id)], ['Signal type', textValue(strategy?.type)],
      ]}/>
      <p>{textValue(pipeline.detail, 'Historical paper decision only.')}</p>
      {pipeline.feed !== 'valid' && <p className="amber">The recorded signal has no fresh feed. It cannot authorize a new order.</p>}
    </>}
    <p className="muted">Research output only. Confidence, expected return and AI validation are unavailable unless independently evidenced.</p>
  </Panel>;
}

function MarketBars({ authorization, providers }: { authorization: string; providers: Provider[] }) {
  const [provider, setProvider] = useState('coinbase-public');
  const [symbol, setSymbol] = useState('BTC-USD');
  const [assetClass, setAssetClass] = useState('crypto');
  const [windowHours, setWindowHours] = useState('1');
  const [data, setData] = useState<Bars | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0), active = useRef<AbortController | null>(null);
  const selected = providers.find(p => p.provider === provider);
  useEffect(() => {
    generation.current += 1; active.current?.abort(); setData(null); setError(''); setLoading(false);
    return () => { generation.current += 1; active.current?.abort(); };
  }, [authorization, provider, symbol, windowHours, assetClass]);
  async function load() {
    if (!selected?.available) return;
    const current = ++generation.current;
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setData(null); setError(''); setLoading(true);
    const timeout = setTimeout(() => controller.abort(), 12_000);
    const end = new Date().toISOString(), start = new Date(Date.now() - Number(windowHours) * 3600_000).toISOString();
    const requested = symbol.trim().toUpperCase();
    const params = new URLSearchParams({ provider, symbol: requested, start, end, asset_class: assetClass });
    try {
      const bars = parseBars(await readApi(`/market/bars?${params}`, authorization, controller.signal), provider, requested, start, end);
      if (current === generation.current && !controller.signal.aborted) setData(bars);
    } catch (failure) {
      if (current === generation.current) setError(controller.signal.aborted ? 'Market request timed out.' : failure instanceof Error ? failure.message : 'Market data unavailable.');
    } finally { clearTimeout(timeout); if (current === generation.current) setLoading(false); }
  }
  const bars = data?.bars.slice(-300) ?? [];
  const low = Math.min(...bars.map(b => b.low)), high = Math.max(...bars.map(b => b.high));
  const span = high - low || 1;
  const first = bars[0], last = bars.at(-1);
  const points = bars.map(b => `${20 + 660 * (Date.parse(b.event_time) - Date.parse(first.event_time)) / (Date.parse(last!.event_time) - Date.parse(first.event_time) || 1)},${185 - 160 * (b.close - low) / span}`).join(' ');
  return <Panel title="Market price history">
    <form className="market-query" onSubmit={event => { event.preventDefault(); void load(); }}>
      <label>Provider<select value={provider} onChange={event => {
        setProvider(event.target.value); setAssetClass(providers.find(p => p.provider === event.target.value)?.supported_asset_classes[0] ?? '');
      }}>
        {!selected && <option value={provider}>{provider} · unavailable</option>}
        {providers.map(p => <option key={p.provider} value={p.provider} disabled={!p.available}>{p.provider} · {p.mode}</option>)}
      </select></label>
      <label>Asset class<select value={assetClass} onChange={event => setAssetClass(event.target.value)}>
        {selected?.supported_asset_classes.map(asset => <option key={asset} value={asset}>{asset}</option>)}
      </select></label>
      <label>Symbol<input value={symbol} maxLength={64} required pattern="[A-Za-z0-9._:/-]+" onChange={event => setSymbol(event.target.value)} /></label>
      <label>History window<select value={windowHours} onChange={event => setWindowHours(event.target.value)}>
        <option value="1">Last hour</option><option value="24">Last day</option><option value="720">Last 30 days</option>
      </select></label>
      <button type="submit" disabled={loading || !authorization.trim() || !selected?.available || !selected.supported_asset_classes.includes(assetClass) || !symbol.trim()}>Load price history</button>
    </form>
    <p className="muted">Native provider candles; no fabricated interval or live-price claim. Use provider symbols (BTC-USD, BTCUSDT, AAPL.US). Price history is fetched only on request.</p>
    {loading && <p role="status">Loading market bars…</p>}{error && <p role="alert">{error}</p>}
    {!data && !loading && !error && <p>No price history loaded.</p>}
    {data && <>
      <p>Source: {data.provider} · {data.mode.toUpperCase()} · {data.symbol} · {data.bars.length} bars</p>
      <p>Requested window: {data.window_start} to {data.window_end}</p>
      <p>Entitlement: {data.entitlement.replaceAll('_', ' ')}</p>
      {!last ? <p>No bars returned for this window. No price is available.</p> : <>
        <Metrics values={[
          ['Last recorded close', String(last.close)], ['Bar time (UTC)', last.event_time],
          ['Available at', last.available_at], ['Last bar volume', String(last.volume)],
        ]}/>
        <svg role="img" aria-label={`${data.symbol} recorded closing prices`} viewBox="0 0 720 230" className="dashboard-chart">
          <text x="20" y="14">{high.toPrecision(7)}</text><text x="20" y="214">{low.toPrecision(7)}</text>
          <polyline points={points} fill="none" stroke="#35d6ec" strokeWidth="2"/>
          {bars.length === 1 && <circle cx="20" cy="185" r="4" fill="#35d6ec"/>}
        </svg>
        <p>Chart: last {bars.length} returned bars. History snapshot; refresh manually for a new observation.</p>
        <div className="dashboard-table"><table><caption>Latest 20 provider bars</caption><thead><tr><th>Time (UTC)</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr></thead>
          <tbody>{data.bars.slice(-20).map(bar => <tr key={bar.event_time}><td>{bar.event_time}</td><td>{bar.open}</td><td>{bar.high}</td><td>{bar.low}</td><td>{bar.close}</td><td>{bar.volume}</td></tr>)}</tbody></table></div>
      </>}
    </>}
  </Panel>;
}

export function DashboardWorkspace({ page, authorization, onAuthorization }: {
  page: string; authorization: string; onAuthorization: (value: string) => void;
}) {
  const { data, error, loading, refresh } = useDashboard(authorization);
  const [input, setInput] = useState('');
  const market = ['Dashboard', 'Live Markets', 'Global Markets'].includes(page);
  return <div className="dashboard-workspace">
    <section className="panel dashboard-connection" aria-label="Dashboard connection">
      <form onSubmit={event => { event.preventDefault(); onAuthorization(input.trim()); setInput(''); }}>
        <label htmlFor="dashboard-auth">Dashboard authorization<input id="dashboard-auth" type="password" autoComplete="off" value={input} onChange={event => setInput(event.target.value)} placeholder="Role token or MFA Session" /></label>
        <button disabled={!input.trim()} type="submit">Connect dashboard</button>
      </form>
      <button disabled={!authorization.trim() || loading} onClick={refresh}>Refresh dashboard</button>
      <button disabled={!authorization.trim()} onClick={() => onAuthorization('')}>Disconnect dashboard</button>
      <p>Authorization stays in memory for this tab. Snapshots refresh every 30 seconds; unavailable data is withheld.</p>
      {!authorization.trim() && <p>Connect with configured authorization to load backend data.</p>}
      {loading && <p role="status">Loading dashboard…</p>}{error && <p role="alert">{error}</p>}
      {data && <p>Backend snapshot: {data.generated_at} · Role: {data.role} · Live execution disabled</p>}
    </section>
    {page === 'Live Markets' && <LiveCryptoTape/>}
    {data && <>
      <div className="dashboard-grid">
        {['Dashboard', 'Paper Trading'].includes(page) && <PaperAccount data={data}/>}
        {['Dashboard', 'Risk Management'].includes(page) && <Risk data={data}/>}
        {['Dashboard', 'Global Markets', 'Live Markets'].includes(page) && <Feeds data={data}/>}
        {page === 'Dashboard' && <Network data={data}/>}
        {['Dashboard', 'AI Signals', 'Paper Trading'].includes(page) && <Signals data={data}/>}
      </div>
      {market && data.research_access === 'allowed' && <MarketBars authorization={authorization} providers={data.providers}/>}
      {page === 'Options Chain' && <Panel title="Options chain source">
        <p>Options chain unavailable. No backend chain snapshot or verified expiry/strike quotes are connected.</p>
        <p>Configured option-capable adapters: {data.providers.filter(p => p.supported_asset_classes.includes('option')).map(p => p.provider).join(', ') || 'None'}.</p>
        <p>Adapter capability alone does not establish chain data or exchange entitlement.</p>
      </Panel>}
      {page === 'Market Regime' && <Panel title="Market regime evidence"><p>Market regime unavailable. No timestamped, validated regime classification is connected to this dashboard.</p></Panel>}
      {page === 'News & Sentiment' && <Panel title="News & sentiment sources"><p>News and sentiment unavailable. No licensed, timestamped headline or validated sentiment feed is connected.</p></Panel>}
      {page === 'Dashboard' && <p className="muted">Options, volatility surfaces, regime labels, news and strategy performance appear only when an evidenced source is connected. Research results are available in Reports.</p>}
    </>}
  </div>;
}
