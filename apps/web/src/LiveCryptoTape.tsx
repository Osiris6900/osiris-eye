import { useEffect, useState } from 'react';

const symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'];
const staleAfterMs = 30_000;
type Quote = { price: number; change: number; receivedAt: number };
type Status = 'connecting' | 'connected' | 'reconnecting';

export default function LiveCryptoTape() {
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [status, setStatus] = useState<Status>('connecting');
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let watchdog: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    let attempts = 0;
    const clock = setInterval(() => setNow(Date.now()), 1000);

    const connect = () => {
      if (disposed) return;
      setStatus(attempts === 0 ? 'connecting' : 'reconnecting');
      setQuotes({});
      const scheduleRetry = () => {
        clearTimeout(watchdog);
        if (disposed) return;
        setStatus('reconnecting');
        attempts += 1;
        retry = setTimeout(connect, Math.min(15_000, 1000 * 2 ** Math.min(attempts, 4)));
      };
      try {
        const current = new WebSocket('wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/ethusdt@ticker/solusdt@ticker/bnbusdt@ticker/xrpusdt@ticker');
        socket = current;
        // Reconnect a silent connection as well as a closed one.
        const armWatchdog = () => {
          clearTimeout(watchdog);
          watchdog = setTimeout(() => current.close(), staleAfterMs);
        };
        armWatchdog();
        current.onopen = () => {
          if (disposed) return;
          setStatus('connected');
          armWatchdog();
        };
        current.onmessage = event => {
          if (disposed) return;
          try {
            const d = JSON.parse(event.data)?.data;
            if (!d || !symbols.includes(d.s) || typeof d.c !== 'string' || !d.c.trim()
              || typeof d.P !== 'string' || !d.P.trim()) return;
            const price = Number(d.c), change = Number(d.P), receivedAt = Date.now();
            if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(change)
              || !Number.isFinite(d.E) || d.E < receivedAt - staleAfterMs
              || d.E > receivedAt + 5000) return;
            attempts = 0;
            armWatchdog();
            setNow(receivedAt);
            setQuotes(previous => ({ ...previous, [d.s]: { price, change, receivedAt } }));
          } catch { /* Ignore malformed upstream frames. */ }
        };
        current.onerror = () => {
          if (!disposed) setStatus('reconnecting');
          current.close();
        };
        current.onclose = scheduleRetry;
      } catch {
        scheduleRetry();
      }
    };
    connect();
    return () => {
      disposed = true;
      clearInterval(clock);
      clearTimeout(retry);
      clearTimeout(watchdog);
      if (socket) {
        socket.onopen = socket.onmessage = socket.onerror = socket.onclose = null;
        socket.close();
      }
    };
  }, []);

  const live = status === 'connected' && Object.values(quotes).some(q => now - q.receivedAt < staleAfterMs);
  const label = live ? 'LIVE TICKS' : status === 'connected' ? 'WAITING FOR FRESH TICKS'
    : status === 'connecting' ? 'CONNECTING' : 'RECONNECTING';
  return <section className="live-feed-panel" aria-label="Live crypto prices">
    <div className="live-feed-head"><b>LIVE CRYPTO · BINANCE SPOT</b><span role="status" className={live ? 'feed-live' : 'feed-wait'}>● {label}</span></div>
    <div className="live-feed-quotes">{symbols.map(symbol => {
      const q = quotes[symbol];
      const fresh = live && q && now - q.receivedAt < staleAfterMs;
      return <div key={symbol} aria-label={symbol}>
        <small>{symbol.replace('USDT', ' / USDT')}</small>
        <strong>{fresh ? q.price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 }) : '—'}</strong>
        <em className={fresh ? (q.change >= 0 ? 'up' : 'down') : ''}>{fresh ? `${q.change >= 0 ? '+' : ''}${q.change.toFixed(2)}%` : 'Waiting for fresh tick'}</em>
      </div>;
    })}</div>
    <small className="feed-disclaimer">Live exchange feed when connected · Binance Spot only · Separate source from backend price history · No orders submitted</small>
  </section>;
}
