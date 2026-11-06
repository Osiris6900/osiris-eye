import { useEffect, useRef, useState } from 'react';

type MechanicsModule = {
  id: string;
  name: string;
  endpoint: string;
  evidence_strength: string;
  mechanism: string;
  limitations: string[];
  sample_input: Record<string, unknown>;
};

function isModule(value: unknown): value is MechanicsModule {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<MechanicsModule>;
  return typeof item.id === 'string' && typeof item.name === 'string'
    && typeof item.endpoint === 'string'
    && /^\/research\/market-mechanics\/[a-z-]+$/.test(item.endpoint)
    && typeof item.evidence_strength === 'string' && typeof item.mechanism === 'string'
    && Array.isArray(item.limitations) && item.limitations.every(x => typeof x === 'string')
    && !!item.sample_input && typeof item.sample_input === 'object'
    && !Array.isArray(item.sample_input);
}

async function readResponse(response: Response): Promise<unknown> {
  if (response.status === 401 || response.status === 403) {
    throw new Error('API access requires a valid bearer token. Enter it below and apply.');
  }
  const body = await response.json().catch(() => {
    throw new Error('The API did not return JSON. Check that the research API is available.');
  });
  if (!response.ok) {
    const detail = body?.detail;
    if (Array.isArray(detail)) {
      throw new Error(detail.slice(0, 4).map(item =>
        `${Array.isArray(item.loc) ? item.loc.join('.') : 'Input'}: ${item.msg || 'invalid value'}`
      ).join('; '));
    }
    throw new Error(typeof detail === 'string' ? detail : `API request failed (${response.status}).`);
  }
  return body;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The request could not be completed.';
}

