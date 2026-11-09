import { useEffect, useState } from 'react';

export interface IntelligenceOverview {
  mode: string;
  execution_enabled: boolean;
  satellite: { telemetry_connected: boolean; arbitrage_verified: boolean };
  sentiment: { buffered_signals: number; weighted_sentiment: number; forecast_verified: boolean };
}

export function useIntelligenceData() {
  const [data, setData] = useState<IntelligenceOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const load = async () => {
      try {
        const response = await fetch('/api/v1/intelligence/dashboard/intelligence-overview', {
          signal: controller.signal, credentials: 'same-origin',
        });
        if (!response.ok) throw new Error('Intelligence API unavailable');
        const result: IntelligenceOverview = await response.json();
        if (active) { setData(result); setError(null); }
      } catch (err) {
        if (active && !controller.signal.aborted) { setError(String(err)); setData(null); }
      }
    };
    void load();
    const timer = setInterval(() => void load(), 15000);
    return () => { active = false; clearInterval(timer); controller.abort(); };
  }, []);
  return { data, error };
}
