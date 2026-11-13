import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { TradingConnections } from '../TradingConnections';
import { connectionDefaults, parseConnectionPreferences } from '../connectionPreferences';
import { parseDashboard } from '../dashboardData';

const fetcher = vi.fn();
const auth = 'Session fixture-operator-session';
const broker = { mode: 'broker', data_provider: 'binance-public', broker: 'alpaca-paper' };
const status = (preferences = { ...connectionDefaults }, version = 0, readiness = 'not_selected') => ({
  schema: 'osiris.connection-options.v1', version, preferences, broker_readiness: readiness,
  live_execution: false, broker_order_submitted: false, connection_verified: false,
});
const probe = () => ({
  schema: 'osiris.paper-broker-probe.v1', broker: 'alpaca-paper', version: 1,
  connected: true, read_only: true, checked_at: new Date().toISOString(),
  live_execution: false, broker_order_submitted: false,
});
const response = (data: unknown, code = 200) => ({ ok: code === 200, status: code, json: async () => data });
async function click(name: string) {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name })); });
}
beforeEach(() => { fetcher.mockReset(); vi.stubGlobal('fetch', fetcher); localStorage.clear(); sessionStorage.clear(); });

it('starts Ghost/keyless without contacting providers, persisting secrets or requiring broker keys', () => {
  render(<TradingConnections authorization=""/>);
  expect(screen.getByRole('radio', { name: 'Ghost / keyless' })).toBeChecked();
  expect(screen.getByLabelText('Paper broker')).toBeDisabled();
  expect(screen.getByLabelText('Paper broker')).toHaveValue('none');
  expect(screen.getByLabelText('Keyless market data')).toHaveValue('coinbase-public');
  expect(screen.getByRole('button', { name: 'Test paper broker connection' })).toBeDisabled();
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.length).toBe(0);
  expect(document.querySelector('input[type="password"]')).toBeNull();
});

it('loads server choices with bearer read access but requires MFA to apply options', async () => {
  fetcher.mockResolvedValue(response(status()));
  render(<TradingConnections authorization="fixture-read-token"/>);
  await click('Load connection status');
  expect(fetcher).toHaveBeenCalledWith('/api/control-plane/connections', expect.objectContaining({
    method: 'GET', headers: { Authorization: 'Bearer fixture-read-token' }, cache: 'no-store',
  }));
  fireEvent.click(screen.getByRole('radio', { name: 'Optional paper broker' }));
  expect(screen.getByRole('button', { name: 'Apply connection options' })).toBeDisabled();
});

it('applies actual versioned broker preferences without connecting or placing an order', async () => {
  fetcher.mockResolvedValueOnce(response(status())).mockResolvedValueOnce(response(status(broker as typeof connectionDefaults, 1, 'configured_unverified')));
  render(<TradingConnections authorization={auth}/>);
  await click('Load connection status');
  fireEvent.click(screen.getByRole('radio', { name: 'Optional paper broker' }));
  fireEvent.change(screen.getByLabelText('Keyless market data'), { target: { value: 'binance-public' } });
  await click('Apply connection options');
  expect(fetcher).toHaveBeenLastCalledWith('/api/control-plane/connections', expect.objectContaining({
    method: 'PUT', headers: { Authorization: auth, 'Content-Type': 'application/json' },
    body: JSON.stringify({ preferences: broker, expected_version: 0 }),
  }));
  expect(screen.getByText(/Connection options saved on the server/)).toBeInTheDocument();
  expect(screen.queryByText(/connection verified at/)).not.toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(localStorage.length).toBe(0); expect(sessionStorage.length).toBe(0);
});

it('tests broker access only by explicit request and removes confirmation when returning to Ghost', async () => {
  fetcher.mockResolvedValueOnce(response(status(broker as typeof connectionDefaults, 1, 'configured_unverified')))
    .mockResolvedValueOnce(response(probe()));
  render(<TradingConnections authorization={auth}/>);
  await click('Load connection status');
  expect(screen.queryByText(/connection verified at/)).not.toBeInTheDocument();
  await click('Test paper broker connection');
  expect(screen.getByText(/Alpaca PAPER connection verified at/)).toBeInTheDocument();
  expect(fetcher).toHaveBeenLastCalledWith('/api/control-plane/connections/test', expect.objectContaining({
    method: 'POST', body: JSON.stringify({ expected_version: 1 }),
  }));
  fireEvent.click(screen.getByRole('radio', { name: 'Ghost / keyless' }));
  expect(screen.queryByText(/connection verified at/)).not.toBeInTheDocument();
  expect(screen.getByLabelText('Paper broker')).toHaveValue('none');
  expect(screen.getByRole('button', { name: 'Test paper broker connection' })).toBeDisabled();
});

