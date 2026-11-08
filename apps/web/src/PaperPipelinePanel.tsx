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
  positions?: null;
  cash?: null;
  pnl?: null;
};

/** Read-only backend state, not a live broker or exchange connection. */
export function PaperPipelinePanel() {
  const [status, setStatus] = useState<PaperPipelineStatus | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const response = await fetch('/api/paper/pipeline/status', { signal: controller.signal });
        if (!response.ok) throw new Error('backend unavailable');
        const data = await response.json() as PaperPipelineStatus;
        if (data.mode !== 'offline-paper') throw new Error('invalid mode');
        if (active) { setStatus(data); setError(''); }
      } catch {
        if (active && !controller.signal.aborted) { setStatus(null); setError('Backend unavailable or authorization required'); }
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
    <p style={{ color: '#91a4bf', marginTop: 0 }}>Read-only paper evaluation status · no live feed or broker connection</p>
    {error && <p role="alert">{error}</p>}
    {!status && !error && <p>Loading backend status…</p>}
    {status && <div role="list" style={{ display: 'grid', gap: 8 }}>
      {gates.map(([label, value]) => <div role="listitem" key={label}
        style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #203451', paddingBottom: 7 }}>
        <span>{label}</span><strong>{value.toUpperCase()}</strong>
      </div>)}
    </div>}
    {status?.intent && <p>Paper intent: {status.intent.side || '—'} {status.intent.quantity ?? '—'} @ {status.intent.reference_price ?? '—'} · {status.intent.status || '—'}</p>}
    <p style={{ color: '#91a4bf' }}>Positions, cash and P&amp;L: NOT AVAILABLE (no execution ledger connected)</p>
    <p style={{ color: '#91a4bf' }}>Instrument: {status?.instrument || '—'} · Source: {status?.source || 'unavailable'}</p>
  </section>;
}
