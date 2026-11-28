import React from 'react';
import { useIntelligenceData } from './useIntelligenceData';

function measuredSentiment(data: NonNullable<ReturnType<typeof useIntelligenceData>['data']>) {
  const sentiment = data.sentiment;
  return data.execution_enabled === false
    && data.mode === 'research_only'
    && sentiment?.source === 'user_submitted_signals'
    && typeof sentiment.timestamp === 'string'
    && sentiment.buffered_signals > 0
    && typeof sentiment.weighted_sentiment === 'number'
    && Number.isFinite(sentiment.weighted_sentiment)
    && Math.abs(sentiment.weighted_sentiment) <= 1
    && sentiment.forecast_verified === false;
}

export function IntelligencePanel() {
  const { data, error } = useIntelligenceData();
  const measured = data !== null && measuredSentiment(data);
  return <section className="panel" aria-label="Research intelligence">
    <div className="panel-head"><h2>Research Intelligence</h2><small>SIMULATION · NO EXECUTION</small></div>
    {error ? <p>Intelligence data unavailable — API connection required.</p>
      : !data ? <p>Loading research telemetry…</p>
      : !measured ? <p>Research sentiment unavailable. An empty buffer is not a sentiment reading, and this overview is not a forecast.</p>
      : <div>
        <p>Source: {data.sentiment.source} · Observed: {data.sentiment.timestamp}</p>
        <p>Submitted sentiment signals: {data.sentiment.buffered_signals}</p>
        <p>Weighted sentiment: {data.sentiment.weighted_sentiment.toFixed(3)}</p>
        <p>Forecast verified: no</p>
        <p>Satellite telemetry: {data.satellite.telemetry_connected ? 'Connected' : 'Not connected'}</p>
        <p>Trading execution: disabled</p>
      </div>}
  </section>;
}
