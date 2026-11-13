import { useEffect, useRef, useState } from 'react';
import { authorizationHeader, object } from './dashboardData';
import { connectionDefaults as defaults, keylessProviders as providers, parseConnectionPreferences, type ConnectionPreferences } from './connectionPreferences';
import './liveTradingSetup.css';

type State = { version: number; preferences: ConnectionPreferences; broker_readiness: string };
function parseState(value: unknown): State {
  const data = object(value);
  if (!data || data.schema !== 'osiris.connection-options.v1' || data.live_execution !== false ||
      data.broker_order_submitted !== false || data.connection_verified !== false ||
      typeof data.version !== 'number' || !Number.isSafeInteger(data.version) || data.version < 0 ||
      !['not_selected', 'disabled', 'credentials_required', 'configured_unverified'].includes(String(data.broker_readiness))) {
    throw new Error('Invalid connection status.');
  }
  const preferences = parseConnectionPreferences(data.preferences);
  if ((preferences.mode === 'ghost') !== (data.broker_readiness === 'not_selected')) throw new Error('Invalid broker readiness.');
  return { version: data.version, preferences, broker_readiness: data.broker_readiness as string };
}
const readiness: Record<string, string> = {
  not_selected: 'No broker selected. Research and internal paper accounting stay independent of broker access.',
  disabled: 'Paper broker access is disabled on the server.',
  credentials_required: 'Configure paper broker credentials on the server, then test the connection.',
  configured_unverified: 'Server setup is present. Test the connection to verify current access.',
};
function same(a: ConnectionPreferences, b: ConnectionPreferences) {
  return a.mode === b.mode && a.broker === b.broker && a.data_provider === b.data_provider;
}

