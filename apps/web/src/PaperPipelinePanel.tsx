import React from 'react';

export type PaperPipelineStatus = {
  mode: 'offline-paper';
  feed: 'valid' | 'stale' | 'missing';
  proposal: 'approved' | 'rejected' | 'none';
  risk: 'pass' | 'blocked' | 'halted';
  order: 'filled' | 'pending' | 'none';
  instrument: string;
  timestamp: string;
};

/** Explicitly labeled synthetic state; not a live broker/market connection. */
export const samplePaperPipeline: PaperPipelineStatus = {
  mode: 'offline-paper',
  feed: 'valid',
  proposal: 'approved',
  risk: 'pass',
  order: 'filled',
  instrument: 'BTC',
  timestamp: 'SIMULATED',
};

export function PaperPipelinePanel({ status = samplePaperPipeline }: { status?: PaperPipelineStatus }) {
  const gates = [
    ['Market Observation', status.feed],
    ['Portfolio Proposal', status.proposal],
    ['Independent Risk', status.risk],
    ['Paper Order', status.order],
  ];
  return <section className="panel" aria-label="Paper pipeline">
    <div className="panel-head"><h2>Paper Trading Pipeline</h2><span className="mini-tag">OFFLINE SIMULATION</span></div>
    <p style={{ color: '#91a4bf', marginTop: 0 }}>Synthetic example only · no live feed, API, or broker connection</p>
    <div role="list" style={{ display: 'grid', gap: 8 }}>
      {gates.map(([label, value]) => <div role="listitem" key={label}
        style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #203451', paddingBottom: 7 }}>
        <span>{label}</span><strong>{value.toUpperCase()}</strong>
      </div>)}
    </div>
    <p style={{ color: '#91a4bf' }}>Instrument: {status.instrument} · Source: {status.timestamp}</p>
  </section>;
}
