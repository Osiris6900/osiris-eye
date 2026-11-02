import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';

type PlatformModule = {id: string; name: string; status: string; capabilities: string[]};
type PlatformResponse = {count: number; modules: PlatformModule[]; execution_enabled: boolean};

function App() {
  const [platform, setPlatform] = useState<PlatformResponse | null>(null);
  const [apiStatus, setApiStatus] = useState<'checking' | 'online' | 'offline'>('checking');

  useEffect(() => {
    let active = true;
    Promise.all([
      fetch('/api/health').then(response => {
        if (!response.ok) throw new Error('health endpoint unavailable');
        return response.json();
      }),
      fetch('/api/platform/modules').then(response => {
        if (!response.ok) throw new Error('module endpoint unavailable');
        return response.json() as Promise<PlatformResponse>;
      }),
    ]).then(([, data]) => {
      if (active) { setPlatform(data); setApiStatus('online'); }
    }).catch(() => { if (active) setApiStatus('offline'); });
    return () => { active = false; };
  }, []);

  const modules = platform?.modules ?? [
    {id:'alternative_data',name:'Alternative Data Engine',status:'foundation',capabilities:['Event timestamps']},
    {id:'microstructure',name:'Market Microstructure',status:'foundation',capabilities:['Order-flow imbalance']},
    {id:'portfolio',name:'Portfolio Optimization',status:'foundation',capabilities:['Baseline weights']},
    {id:'risk',name:'Risk Intelligence',status:'foundation',capabilities:['Historical VaR / ES']},
    {id:'model_factory',name:'AI Model Factory',status:'foundation',capabilities:['Drift diagnostics']},
    {id:'strategy_lab',name:'Strategy Discovery Lab',status:'foundation',capabilities:['Performance diagnostics']},
    {id:'execution',name:'Execution Intelligence',status:'foundation',capabilities:['Cost estimation']},
    {id:'macro_regime',name:'Macro & Regime Engine',status:'foundation',capabilities:['Regime heuristic']},
    {id:'reliability',name:'System Reliability Center',status:'foundation',capabilities:['Health aggregation']},
    {id:'agent_team',name:'AI Agent Research Team',status:'foundation',capabilities:['Approval-gated tasks']},
  ];

  return <main className="shell">
    <aside className="rail"><div className="logo">◉</div><strong>OSIRIS EYE</strong><small>QUANT INTELLIGENCE</small>
      <nav>{['◈ Overview','⌁ Markets','▦ Options','⌘ Research','◫ Strategy Lab','◷ Backtests','⚖ Risk'].map((x,i)=><a className={i===0?'active':''} key={x}>{x}</a>)}</nav>
      <div className="foot">SYSTEM MODE<br/><b>RESEARCH ONLY</b></div>
    </aside>
    <section className="content">
      <header>OSIRIS EYE / INSTITUTIONAL OVERVIEW <span>● {apiStatus === 'online' ? 'SHARED API CONNECTED' : apiStatus === 'offline' ? 'API OFFLINE · SHOWING CATALOG' : 'CONNECTING TO API'}</span></header>
      <div className="hero"><div><p className="eyebrow">MULTI-ASSET INTELLIGENCE</p><h1>Market Intelligence Terminal</h1><p>One research API, a shared point-in-time store and ten connected module foundations.</p></div><label>LIVE EXECUTION DISABLED</label></div>
      <section className="stats">
        <article><small>DATA STORE</small><h2>{apiStatus === 'online' ? 'SQLite · Connected' : 'Not connected'}</h2></article>
        <article><small>PLATFORM MODULES</small><h2>{platform?.count ?? 10} foundations</h2></article>
        <article><small>RISK GATE</small><h2>Enabled · simulation</h2></article>
        <article><small>API STATUS</small><h2>{apiStatus.toUpperCase()}</h2></article>
      </section>
      <section className="columns">
        <article><h3>CONNECTED MODULE CATALOG</h3>{modules.map((m,i)=><div className="item" key={m.id}><i>{String(i+1).padStart(2,'0')}</i><div className="module-copy"><strong>{m.name}</strong><small>{m.capabilities.join(' · ')}</small></div><b>FOUNDATION</b></div>)}</article>
        <article><h3>SHARED DATA & API</h3><p>All research modules are exposed through one FastAPI service.</p>
          <ul><li>Point-in-time observations with event and availability timestamps</li><li>SQLite persistence for local development</li><li>Idempotent source + dataset + record keys</li><li>Common module registry and health endpoint</li><li>Research diagnostics only; no live order routing</li></ul>
          <p className="notice">Use licensed feeds. Preserve publication time to prevent look-ahead bias. SQLite is a local foundation, not production-scale storage.</p>
        </article>
      </section>
      <footer>OSIRIS EYE · INSTITUTIONAL QUANT PLATFORM <span>V0.2.0 · RESEARCH FOUNDATION</span></footer>
    </section>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
