import React, { useEffect, useState } from 'react';

export type PaperPipelineStatus = {
  mode: 'offline-paper';
  feed: 'valid' | 'stale' | 'missing';
  proposal: 'approved' | 'rejected' | 'none';
  risk: 'pass' | 'blocked' | 'halted';
  order: 'filled' | 'pending' | 'none';
  instrument: string;
  timestamp: string;
  source?: string;
  detail?: string;
  intent?: { side?: string; quantity?: number; reference_price?: number; status?: string } | null;
  positions?: Array<{ symbol: string; quantity: string; average_cost: string }> | null;
  cash?: string | null;
  pnl?: { realized: string; unrealized: string | null; equity: string | null } | null;
  fill_count?: number;
  reconciliation?: { passed: boolean; breaks?: string[] } | null;
  valuation_as_of?: string | null;
  valuation_warnings?: string[];
};

/** Read-only backend state, not a live broker or exchange connection. */
export function PaperPipelinePanel() {
  const [status, setStatus] = useState<PaperPipelineStatus | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    let pending = false;
    const controller = new AbortController();
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const response = await fetch('/api/paper/pipeline/status', { signal: controller.signal });
        if (!response.ok) throw new Error('backend unavailable');
        const data = await response.json() as PaperPipelineStatus;
        if (data.mode !== 'offline-paper' || typeof data.feed !== 'string'
            || typeof data.proposal !== 'string' || typeof data.risk !== 'string'
            || typeof data.order !== 'string'
            || (data.positions != null && !Array.isArray(data.positions))) throw new Error('invalid status');
        if (active) { setStatus(data); setError(''); }
      } catch {
        if (active && !controller.signal.aborted) { setStatus(null); setError('Backend unavailable or authorization required'); }
      } finally {
        pending = false;
      }
    };
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 5000);
    return () => { active = false; clearInterval(timer); controller.abort(); };
  }, []);
  const gates = status ? [
    ['Market Observation', status.feed],
    ['Portfolio Proposal', status.proposal],
    ['Independent Risk', status.risk],
    ['Paper Order', status.order],
  ] : [];
  return <section className="panel" aria-label="Paper pipeline">
    <div className="panel-head"><h2>Paper Trading Pipeline</h2><span className="mini-tag">BACKEND · PAPER ONLY</span></div>
    <p style={{ color: '#91a4bf', marginTop: 0 }}>{status?.detail || 'Read-only paper pipeline status · simulated fills, no broker orders'}</p>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p>Loading backend status…</p>}
    {status && <div role="list" style={{ display: 'grid', gap: 8 }}>
      {gates.map(([label, value]) => <div role="listitem" key={label}
        style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #203451', paddingBottom: 7 }}>
        <span>{label}</span><strong>{value.toUpperCase()}</strong>
      </div>)}
    </div>}
    {status?.intent && <p>Paper intent: {status.intent.side || '—'} {status.intent.quantity ?? '—'} @ {status.intent.reference_price ?? '—'} · {status.intent.status || '—'}</p>}
    {status?.reconciliation && <p role={status.reconciliation.passed ? undefined : 'alert'}>
      Ledger reconciliation: {status.reconciliation.passed ? 'PASS' : 'FAILED'}
      {!status.reconciliation.passed && status.reconciliation.breaks?.length
        ? ` · ${status.reconciliation.breaks.join(', ')}` : ''}
    </p>}
    {status?.cash != null && status.positions && status.reconciliation?.passed ? <div role="group" aria-label="Paper execution ledger">
      <p>Paper cash: {status.cash} · Paper fills: {status.fill_count ?? 0} · Realized P&amp;L: {status.pnl?.realized ?? '—'}</p>
      <p>Unrealized P&amp;L: {status.pnl?.unrealized ?? 'Unavailable without fresh marks'} · Equity: {status.pnl?.equity ?? 'Unavailable without fresh marks'}</p>
      {status.valuation_as_of && <p>Valuation as of: {status.valuation_as_of}</p>}
      {!!status.valuation_warnings?.length && <p>Valuation: {status.valuation_warnings.join(', ')}</p>}
      {status.positions.map(position => <p key={position.symbol}>{position.symbol}: {position.quantity} units · average cost {position.average_cost}</p>)}
    </div> : <p style={{ color: '#91a4bf' }}>Verified paper accounting unavailable · cash, positions and P&amp;L unavailable</p>}
    <p style={{ color: '#91a4bf' }}>Instrument: {status?.instrument || '—'} · Source: {status?.source || 'unavailable'}</p>
  </section>;
}
