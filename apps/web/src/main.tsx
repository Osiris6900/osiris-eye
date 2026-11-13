import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { LiveTradingSetup } from './LiveTradingSetup';
import { TradingConnections } from './TradingConnections';
import { ResearchWorkspace } from './ResearchWorkspace';
import { SecuritySetup } from './SecuritySetup';
import { DashboardWorkspace } from './DashboardWorkspace';
import MarketMechanics from './MarketMechanics';

const nav = ['Dashboard', 'Live Markets', 'Options Chain', 'AI Signals', 'Strategy Lab', 'Backtesting', 'Paper Trading', 'Live Trading', 'Risk Management', 'Market Regime', 'Global Markets', 'News & Sentiment', 'Reports', 'Settings'];
const icons = ['▦', '⌁', '▤', '✧', '⌘', '◷', '▣', '⇄', '⚖', '◉', '◎', '▧', '▤', '⚙'];

export function App() {
  const [authorization, setAuthorization] = useState('');
  const [active, setActive] = useState('Dashboard');
  return <div className="app-shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">✧</div><div><b>OSIRIS EYE</b><small>INSTITUTIONAL QUANT</small></div></div>
      <div className="side-label">WORKSPACE</div>
      <nav aria-label="Workspace">{nav.map((page, i) => <button key={page} onClick={() => setActive(page)} className={active === page ? 'selected' : ''} aria-current={active === page ? 'page' : undefined}>
        <span aria-hidden="true">{icons[i]}</span>{page}{page === 'Live Trading' && <em>LOCKED</em>}
      </button>)}</nav>
      <div className="world-art"><div className="globe" aria-hidden="true">◉</div></div>
      <div className="system-card"><small>SYSTEM STATUS</small>
        <p><i className="unknown"/>Health · See backend snapshot</p>
        <p><i className="unknown"/>Research · Requires authorization</p>
        <p><i className="amber"/>Live execution · Disabled</p>
      </div>
    </aside>
    <main className="main">
      <header className="topbar dashboard-topbar"><strong>OSIRIS EYE / RESEARCH WORKSPACE</strong><a href="/admin.html">Admin Control Centre</a></header>
      <div className="demo-banner"><span>● PAPER RESEARCH ONLY</span><span>Backend snapshots carry source and time · Live execution disabled</span></div>
      <div className="section-heading"><div><small>MARKETS / RESEARCH / OPERATIONS</small><h1>{active === 'Dashboard' ? 'Trading Intelligence Dashboard' : active}</h1></div><span className="market-open">● RESEARCH MODE</span></div>
      {active === 'Live Trading' || active === 'Settings' ? <>
        <TradingConnections key={authorization} authorization={authorization}/>
        {active === 'Settings' && <SecuritySetup authorization={authorization} onAuthorization={setAuthorization}/>}
        <LiveTradingSetup/>
      </> : active === 'Strategy Lab' || active === 'Backtesting' || active === 'Reports' ?
        <><ResearchWorkspace page={active} authorization={authorization} onAuthorization={setAuthorization}/>
          {active === 'Strategy Lab' && <MarketMechanics/>}</> :
        <DashboardWorkspace page={active} authorization={authorization} onAuthorization={setAuthorization}/>}
      <footer>OSIRIS EYE · INSTITUTIONAL QUANT TERMINAL <span>PAPER ONLY · LIVE EXECUTION DISABLED</span></footer>
    </main>
  </div>;
}
const root = document.getElementById('root');
if (root) createRoot(root).render(<React.StrictMode><App/></React.StrictMode>);
