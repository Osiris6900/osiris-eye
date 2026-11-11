import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { LiveTradingSetup, LIVE_SETUP_STORAGE_KEY } from '../LiveTradingSetup';

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

function change(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
function save() { fireEvent.click(screen.getByRole('button', { name: 'Save setup draft' })); }

describe('Live trading setup draft', () => {
  it('allows configuration to be deferred without enabling execution or sending requests', () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    render(<LiveTradingSetup />);
    save();
    expect(screen.getByRole('status')).toHaveTextContent('Setup draft saved on this browser');
    expect(screen.getByRole('button', { name: 'Activate live trading' })).toBeDisabled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('restores broker and risk preferences after reopening', () => {
    const first = render(<LiveTradingSetup />);
    change('Broker / exchange', ' Example broker ');
    change('Instruments', 'TEST-A, TEST-B');
    change('Account currency', 'INR');
    change('Maximum order amount', '1000');
    change('Daily loss limit', '100');
    save();
    first.unmount();
    render(<LiveTradingSetup />);
    expect(screen.getByLabelText('Broker / exchange')).toHaveValue('Example broker');
    expect(screen.getByLabelText('Maximum order amount')).toHaveValue(1000);
    expect(screen.getByLabelText('Daily loss limit')).toHaveValue(100);
    expect(screen.getByLabelText('Account currency')).toHaveValue('INR');
    expect(screen.getByRole('button', { name: 'Activate live trading' })).toBeDisabled();
  });

  it('rejects negative limits and requires their currency', () => {
    render(<LiveTradingSetup />);
    change('Maximum order amount', '-1');
    save();
    expect(screen.getByRole('alert')).toHaveTextContent('must be a positive amount');
    expect(localStorage.getItem(LIVE_SETUP_STORAGE_KEY)).toBeNull();
    change('Maximum order amount', '1000');
    save();
    expect(screen.getByRole('alert')).toHaveTextContent('Choose a currency');
  });

  it('rejects unexpected activation flags in a stored draft', () => {
    localStorage.setItem(LIVE_SETUP_STORAGE_KEY, JSON.stringify({
      version: 1, broker: 'Example', instruments: '', currency: '',
      maxOrderAmount: '', dailyLossLimit: '', liveTradingEnabled: true,
    }));
    render(<LiveTradingSetup />);
    expect(screen.getByRole('alert')).toHaveTextContent('could not be loaded');
    expect(screen.getByLabelText('Broker / exchange')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Activate live trading' })).toBeDisabled();
  });

  it('recovers from corrupt storage and lets the user replace it', () => {
    localStorage.setItem(LIVE_SETUP_STORAGE_KEY, 'not json');
    render(<LiveTradingSetup />);
    expect(screen.getByRole('alert')).toHaveTextContent('could not be loaded');
    save();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Setup draft saved');
  });

  it('reports unavailable storage without losing entered preferences', () => {
    render(<LiveTradingSetup />);
    change('Broker / exchange', 'Example');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementationOnce(() => { throw new Error('quota'); });
    save();
    expect(screen.getByRole('alert')).toHaveTextContent('Could not save');
    expect(screen.getByLabelText('Broker / exchange')).toHaveValue('Example');
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('clears the saved draft and the form', () => {
    render(<LiveTradingSetup />);
    change('Broker / exchange', 'Example');
    save();
    fireEvent.click(screen.getByRole('button', { name: 'Clear draft' }));
    expect(localStorage.getItem(LIVE_SETUP_STORAGE_KEY)).toBeNull();
    expect(screen.getByLabelText('Broker / exchange')).toHaveValue('');
  });
});
