import { act, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { PaperPipelinePanel } from '../PaperPipelinePanel';

const response = (body: unknown, ok = true) => ({ ok, json: async () => body });
const status = {
  mode: 'offline-paper', feed: 'valid', proposal: 'approved', risk: 'pass', order: 'filled',
  instrument: 'BTC-USD', timestamp: '2026-11-11T00:00:00Z', source: 'coinbase',
  detail: 'Public market data; simulated paper fills. No broker orders.',
  cash: '899', positions: [{ symbol: 'BTC-USD', quantity: '1', average_cost: '101' }],
  pnl: { realized: '0', unrealized: '9', equity: '1009' }, fill_count: 1,
  reconciliation: { passed: true, breaks: [] },
};

test('renders authoritative execution accounting from one pipeline status response', async () => {
  const fetcher = vi.fn().mockResolvedValue(response(status));
  vi.stubGlobal('fetch', fetcher);
  render(<PaperPipelinePanel />);
  const ledger = await screen.findByRole('group', { name: 'Paper execution ledger' });
  expect(ledger).toHaveTextContent('Paper cash: 899');
  expect(ledger).toHaveTextContent('Paper fills: 1');
  expect(ledger).toHaveTextContent('Unrealized P&L: 9');
  expect(ledger).toHaveTextContent('Equity: 1009');
  expect(ledger).toHaveTextContent('BTC-USD: 1 units');
  expect(screen.getByText('Ledger reconciliation: PASS')).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0][0]).toBe('/api/paper/pipeline/status');
});

test('hides unverified accounting when reconciliation fails', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({
    ...status, reconciliation: { passed: false, breaks: ['cash_mismatch'] },
  })));
  render(<PaperPipelinePanel />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Ledger reconciliation: FAILED');
  expect(screen.queryByRole('group', { name: 'Paper execution ledger' })).not.toBeInTheDocument();
  expect(screen.queryByText(/Paper cash: 899/)).not.toBeInTheDocument();
});

test('missing marks are shown as unavailable rather than zero P&L', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({
    ...status, pnl: { realized: '0', unrealized: null, equity: null },
  })));
  render(<PaperPipelinePanel />);
  const ledger = await screen.findByRole('group', { name: 'Paper execution ledger' });
  expect(ledger).toHaveTextContent('Unrealized P&L: Unavailable without fresh marks');
  expect(ledger).toHaveTextContent('Equity: Unavailable without fresh marks');
});

test('failed refresh removes previous balances and unmount aborts pending work', async () => {
  vi.useFakeTimers();
  const fetcher = vi.fn().mockResolvedValueOnce(response(status)).mockResolvedValue(response({}, false));
  vi.stubGlobal('fetch', fetcher);
  const view = render(<PaperPipelinePanel />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByRole('group', { name: 'Paper execution ledger' })).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByRole('alert')).toHaveTextContent('Backend unavailable');
  expect(screen.queryByRole('group', { name: 'Paper execution ledger' })).not.toBeInTheDocument();
  view.unmount();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});
