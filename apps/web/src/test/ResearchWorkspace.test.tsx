import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ResearchWorkspace } from '../ResearchWorkspace';
import { SecuritySetup } from '../SecuritySetup';

const response = (body: unknown, status = 200) => ({ ok: status === 200, status, json: async () => body });
function Workspace({ initial = 'Backtesting' }: { initial?: 'Backtesting' | 'Strategy Lab' | 'Reports' }) {
  const [page, setPage] = useState(initial);
  const [authorization, setAuthorization] = useState('');
  return <><button onClick={() => setPage('Reports')}>Switch to reports</button>
    <ResearchWorkspace page={page} authorization={authorization} onAuthorization={setAuthorization}/></>;
}
function Security() {
  const [authorization, setAuthorization] = useState('Bearer test-only-token');
  return <SecuritySetup authorization={authorization} onAuthorization={setAuthorization}/>;
}
beforeEach(() => localStorage.clear());

it('runs the actual backtest API and renders backend output', async () => {
  const fetch = vi.fn().mockResolvedValue(response({ periods: 3, total_return: -0.02 }));
  vi.stubGlobal('fetch', fetch);
  render(<Workspace/>);
  fireEvent.change(screen.getByLabelText('API authorization'), { target: { value: 'test-only-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run backtest' }));
  await screen.findByLabelText('Research result');
  expect(fetch.mock.calls[0][0]).toBe('/api/control-plane/backtest/simulate');
  expect(fetch.mock.calls[0][1].headers.Authorization).toBe('Bearer test-only-token');
  expect(screen.getByLabelText('Research result')).toHaveTextContent('-0.02');
});

it('rejects invalid arrays before sending a request', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  render(<Workspace/>);
  fireEvent.change(screen.getByLabelText('Target exposures (JSON array)'), { target: { value: '[3]' } });
  fireEvent.click(screen.getByRole('button', { name: 'Run backtest' }));
  expect(screen.getByRole('alert')).toHaveTextContent('aligned finite arrays');
  expect(fetch).not.toHaveBeenCalled();
});

it('submits a research request for independent review', async () => {
  const fetch = vi.fn().mockResolvedValue(response({ id: 'experiment-a', status: 'awaiting_review' }));
  vi.stubGlobal('fetch', fetch);
  render(<Workspace initial="Strategy Lab"/>);
  fireEvent.change(screen.getByLabelText('Experiment ID'), { target: { value: 'experiment-a' } });
  fireEvent.click(screen.getByRole('button', { name: 'Submit for independent review' }));
  await screen.findByLabelText('Research result');
  expect(fetch.mock.calls[0][0]).toBe('/api/control-plane/agents/requests');
  expect(JSON.parse(fetch.mock.calls[0][1].body).tool).toBe('backtest');
  expect(screen.getByLabelText('Research result')).toHaveTextContent('awaiting_review');
});

it('shows authorization failures without stale results', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({}, 403)));
  render(<Workspace/>);
  fireEvent.click(screen.getByRole('button', { name: 'Run backtest' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('MFA session');
  expect(screen.queryByLabelText('Research result')).not.toBeInTheDocument();
});

it('loads an empty report and rejects malformed job rows', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(response({ jobs: [], experiments: [], models: [], shadow_runs: [], role: 'analyst' }))
    .mockResolvedValueOnce(response({ jobs: [null], experiments: [], models: [], shadow_runs: [] })));
  render(<Workspace initial="Reports"/>);
  fireEvent.click(screen.getByRole('button', { name: 'Load reports' }));
  await screen.findByText('No research jobs recorded.');
  fireEvent.click(screen.getByRole('button', { name: 'Load reports' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Invalid report response');
});

it('does not render a backtest response after navigating away', async () => {
  let complete!: (value: unknown) => void;
  vi.stubGlobal('fetch', vi.fn(() => new Promise(resolve => { complete = resolve; })));
  render(<Workspace/>);
  fireEvent.click(screen.getByRole('button', { name: 'Run backtest' }));
  fireEvent.click(screen.getByRole('button', { name: 'Switch to reports' }));
  complete(response({ total_return: 12345 }));
  await waitFor(() => expect(screen.getByRole('region', { name: 'Reports workspace' })).toBeInTheDocument());
  expect(screen.queryByLabelText('Research result')).not.toBeInTheDocument();
});

it('enrolls MFA, replaces the in-memory token and revokes it without browser persistence', async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(response({ secret: 'A'.repeat(32) }))
    .mockResolvedValueOnce(response({ session_token: 's'.repeat(43) }))
    .mockResolvedValueOnce(response({ revoked: true }));
  vi.stubGlobal('fetch', fetch);
  render(<Security/>);
  fireEvent.click(screen.getByRole('button', { name: 'Enroll authenticator' }));
  await screen.findByLabelText('Authenticator setup key');
  fireEvent.change(screen.getByLabelText('Authenticator code'), { target: { value: '123456' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start MFA session' }));
  await screen.findByText(/MFA session active/);
  expect(screen.queryByLabelText('Authenticator setup key')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Configured admin authorization')).toHaveValue('Session ' + 's'.repeat(43));
  expect(localStorage.length).toBe(0);
  fireEvent.click(screen.getByRole('button', { name: 'Revoke session' }));
  await screen.findByText('Session revoked.');
  expect(screen.getByLabelText('Configured admin authorization')).toHaveValue('');
});

it('handles failed enrollment without exposing a setup key', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({}, 401)));
  render(<Security/>);
  fireEvent.click(screen.getByRole('button', { name: 'Enroll authenticator' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('401');
  expect(screen.queryByLabelText('Authenticator setup key')).not.toBeInTheDocument();
});
