import { useEffect, useRef, useState } from 'react';
import { amount, count, object, readApi, Row, rows, textValue } from './dashboardData';

export function SourceViews({ kind, authorization }: { kind: 'options' | 'news'; authorization: string }) {
  const [entity, setEntity] = useState(kind === 'options' ? 'NIFTY' : 'GLOBAL');
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const generation = useRef(0), active = useRef<AbortController | null>(null);
  useEffect(() => {
    generation.current += 1; active.current?.abort(); setData(null); setError(''); setLoading(false);
    return () => { generation.current += 1; active.current?.abort(); };
  }, [kind, authorization, entity]);
  async function load() {
    const current = ++generation.current;
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    const timeout = setTimeout(() => controller.abort(), 12_000);
    setData(null); setError(''); setLoading(true);
    try {
      const requested = entity.trim().toUpperCase();
      const result = object(await readApi(`/market/views/${kind}?${new URLSearchParams({ entity: requested, limit: kind === 'options' ? '5' : '30' })}`, authorization, controller.signal));
      if (!result || result.kind !== kind || result.entity !== requested || result.live_execution !== false ||
          !['available', 'unavailable'].includes(String(result.status)) || !Array.isArray(result.records) || result.records.length > 100 ||
          !result.records.every(item => {
            const record = object(item);
            return record && record.kind === kind && record.entity === requested && record.live_execution === false &&
              ['synthetic', 'operator_licensed'].includes(String(record.source_mode)) && object(record.payload) &&
              typeof record.event_time === 'string' && Number.isFinite(Date.parse(record.event_time)) &&
              typeof record.available_at === 'string' && Number.isFinite(Date.parse(record.available_at)) &&
              Date.parse(record.event_time) <= Date.parse(record.available_at) && Date.parse(record.available_at) <= Date.now() + 5000;
          }) || (result.status === 'unavailable' && result.records.length !== 0)) throw new Error('Invalid source snapshot.');
      if (current === generation.current && !controller.signal.aborted) setData(result);
    } catch (failure) {
      if (current === generation.current) setError(controller.signal.aborted ? 'Source request timed out.' : failure instanceof Error ? failure.message : 'Source unavailable.');
    } finally { clearTimeout(timeout); if (current === generation.current) setLoading(false); }
  }
  const records = rows(data?.records), title = kind === 'options' ? 'Options chain source' : 'News & sentiment sources';
  return <section className="panel dashboard-panel" aria-label={title}>
    <div className="panel-head"><h2>{title}</h2></div>
    <form className="market-query" onSubmit={event => { event.preventDefault(); void load(); }}>
      <label>{kind === 'options' ? 'Underlying' : 'News universe'}<input required maxLength={128} value={entity} onChange={event => setEntity(event.target.value)}/></label>
      <button disabled={loading || !authorization.trim() || !entity.trim()} type="submit">Load {kind === 'options' ? 'option snapshots' : 'news snapshots'}</button>
    </form>
    {loading && <p role="status">Loading source snapshots…</p>}{error && <p role="alert">{error}</p>}
    {!loading && !records.length && <p>{kind === 'options' ? 'Options chain unavailable.' : 'News and sentiment unavailable.'} An allowed source must supply a current snapshot. No sample values replace missing data.</p>}
    {data && <p>Query as of: {textValue(data.as_of)} · {records.length} records · {textValue(data.reason, 'Available')}</p>}
    {records.map((record, i) => {
      const payload = object(record.payload)!;
      return <article key={textValue(record.id, String(i))} className="source-record">
        <p><strong>{textValue(record.source)}</strong> · {record.source_mode === 'synthetic' ? 'SYNTHETIC' : 'OPERATOR DECLARED LICENCE'}</p>
        <p>Event: {textValue(record.event_time)} · Available: {textValue(record.available_at)}</p>
        <p>Rights reference: {textValue(record.rights_reference)} · Independent rights verification pending</p>
        {kind === 'news' ? <>
          <h3>{textValue(payload.title)}</h3>
          {typeof payload.url === 'string' && /^https:\/\/[^\s]+$/.test(payload.url) && <a href={payload.url} target="_blank" rel="noopener noreferrer">Original source</a>}
          <p>Source-supplied research sentiment: {typeof payload.sentiment === 'number' && Number.isFinite(payload.sentiment) && Math.abs(payload.sentiment) <= 1 ? payload.sentiment : 'Unavailable'}</p>
        </> : <>
          <h3>{textValue(payload.underlying)} · Expiry {textValue(payload.expiry)} · {textValue(payload.currency)}</h3>
          <div className="dashboard-table"><table><caption>Recorded option quotes</caption><thead><tr><th>Contract</th><th>Type</th><th>Strike</th><th>Bid</th><th>Ask</th><th>Volume</th><th>Open interest</th></tr></thead>
            <tbody>{rows(payload.contracts).slice(0, 100).map((contract, j) => <tr key={j}><td>{textValue(contract.symbol)}</td><td>{textValue(contract.option_type)}</td><td>{amount(contract.strike)}</td><td>{amount(contract.bid)}</td><td>{amount(contract.ask)}</td><td>{count(contract.volume)}</td><td>{count(contract.open_interest)}</td></tr>)}</tbody></table></div>
          <p>Showing up to 100 contracts from this snapshot. Quotes are historical observations; no order routing is available.</p>
        </>}
        <p className="muted">Record SHA-256: {textValue(record.record_sha256)}</p>
      </article>;
    })}
    <p className="muted">Source configuration is an operator declaration. A stored record does not prove vendor licensing, data authenticity, sentiment/model approval or permission to trade.</p>
  </section>;
}