it('expires a successful connection probe instead of showing indefinite connectivity', async () => {
  vi.useFakeTimers();
  fetcher.mockResolvedValueOnce(response(status(broker as typeof connectionDefaults, 1, 'configured_unverified')))
    .mockResolvedValueOnce(response(probe()));
  render(<TradingConnections authorization={auth}/>);
  await click('Load connection status');
  await click('Test paper broker connection');
  expect(screen.getByText(/connection verified at/)).toBeInTheDocument();
  await act(async () => { vi.advanceTimersByTime(61_000); });
  expect(screen.queryByText(/connection verified at/)).not.toBeInTheDocument();
  expect(screen.getByText(/Previous connection check expired/)).toBeInTheDocument();
});

it.each(['disabled', 'credentials_required'])('does not offer an external probe when readiness is %s', async readiness => {
  fetcher.mockResolvedValue(response(status(broker as typeof connectionDefaults, 1, readiness)));
  render(<TradingConnections authorization={auth}/>);
  await click('Load connection status');
  expect(screen.getByRole('button', { name: 'Test paper broker connection' })).toBeDisabled();
  expect(screen.queryByText(/connection verified at/)).not.toBeInTheDocument();
});

it.each([401, 403, 409, 503])('withholds stale settings and verification after HTTP %i', async code => {
  fetcher.mockResolvedValueOnce(response(status(broker as typeof connectionDefaults, 1, 'configured_unverified')))
    .mockResolvedValueOnce(response({}, code));
  render(<TradingConnections authorization={auth}/>);
  await click('Load connection status');
  await click('Test paper broker connection');
  expect(screen.getByRole('alert')).toBeInTheDocument();
  expect(screen.queryByText(/Saved mode:/)).not.toBeInTheDocument();
  expect(screen.queryByText(/connection verified at/)).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Apply connection options' })).toBeDisabled();
});

it.each([
  { version: 2 }, { connected: false }, { live_execution: true }, { broker_order_submitted: true },
  { read_only: false }, { checked_at: '2000-01-01T00:00:00Z' }, { checked_at: '2026-11-13T00:00:00' },
])('rejects unbound, unsafe and expired probe results: %j', async changes => {
  fetcher.mockResolvedValueOnce(response(status(broker as typeof connectionDefaults, 1, 'configured_unverified')))
    .mockResolvedValueOnce(response({ ...probe(), ...changes }));
  render(<TradingConnections authorization={auth}/>);
  await click('Load connection status'); await click('Test paper broker connection');
  expect(screen.getByRole('alert')).toHaveTextContent('Invalid broker connection result');
  expect(screen.queryByText(/connection verified at/)).not.toBeInTheDocument();
});

it('ignores a delayed result from a previous identity and cancels its request', async () => {
  let finish: (value: unknown) => void = () => {};
  fetcher.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<TradingConnections authorization={auth}/>);
  await click('Load connection status');
  const signal = fetcher.mock.calls[0][1].signal as AbortSignal;
  view.rerender(<TradingConnections authorization="Session different-session"/>);
  await act(async () => { finish(response(status(broker as typeof connectionDefaults, 1, 'configured_unverified'))); });
  expect(signal.aborted).toBe(true);
  expect(screen.queryByText(/Saved mode:/)).not.toBeInTheDocument();
  expect(screen.getByRole('radio', { name: 'Ghost / keyless' })).toBeChecked();
});

it('does not interpret a successful save of different preferences as its own result', async () => {
  fetcher.mockResolvedValueOnce(response(status())).mockResolvedValueOnce(response(status(connectionDefaults, 1)));
  render(<TradingConnections authorization={auth}/>);
  await click('Load connection status');
  fireEvent.click(screen.getByRole('radio', { name: 'Optional paper broker' }));
  await click('Apply connection options');
  expect(screen.getByRole('alert')).toHaveTextContent('Connection settings changed');
  expect(screen.queryByText(/Connection options saved/)).not.toBeInTheDocument();
});

it.each([
  { ...connectionDefaults, mode: 'live' }, { ...connectionDefaults, broker: 'alpaca-paper' },
  { ...connectionDefaults, data_provider: 'alpaca-live' }, { ...connectionDefaults, api_key: 'secret' },
])('rejects unsupported modes, keyed sources and hidden credentials: %j', value => {
  expect(() => parseConnectionPreferences(value)).toThrow('Invalid connection preferences');
});

it('rejects dashboard-supplied connection settings before selecting an unsafe source', () => {
  expect(() => parseDashboard({ connection_options: { version: 1, preferences: { ...connectionDefaults, data_provider: 'live-broker' } } }))
    .toThrow('Invalid connection preferences');
});

it('offers the controls on both Settings and Live Trading while retaining the live lock', async () => {
  const { App } = await import('../main');
  render(<App/>);
  await click('Settings');
  expect(screen.getByRole('region', { name: 'Ghost, keyless and broker connections' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Activate live trading' })).toBeDisabled();
  expect(within(screen.getByRole('region', { name: 'Ghost, keyless and broker connections' })).getByLabelText('Paper broker')).toHaveValue('none');
});

