import { useEffect, useRef, useState } from 'react';
import './researchWorkspace.css';

type Page = 'Strategy Lab' | 'Backtesting' | 'Reports';
type Job = { id: string; kind: string; status: string; result: unknown };
type Review = { id: string; value: { tool: string; status: string } };
type Reports = { jobs: Job[]; experiments: Review[]; models: unknown[]; shadow_runs: unknown[]; role: string };

export function ResearchWorkspace({ page, authorization, onAuthorization }: {
  page: Page; authorization: string; onAuthorization: (value: string) => void;
}) {
  const [signals, setSignals] = useState('[0, 1, 1, 0]');
  const [returns, setReturns] = useState('[0, 0.01, -0.02, 0.005]');
  const [cost, setCost] = useState('10');
  const [experiment, setExperiment] = useState(() => `experiment-${crypto.randomUUID()}`);
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [reports, setReports] = useState<Reports | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    generation.current += 1; pending.current?.abort(); setLoading(false); setResult(null); setError(''); setReports(null);
    return () => { generation.current += 1; pending.current?.abort(); };
  }, [page]);

  async function request(path: string, body?: unknown) {
    const current = ++generation.current;
    pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    setLoading(true); setError('');
    try {
      const response = await fetch(`/api${path}`, {
        method: body === undefined ? 'GET' : 'POST', signal: controller.signal,
        headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
          ...(authorization.trim() ? { Authorization: /^(Bearer|Session) /.test(authorization.trim()) ? authorization.trim() : `Bearer ${authorization.trim()}` } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) throw new Error(response.status === 401 || response.status === 403 ? 'Authorization or an MFA session is required.' : `Backend request failed (${response.status}).`);
      const data: unknown = await response.json();
      if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid backend response.');
      if (generation.current !== current) return null;
      return data as Record<string, unknown>;
    } catch (failure) {
      if (generation.current === current && !controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Backend unavailable.');
      return null;
    } finally { if (generation.current === current) setLoading(false); }
  }

  function inputs() {
    const values: unknown = JSON.parse(signals), observations: unknown = JSON.parse(returns);
    if (!Array.isArray(values) || !Array.isArray(observations) || values.length !== observations.length || values.length < 2 || values.length > 10000 ||
      values.some(value => typeof value !== 'number' || !Number.isFinite(value) || Math.abs(value) > 1) ||
      observations.some(value => typeof value !== 'number' || !Number.isFinite(value) || value <= -1) ||
      !cost.trim() || !Number.isFinite(Number(cost)) || Number(cost) < 0) throw new Error('Use aligned finite arrays, exposures between -1 and 1, returns above -100%, and nonnegative costs.');
    return { signals: values, asset_returns: observations, cost_bps_per_unit_turnover: Number(cost) };
  }

  async function submit() {
    setResult(null); setError('');
    try {
      const payload = inputs();
      const data = page === 'Strategy Lab'
        ? await request('/control-plane/agents/requests', { id: experiment.trim(), tool: 'backtest', payload })
        : await request('/control-plane/backtest/simulate', payload);
      if (data) setResult(data);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Invalid input.'); }
  }

  async function loadReports() {
    setReports(null);
    const data = await request('/control-plane/reports');
    if (!data) return;
    if (!Array.isArray(data.jobs) || !data.jobs.every(job => job && typeof job.id === 'string' && typeof job.kind === 'string' && typeof job.status === 'string') ||
      !Array.isArray(data.experiments) || !data.experiments.every(review => review && typeof review.id === 'string' && review.value && typeof review.value.status === 'string') ||
      !Array.isArray(data.models) || !Array.isArray(data.shadow_runs)) {
      setError('Invalid report response.'); return;
    }
    setReports(data as unknown as Reports);
  }

  async function approve(id: string) {
    const data = await request(`/control-plane/agents/${encodeURIComponent(id)}/approve`, {});
    if (data) await loadReports();
  }

  function download() {
    if (!reports) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(reports, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'osiris-research-report.json'; anchor.click();
    URL.revokeObjectURL(url);
  }

  return <section className="panel research-workspace" aria-label={`${page} workspace`}>
    <div className="panel-head"><h2>{page}</h2><span className="mini-tag">RESEARCH · EXECUTION DISABLED</span></div>
    <label htmlFor="research-auth">API authorization
      <input id="research-auth" type="password" autoComplete="off" value={authorization} onChange={event => onAuthorization(event.target.value)} placeholder="Bearer token or MFA Session token" />
    </label>
    <p>Authorization stays in memory for this browser tab. Configure an MFA session in Settings for reviewed research jobs.</p>
    {page !== 'Reports' ? <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      {page === 'Strategy Lab' && <label htmlFor="experiment-id">Experiment ID<input id="experiment-id" value={experiment} maxLength={128} onChange={event => setExperiment(event.target.value)} /></label>}
      <label htmlFor="research-signals">Target exposures (JSON array)<textarea id="research-signals" value={signals} onChange={event => setSignals(event.target.value)} /></label>
      <label htmlFor="research-returns">Asset returns (JSON array)<textarea id="research-returns" value={returns} onChange={event => setReturns(event.target.value)} /></label>
      <label htmlFor="research-cost">Turnover cost (basis points)<input id="research-cost" type="number" min="0" step="any" value={cost} onChange={event => setCost(event.target.value)} /></label>
      <p>Signals apply to the next period. This simulator excludes market impact, funding and borrow costs.</p>
      <button disabled={loading} type="submit">{page === 'Strategy Lab' ? 'Submit for independent review' : 'Run backtest'}</button>
    </form> : <>
      <button disabled={loading} type="button" onClick={() => { void loadReports(); }}>Load reports</button>
      {reports && <>
        <button type="button" onClick={download}>Download JSON report</button>
        {!reports.jobs.length && <p>No research jobs recorded.</p>}
        <table><thead><tr><th>Job</th><th>Tool</th><th>Status</th></tr></thead><tbody>
          {reports.jobs.map(job => <tr key={job.id}><td>{job.id}</td><td>{job.kind}</td><td>{job.status}</td></tr>)}
        </tbody></table>
        {reports.experiments.map(review => <p key={review.id}>{review.id}: {review.value.status}
          {review.value.status === 'awaiting_review' && ['operator', 'risk_officer', 'security_admin'].includes(reports.role) &&
            <button disabled={loading} type="button" onClick={() => { void approve(review.id); }}>Approve {review.id}</button>}</p>)}
        <p>Models: {reports.models.length} · Shadow runs: {reports.shadow_runs.length}</p>
      </>}
    </>}
    {loading && <p role="status">Waiting for backend…</p>}
    {error && <p role="alert" className="down">{error}</p>}
    {result && <div aria-label="Research result"><pre>{JSON.stringify(result, null, 2)}</pre></div>}
  </section>;
}
