import { act, fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SourceViews } from '../SourceViews';

function response(kind: 'news' | 'options') {
  return { kind, entity: kind === 'news' ? 'GLOBAL' : 'NIFTY', status: 'available', as_of: new Date().toISOString(), live_execution: false,
    records: [{ id: 'fixture', kind, entity: kind === 'news' ? 'GLOBAL' : 'NIFTY', source: 'offline-fixture', source_mode: 'synthetic',
      live_execution: false, event_time: new Date(Date.now() - 1000).toISOString(), available_at: new Date().toISOString(),
      rights_reference: 'offline-only', record_sha256: 'a'.repeat(64),
      payload: kind === 'news' ? { title: 'Fixture headline', url: 'https://example.invalid/news', sentiment: null } :
        { underlying: 'NIFTY', expiry: '2026-11-19', currency: 'INR', contracts: [{ symbol: 'FIXTURE-CALL', option_type: 'CALL', strike: '24000', bid: '100', ask: '101', volume: 10, open_interest: 50 }] },
    }] };
}
const ok = (data: unknown) => ({ ok: true, json: async () => data });

it.each(['news', 'options'] as const)('renders actual %s records with synthetic source and pending rights labels', async kind => {
  const fetcher = vi.fn().mockResolvedValue(ok(response(kind))); vi.stubGlobal('fetch', fetcher);
  render(<SourceViews kind={kind} authorization="Session fixture-token"/>);
  fireEvent.click(screen.getByRole('button', { name: kind === 'news' ? 'Load news snapshots' : 'Load option snapshots' }));
  await screen.findByText(kind === 'news' ? 'Fixture headline' : 'FIXTURE-CALL');
  expect(screen.getByText(/SYNTHETIC/)).toBeInTheDocument();
  expect(screen.getByText(/Independent rights verification pending/)).toBeInTheDocument();
  expect(fetcher.mock.calls[0][1].headers.Authorization).toBe('Session fixture-token');
  if (kind === 'news') expect(screen.getByRole('link', { name: 'Original source' })).toHaveAttribute('rel', 'noopener noreferrer');
});

it('withholds source data after a failed refresh', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(ok(response('options'))).mockResolvedValueOnce({ ok: false, status: 403 }));
  render(<SourceViews kind="options" authorization="fixture-token"/>);
  fireEvent.click(screen.getByRole('button', { name: 'Load option snapshots' })); await screen.findByText('FIXTURE-CALL');
  fireEvent.click(screen.getByRole('button', { name: 'Load option snapshots' }));
  expect(screen.queryByText('FIXTURE-CALL')).not.toBeInTheDocument();
  expect(await screen.findByRole('alert')).toHaveTextContent('Access denied');
});

it('rejects a late reply after identity change', async () => {
  let complete: (value: unknown) => void = () => {};
  const fetcher = vi.fn((_url: string, _options: { signal: AbortSignal }) => new Promise(resolve => { complete = resolve; })); vi.stubGlobal('fetch', fetcher);
  const view = render(<SourceViews kind="news" authorization="old-token"/>);
  fireEvent.click(screen.getByRole('button', { name: 'Load news snapshots' }));
  view.rerender(<SourceViews kind="news" authorization="new-token"/>);
  await act(async () => { complete(ok(response('news'))); });
  expect(screen.queryByText('Fixture headline')).not.toBeInTheDocument();
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
});

it('rejects records with future availability', async () => {
  const data = response('news'); data.records[0].available_at = '2100-01-01T00:00:00Z';
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok(data)));
  render(<SourceViews kind="news" authorization="fixture-token"/>);
  fireEvent.click(screen.getByRole('button', { name: 'Load news snapshots' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid source snapshot');
  expect(screen.queryByText('Fixture headline')).not.toBeInTheDocument();
});
