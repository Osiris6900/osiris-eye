import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import MarketMechanics from '../MarketMechanics';

const module = { id: 'gex', name: 'Dealer GEX', endpoint: '/research/market-mechanics/dealer-gex',
  evidence_strength: 'mechanical', mechanism: 'Signed dealer exposure', limitations: ['Synthetic input'], sample_input: { positions: [] } };
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });

test('loads examples, posts edited input and renders the result', async () => {
  let resolve!: (r: unknown) => void;
  const fetcher = vi.fn().mockImplementationOnce(() => new Promise(r => { resolve = r; }))
    .mockResolvedValueOnce(response({ exposure: 123 }));
  vi.stubGlobal('fetch', fetcher);
  render(<MarketMechanics />);
  expect(screen.getByRole('status')).toHaveTextContent('Loading');
  await act(async () => resolve(response({ modules: [module] })));
  fireEvent.change(screen.getByLabelText('Scenario input · editable JSON'), { target: { value: '{"positions":[1]}' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnostic' }));
  expect(await screen.findByLabelText('Diagnostic result JSON')).toHaveTextContent('123');
  expect(fetcher.mock.calls[1][0]).toBe(`/api${module.endpoint}`);
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ positions: [1] });
});

test('recovers from auth errors and sends the memory-only token to both requests', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response({}, 401))
    .mockResolvedValueOnce(response({ modules: [module] })).mockResolvedValueOnce(response({ ok: true }));
  vi.stubGlobal('fetch', fetcher);
  render(<MarketMechanics />);
  expect(await screen.findByRole('alert')).toHaveTextContent('valid bearer token');
  fireEvent.click(screen.getByText('API access · optional bearer token'));
  fireEvent.change(screen.getByLabelText('Token for a protected research API'), { target: { value: 'test-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply & load catalog' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Run diagnostic' }));
  await screen.findByLabelText('Diagnostic result JSON');
  expect(fetcher.mock.calls[1][1].headers.Authorization).toBe('Bearer test-token');
  expect(fetcher.mock.calls[2][1].headers.Authorization).toBe('Bearer test-token');
  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
});

test('invalid input stays local and failed calculations show the API error', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response({ modules: [module] }))
    .mockResolvedValueOnce(response({ detail: 'Invalid positions' }, 422));
  vi.stubGlobal('fetch', fetcher);
  render(<MarketMechanics />);
  const input = await screen.findByLabelText('Scenario input · editable JSON');
  fireEvent.change(input, { target: { value: '[]' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnostic' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('JSON object');
  expect(fetcher).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'Reset example' }));
  fireEvent.click(screen.getByRole('button', { name: 'Run diagnostic' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid positions');
});

test('editing pending input prevents an old calculation result from appearing', async () => {
  let resolve!: (r: unknown) => void;
  const fetcher = vi.fn().mockResolvedValueOnce(response({ modules: [module] }))
    .mockImplementationOnce(() => new Promise(r => { resolve = r; }));
  vi.stubGlobal('fetch', fetcher);
  render(<MarketMechanics />);
  fireEvent.click(await screen.findByRole('button', { name: 'Run diagnostic' }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  fireEvent.change(screen.getByLabelText('Scenario input · editable JSON'), { target: { value: '{}' } });
  expect(fetcher.mock.calls[1][1].signal.aborted).toBe(true);
  await act(async () => resolve(response({ stale: true })));
  expect(screen.queryByLabelText('Diagnostic result JSON')).not.toBeInTheDocument();
});
