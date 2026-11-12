import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';
import LiveCryptoTape from '../LiveCryptoTape';
import { App } from '../main';
import { StrictMode } from 'react';

class Socket {
  static instances: Socket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  close = vi.fn(() => this.onclose?.());
  constructor(public url: string) { Socket.instances.push(this); }
  message(data: unknown) { this.onmessage?.({ data: JSON.stringify({ data }) }); }
}
beforeEach(() => {
  vi.useFakeTimers();
  Socket.instances = [];
  vi.stubGlobal('WebSocket', Socket);
});
const tick = (overrides = {}) => ({ s: 'BTCUSDT', c: '68000.5', P: '2.5', E: Date.now(), ...overrides });

test('Live Markets opens the public exchange feed without simulated dashboard values', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 503, ok: false, json: async () => ({ detail: 'Offline' }) }));
  await act(async () => { render(<App />); });
  expect(Socket.instances).toHaveLength(0);
  fireEvent.click(screen.getByRole('button', { name: 'Live Markets' }));
  expect(screen.getByRole('region', { name: 'Live crypto prices' })).toBeInTheDocument();
  expect(Socket.instances).toHaveLength(1);
  expect(Socket.instances[0].url).toContain('btcusdt@ticker');
  expect(screen.getByText(/Live execution disabled/)).toBeInTheDocument();
  expect(screen.queryByText('24,198.60')).not.toBeInTheDocument();
});

test('connection alone is not live; fresh ticks render with signed changes', () => {
  render(<LiveCryptoTape />);
  const socket = Socket.instances[0];
  act(() => socket.onopen?.());
  expect(screen.getByRole('status')).toHaveTextContent('WAITING FOR FRESH TICKS');
  act(() => socket.message(tick()));
  expect(screen.getByRole('status')).toHaveTextContent('LIVE TICKS');
  const btc = within(screen.getByLabelText('BTCUSDT'));
  expect(btc.getByText('68,000.50')).toBeInTheDocument();
  expect(btc.getByText('+2.50%')).toBeInTheDocument();
});

test('rejects malformed, unsupported, non-finite, stale and future ticks', () => {
  render(<LiveCryptoTape />);
  const socket = Socket.instances[0];
  act(() => {
    socket.onopen?.();
    socket.onmessage?.({ data: '{broken' });
    for (const invalid of [{ c: '' }, { c: 'Infinity' }, { c: '-1' }, { P: 'NaN' },
      { s: 'UNKNOWN' }, { E: Date.now() - 31000 }, { E: Date.now() + 6000 }]) socket.message(tick(invalid));
  });
  expect(screen.getByRole('status')).toHaveTextContent('WAITING FOR FRESH TICKS');
  expect(screen.queryByText('68,000.50')).not.toBeInTheDocument();
});

test('hides stale prices, reconnects silent sockets and waits for new data', () => {
  render(<LiveCryptoTape />);
  const socket = Socket.instances[0];
  act(() => { socket.onopen?.(); socket.message(tick()); });
  act(() => { vi.advanceTimersByTime(30000); });
  expect(socket.close).toHaveBeenCalledOnce();
  expect(screen.queryByText('68,000.50')).not.toBeInTheDocument();
  expect(screen.getByRole('status')).toHaveTextContent('RECONNECTING');
  act(() => { vi.advanceTimersByTime(2000); });
  expect(Socket.instances).toHaveLength(2);
  act(() => Socket.instances[1].onopen?.());
  expect(screen.getByRole('status')).toHaveTextContent('WAITING FOR FRESH TICKS');
});

test('unmount closes the connection and cancels pending reconnects', () => {
  const view = render(<LiveCryptoTape />);
  act(() => Socket.instances[0].onclose?.());
  view.unmount();
  act(() => { vi.advanceTimersByTime(60000); });
  expect(Socket.instances).toHaveLength(1);
  expect(Socket.instances[0].close).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

test('StrictMode cleanup leaves only the active connection and no orphan timers', () => {
  const view = render(<StrictMode><LiveCryptoTape /></StrictMode>);
  expect(Socket.instances).toHaveLength(2);
  expect(Socket.instances[0].close).toHaveBeenCalledOnce();
  act(() => { Socket.instances[1].onopen?.(); Socket.instances[1].message(tick()); });
  expect(screen.getByRole('status')).toHaveTextContent('LIVE TICKS');
  view.unmount();
  expect(Socket.instances[1].close).toHaveBeenCalledOnce();
  expect(vi.getTimerCount()).toBe(0);
});

test('constructor failures retry with bounded backoff and stop on unmount', () => {
  const failing = vi.fn(function () { throw new Error('Unavailable'); });
  vi.stubGlobal('WebSocket', failing);
  const view = render(<LiveCryptoTape />);
  expect(screen.getByRole('status')).toHaveTextContent('RECONNECTING');
  act(() => { vi.advanceTimersByTime(2000); });
  expect(failing).toHaveBeenCalledTimes(2);
  act(() => { vi.advanceTimersByTime(4000 + 8000 + 15000); });
  expect(failing).toHaveBeenCalledTimes(5);
  view.unmount();
  act(() => { vi.advanceTimersByTime(60000); });
  expect(failing).toHaveBeenCalledTimes(5);
  expect(vi.getTimerCount()).toBe(0);
});
