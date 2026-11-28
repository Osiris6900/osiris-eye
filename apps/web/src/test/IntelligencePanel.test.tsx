import { render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { IntelligencePanel } from '../IntelligencePanel';

function overview(sentiment: Record<string, unknown>) {
  return {
    mode: 'research_only',
    execution_enabled: false,
    satellite: { telemetry_connected: false, arbitrage_verified: false },
    sentiment,
  };
}

it('does not present an empty sentiment buffer as a reading', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: async () => overview({
      buffered_signals: 0, weighted_sentiment: 0, forecast_verified: false,
      source: 'user_submitted_signals', timestamp: new Date().toISOString(),
    }),
  }));
  render(<IntelligencePanel />);
  expect(await screen.findByText(/Research sentiment unavailable/)).toBeInTheDocument();
  expect(screen.queryByText(/0\.000/)).not.toBeInTheDocument();
});

it('labels a submitted research sentiment and keeps execution disabled', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    json: async () => overview({
      buffered_signals: 2, weighted_sentiment: 0.25, forecast_verified: false,
      source: 'user_submitted_signals', timestamp: '2026-11-28T15:00:00+00:00',
    }),
  }));
  render(<IntelligencePanel />);
  expect(await screen.findByText(/user_submitted_signals/)).toBeInTheDocument();
  expect(screen.getByText(/Weighted sentiment: 0\.250/)).toBeInTheDocument();
  expect(screen.getByText(/Forecast verified: no/)).toBeInTheDocument();
  expect(screen.getByText(/Trading execution: disabled/)).toBeInTheDocument();
});