export function TradingConnections({ authorization }: { authorization: string }) {
  const [preferences, setPreferences] = useState<ConnectionPreferences>(defaults);
  const [snapshot, setSnapshot] = useState<{ identity: string; state: State } | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [probe, setProbe] = useState<{ identity: string; version: number; checkedAt: string } | null>(null);
  const [clock, setClock] = useState(Date.now());
  const generation = useRef(0), active = useRef<AbortController | null>(null);
  const identity = useRef(authorization); identity.current = authorization;
  useEffect(() => {
    generation.current += 1; active.current?.abort(); setSnapshot(null); setProbe(null);
    setPending(false); setError(''); setNotice(''); setPreferences(defaults);
    return () => { generation.current += 1; active.current?.abort(); };
  }, [authorization]);
  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const current = snapshot?.identity === authorization ? snapshot.state : null;
  const modified = !current || !same(current.preferences, preferences);
  const probeAge = probe ? clock - Date.parse(probe.checkedAt) : Infinity;
  const verified = probe?.identity === authorization && probe.version === current?.version && !modified && probeAge >= -5000 && probeAge <= 60_000;
  const canWrite = authorization.trim().startsWith('Session ');

  function change(value: ConnectionPreferences) {
    generation.current += 1; active.current?.abort(); setPending(false);
    setPreferences(value); setProbe(null); setError(''); setNotice('');
  }
  async function request(action: 'load' | 'save' | 'test') {
    const id = ++generation.current, actor = authorization;
    active.current?.abort(); const controller = new AbortController(); active.current = controller;
    setPending(true); setError(''); setNotice(''); setProbe(null);
    // An unverified or failed refresh must not leave actionable stale settings.
    if (action === 'load') setSnapshot(null);
    const timeout = setTimeout(() => controller.abort(), 15_000);
    try {
      const response = await fetch(`/api/control-plane/connections${action === 'test' ? '/test' : ''}`, {
        method: action === 'load' ? 'GET' : action === 'save' ? 'PUT' : 'POST',
        headers: { Authorization: authorizationHeader(actor), ...(action === 'load' ? {} : { 'Content-Type': 'application/json' }) },
        ...(action === 'load' ? {} : { body: JSON.stringify(action === 'save'
          ? { preferences, expected_version: current?.version }
          : { expected_version: current?.version }) }),
        signal: controller.signal, cache: 'no-store',
      });
      if (!response.ok) {
        if (response.status === 409) throw new Error('Connection settings changed. Load status before saving or testing again.');
        if (response.status === 401) throw new Error('Authorization expired. Reconnect in Settings.');
        if (response.status === 403) throw new Error('An operator MFA session is required to change or test connections.');
        if (response.status === 503) throw new Error('Connection unavailable. Check the server setup and try again.');
        throw new Error('Connection request failed.');
      }
      const result = await response.json();
      if (generation.current !== id || identity.current !== actor || controller.signal.aborted) return;
      if (action === 'test') {
        const data = object(result), stamp = typeof data?.checked_at === 'string' ? Date.parse(data.checked_at) : NaN;
        if (!data || data.schema !== 'osiris.paper-broker-probe.v1' || data.broker !== 'alpaca-paper' ||
            data.connected !== true || data.read_only !== true || data.live_execution !== false ||
            data.broker_order_submitted !== false || data.version !== current?.version ||
            typeof data.checked_at !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(data.checked_at) ||
            !Number.isFinite(stamp) || Math.abs(Date.now() - stamp) > 60_000) throw new Error('Invalid broker connection result.');
        setProbe({ identity: actor, version: data.version as number, checkedAt: data.checked_at });
        setClock(Date.now());
      } else {
        const state = parseState(result);
        if (action === 'save' && (!same(state.preferences, preferences) || state.version !== (current?.version ?? -1) + 1)) {
          throw new Error('Connection settings changed. Load status again.');
        }
        setSnapshot({ identity: actor, state }); setPreferences(state.preferences);
        setNotice(action === 'save' ? 'Connection options saved on the server. No order was placed.' : 'Server connection options loaded.');
      }
    } catch (failure) {
      if (generation.current === id && identity.current === actor) {
        setSnapshot(null); setProbe(null);
        setError(controller.signal.aborted ? 'Connection request timed out. Load status to retry.' :
          failure instanceof Error ? failure.message : 'Connection unavailable.');
      }
    } finally { clearTimeout(timeout); if (generation.current === id) setPending(false); }
  }

  return <section className="panel live-setup" aria-label="Ghost, keyless and broker connections">
    <div className="panel-head"><h2>Ghost, keyless &amp; broker options</h2><span className="mini-tag">PAPER ONLY</span></div>
    <p>Start with public data and no broker account. Add an optional paper broker when you need it.</p>
    <fieldset disabled={pending} className="connection-modes"><legend>Connection mode</legend>
      <label><input type="radio" name="connection-mode" checked={preferences.mode === 'ghost'}
        onChange={() => change({ ...preferences, mode: 'ghost', broker: 'none' })}/> Ghost / keyless</label>
      <label><input type="radio" name="connection-mode" checked={preferences.mode === 'broker'}
        onChange={() => change({ ...preferences, mode: 'broker', broker: 'alpaca-paper' })}/> Optional paper broker</label>
    </fieldset>
    <div className="live-setup-grid">
      <label htmlFor="connection-provider">Keyless market data
        <select id="connection-provider" disabled={pending} value={preferences.data_provider}
          onChange={event => change({ ...preferences, data_provider: event.target.value as ConnectionPreferences['data_provider'] })}>
          {providers.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
      </label>
      <label htmlFor="connection-broker">Paper broker
        <select id="connection-broker" disabled={pending || preferences.mode === 'ghost'} value={preferences.broker}
          onChange={event => change({ ...preferences, broker: event.target.value as ConnectionPreferences['broker'] })}>
          {preferences.mode === 'ghost' ? <option value="none">None · internal paper account</option> : <option value="alpaca-paper">Alpaca PAPER</option>}
        </select>
      </label>
    </div>
    <p className="muted">Keyless feeds need no vendor API key. Ghost avoids a broker connection; public HTTPS requests are still visible to the data provider. Node authentication remains enabled.</p>
    <p className="muted">Saved feed choices set the default on newly opened market charts. Broker access is tested only when requested; selection never starts trading.</p>
    <div className="live-setup-actions">
      <button disabled={pending || !authorization.trim()} onClick={() => { void request('load'); }}>Load connection status</button>
      <button disabled={pending || !canWrite || !current || !modified} onClick={() => { void request('save'); }}>Apply connection options</button>
      <button disabled={pending || !canWrite || !current || modified || current.preferences.mode !== 'broker' || current.broker_readiness !== 'configured_unverified'}
        onClick={() => { void request('test'); }}>Test paper broker connection</button>
    </div>
    {!authorization.trim() && <p>Use Settings to sign in before loading server connection options. Ghost/keyless is the default.</p>}
    {authorization.trim() && !canWrite && <p>Reading status needs your configured role. Applying options and testing a broker require an operator MFA session.</p>}
    {pending && <p role="status">Checking connection options…</p>}
    {current && <p>Saved mode: {current.preferences.mode === 'ghost' ? 'Ghost / keyless' : 'Optional paper broker'} · {readiness[current.broker_readiness]}</p>}
    {notice && <p role="status">{notice}</p>}
    {verified && <p role="status">Alpaca PAPER connection verified at {probe!.checkedAt}. Read-only account check; no order was placed.</p>}
    {probe && !verified && <p>Previous connection check expired. Test again for current access.</p>}
    {error && <p role="alert" className="down">{error}</p>}
    <p className="muted">Broker secrets stay on the server. These options do not authorize live orders or verify data redistribution rights.</p>
  </section>;
}
