import { useState, type FormEvent } from 'react';
import './liveTradingSetup.css';

export const LIVE_SETUP_STORAGE_KEY = 'osiris.liveTradingSetup.v1';

type Draft = {
  version: 1;
  broker: string;
  instruments: string;
  currency: string;
  maxOrderAmount: string;
  dailyLossLimit: string;
};

const emptyDraft = (): Draft => ({
  version: 1, broker: '', instruments: '', currency: '',
  maxOrderAmount: '', dailyLossLimit: '',
});
const currencies = ['INR', 'USD', 'EUR', 'GBP', 'USDT'];

function validateDraft(value: unknown): string | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return 'Invalid setup draft.';
  const data = value as Record<string, unknown>;
  if (Object.keys(data).sort().join(',') !== Object.keys(emptyDraft()).sort().join(',') || data.version !== 1) {
    return 'Invalid setup draft.';
  }
  for (const [field, maxLength] of [['broker', 128], ['instruments', 1000]] as const) {
    if (typeof data[field] !== 'string' || data[field].length > maxLength) return 'Invalid setup details.';
  }
  if (typeof data.currency !== 'string' || (data.currency !== '' && !currencies.includes(data.currency))) {
    return 'Choose a supported currency.';
  }
  for (const [field, label] of [['maxOrderAmount', 'Maximum order amount'], ['dailyLossLimit', 'Daily loss limit']] as const) {
    const amount = data[field];
    if (typeof amount !== 'string' || (amount !== '' &&
        (!/^\d+(\.\d{1,8})?$/.test(amount) || !Number.isFinite(Number(amount)) ||
         Number(amount) <= 0 || Number(amount) > Number.MAX_SAFE_INTEGER))) {
      return `${label} must be a positive amount with up to 8 decimal places, or left blank.`;
    }
  }
  if ((data.maxOrderAmount !== '' || data.dailyLossLimit !== '') && data.currency === '') {
    return 'Choose a currency for your risk limits.';
  }
  return null;
}

function loadDraft(): { draft: Draft; error: string } {
  try {
    const raw = localStorage.getItem(LIVE_SETUP_STORAGE_KEY);
    if (!raw) return { draft: emptyDraft(), error: '' };
    const parsed: unknown = JSON.parse(raw);
    if (validateDraft(parsed)) throw new Error('invalid draft');
    return { draft: parsed as Draft, error: '' };
  } catch {
    return { draft: emptyDraft(), error: 'Saved setup could not be loaded. Enter your details again.' };
  }
}

export function LiveTradingSetup() {
  const [initial] = useState(loadDraft);
  const [draft, setDraft] = useState(initial.draft);
  const [error, setError] = useState(initial.error);
  const [notice, setNotice] = useState('');

  function update(field: Exclude<keyof Draft, 'version'>, value: string) {
    setDraft(current => ({ ...current, [field]: value }));
    setError('');
    setNotice('');
  }

  function save(event: FormEvent) {
    event.preventDefault();
    const normalized = { ...draft, broker: draft.broker.trim(), instruments: draft.instruments.trim() };
    const problem = validateDraft(normalized);
    if (problem) { setError(problem); return; }
    try {
      localStorage.setItem(LIVE_SETUP_STORAGE_KEY, JSON.stringify(normalized));
      setDraft(normalized);
      setError('');
      setNotice('Setup draft saved on this browser. Live trading is still disabled.');
    } catch {
      setError('Could not save on this browser. Your entries are still shown.');
    }
  }

  function clear() {
    try {
      localStorage.removeItem(LIVE_SETUP_STORAGE_KEY);
      setDraft(emptyDraft());
      setError('');
      setNotice('Setup draft cleared.');
    } catch {
      setError('Could not clear the saved draft on this browser.');
    }
  }

  return <section className="panel live-setup" aria-label="Live trading setup">
    <div className="panel-head"><h2>Future live trading setup</h2><span className="mini-tag">CONFIGURE LATER</span></div>
    <p>Optional future broker and risk preferences. This draft does not change the paper connection options above.</p>
    <p className="muted" id="live-setup-description">This draft stays on this browser. It does not connect an account or place orders.
      {' '}API keys and passwords must be configured securely when broker integration is available.</p>
    <form onSubmit={save} noValidate aria-describedby="live-setup-description">
      <div className="live-setup-grid">
        <label htmlFor="live-broker">Broker / exchange
          <input id="live-broker" value={draft.broker} maxLength={128} placeholder="Add later" autoComplete="off"
            onChange={event => update('broker', event.target.value)} />
        </label>
        <label htmlFor="live-instruments">Instruments
          <input id="live-instruments" value={draft.instruments} maxLength={1000} placeholder="Symbols, separated by commas" autoComplete="off"
            onChange={event => update('instruments', event.target.value)} />
        </label>
        <label htmlFor="live-currency">Account currency
          <select id="live-currency" value={draft.currency} onChange={event => update('currency', event.target.value)}>
            <option value="">Choose later</option>
            {currencies.map(currency => <option key={currency} value={currency}>{currency}</option>)}
          </select>
        </label>
        <label htmlFor="live-order-limit">Maximum order amount
          <input id="live-order-limit" type="number" min="0.00000001" step="any" value={draft.maxOrderAmount} placeholder="Add later"
            onChange={event => update('maxOrderAmount', event.target.value)} />
        </label>
        <label htmlFor="live-loss-limit">Daily loss limit
          <input id="live-loss-limit" type="number" min="0.00000001" step="any" value={draft.dailyLossLimit} placeholder="Add later"
            onChange={event => update('dailyLossLimit', event.target.value)} />
        </label>
      </div>
      {error && <p role="alert" className="down">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <div className="live-setup-actions">
        <button type="submit">Save setup draft</button>
        <button type="button" onClick={clear}>Clear draft</button>
      </div>
    </form>
    <div className="live-setup-activation">
      <button type="button" disabled aria-describedby="live-activation-status">Activate live trading</button>
      <p id="live-activation-status">Live trading disabled. Broker connection, credentials, risk controls and release checks are required before activation.</p>
    </div>
  </section>;
}
