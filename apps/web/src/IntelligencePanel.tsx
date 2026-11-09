import React from 'react';
import { useIntelligenceData } from './useIntelligenceData';

export function IntelligencePanel() {
  const { data, error } = useIntelligenceData();
  return <section className="panel" aria-label="Research intelligence">
    <div className="panel-head"><h2>Research Intelligence</h2><small>SIMULATION · NO EXECUTION</small></div>
    {error ? <p>Intelligence data unavailable — API connection required.</p>
      : !data ? <p>Loading research telemetry…</p>
      : <div>
        <p>Satellite telemetry: {data.satellite.telemetry_connected ? 'Connected' : 'Not connected'}</p>
        <p>Latency arbitrage verified: {data.satellite.arbitrage_verified ? 'Yes' : 'No'}</p>
        <p>Submitted sentiment signals: {data.sentiment.buffered_signals}</p>
        <p>Weighted sentiment: {data.sentiment.weighted_sentiment.toFixed(3)} (research only)</p>
        <p>Forecast verified: {data.sentiment.forecast_verified ? 'Yes' : 'No'}</p>
        <p>Trading execution: {data.execution_enabled ? 'Enabled' : 'Disabled'}</p>
      </div>}
  </section>;
}