export default function MarketMechanics() {
  const [modules, setModules] = useState<MechanicsModule[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [input, setInput] = useState('');
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState('');
  const [retry, setRetry] = useState(0);
  const [tokenDraft, setTokenDraft] = useState('');
  const [token, setToken] = useState('');
  const [pending, setPending] = useState(false);
  const [runError, setRunError] = useState('');
  const [result, setResult] = useState<unknown>(null);
  const request = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const selected = modules.find(item => item.id === selectedId);

  function invalidateRun() {
    generation.current += 1;
    request.current?.abort();
    request.current = null;
    setPending(false);
    setRunError('');
    setResult(null);
  }

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    setCatalogLoading(true);
    setCatalogError('');
    setModules([]);
    setSelectedId('');
    setInput('');
    const headers: HeadersInit = token ? { Authorization: `Bearer ${token}` } : {};
    fetch('/api/research/market-mechanics/catalog', { signal: controller.signal, headers })
      .then(readResponse)
      .then(body => {
        if (disposed) return;
        const items = (body as { modules?: unknown })?.modules;
        if (!Array.isArray(items) || items.length === 0 || !items.every(isModule)) {
          throw new Error('The API returned an invalid market mechanics catalog.');
        }
        setModules(items);
        setSelectedId(items[0].id);
        setInput(JSON.stringify(items[0].sample_input, null, 2));
      })
      .catch(error => {
        if (!disposed) setCatalogError(timedOut ? 'The API timed out. Retry when it is available.' : errorMessage(error));
      })
      .finally(() => {
        clearTimeout(timer);
        if (!disposed) setCatalogLoading(false);
      });
    return () => { disposed = true; clearTimeout(timer); controller.abort(); };
  }, [token, retry]);

  useEffect(() => () => { generation.current += 1; request.current?.abort(); }, []);

  async function runDiagnostic() {
    if (!selected) return;
    invalidateRun();
    let payload: unknown;
    try {
      payload = JSON.parse(input);
      if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
        throw new Error('Input must be a JSON object.');
      }
    } catch (error) {
      setRunError(`Invalid JSON input: ${errorMessage(error)}`);
      return;
    }
    const controller = new AbortController();
    request.current = controller;
    const current = generation.current;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 20000);
    setPending(true);
    try {
      const response = await fetch(`/api${selected.endpoint}`, {
        method: 'POST',
        signal: controller.signal,
        headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify(payload),
      });
      const body = await readResponse(response);
      if (generation.current === current) setResult(body);
    } catch (error) {
      if (generation.current === current) {
        setRunError(timedOut ? 'The diagnostic timed out. Retry with a smaller input.' : errorMessage(error));
      }
    } finally {
      clearTimeout(timer);
      if (generation.current === current) { setPending(false); request.current = null; }
    }
  }

  return <section className="panel mechanics-panel" id="market-mechanics" aria-labelledby="mechanics-title">
    <div className="panel-head">
      <div><h2 id="mechanics-title">Market Mechanics Lab</h2><small>OPTIONS · LIQUIDITY · INSTITUTIONAL FLOWS · CROSS-ASSET</small></div>
      <span className="mini-tag">RESEARCH ONLY</span>
    </div>
    <p className="mechanics-intro">Explore how market structure can affect price movement. Inputs start with synthetic examples; no vendor feed is connected to these diagnostics.</p>
    {catalogLoading && <p role="status" className="mechanics-status">Loading market mechanics…</p>}
    {catalogError && <div className="mechanics-error" role="alert"><p>{catalogError}</p><button type="button" onClick={() => { invalidateRun(); setRetry(value => value + 1); }}>Retry catalog</button></div>}
    {selected && <>
      <div className="mechanics-picker">
        <label htmlFor="mechanics-module">Mechanism · {modules.length} modules</label>
        <select id="mechanics-module" value={selectedId} onChange={event => {
          invalidateRun();
          const next = modules.find(item => item.id === event.target.value);
          if (next) { setSelectedId(next.id); setInput(JSON.stringify(next.sample_input, null, 2)); }
        }}>{modules.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
        <span className="mechanics-evidence">Evidence: {selected.evidence_strength.replace(/_/g, ' ')}</span>
      </div>
      <p className="mechanics-description">{selected.mechanism}</p>
      <details className="mechanics-limitations" open><summary>Interpretation & limitations</summary><ul>{selected.limitations.map((item, index) => <li key={index}>{item}</li>)}</ul></details>
      <div className="mechanics-workbench">
        <div className="mechanics-input">
          <label htmlFor="mechanics-input">Scenario input · editable JSON</label>
          <small id="mechanics-input-help">Synthetic example. Replace values with authorized data in the documented units.</small>
          <textarea id="mechanics-input" aria-describedby="mechanics-input-help" spellCheck={false} value={input} onChange={event => { invalidateRun(); setInput(event.target.value); }} />
          <div className="mechanics-actions">
            <button type="button" className="mechanics-run" disabled={pending} onClick={runDiagnostic}>{pending ? 'Computing…' : 'Run diagnostic'}</button>
            <button type="button" onClick={() => { invalidateRun(); setInput(JSON.stringify(selected.sample_input, null, 2)); }}>Reset example</button>
          </div>
        </div>
        <div className="mechanics-output" aria-live="polite" aria-busy={pending}>
          <h3>Diagnostic result</h3>
          {runError && <p className="mechanics-error" role="alert">{runError}</p>}
          {pending ? <p className="mechanics-status">Computing the supplied scenario…</p> : result !== null ? <pre tabIndex={0} aria-label="Diagnostic result JSON">{JSON.stringify(result, null, 2)}</pre> : !runError && <p className="mechanics-status">Run a scenario to inspect its calculations and assumptions.</p>}
        </div>
      </div>
    </>}
    <details className="mechanics-auth"><summary>API access · optional bearer token</summary>
      <label htmlFor="mechanics-token">Token for a protected research API</label>
      <div><input id="mechanics-token" type="password" autoComplete="off" value={tokenDraft} onChange={event => setTokenDraft(event.target.value)} placeholder="Bearer token" /><button type="button" onClick={() => { invalidateRun(); setToken(tokenDraft.trim()); setRetry(value => value + 1); }}>Apply & load catalog</button></div>
      <small>Kept in this page's memory only. Sent to the same research API for catalog and diagnostic requests.</small>
    </details>
  </section>;
}
