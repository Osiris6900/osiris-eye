import { useEffect, useRef, useState } from 'react';

export function SecuritySetup({ authorization, onAuthorization }: { authorization: string; onAuthorization: (value: string) => void }) {
  const [seed, setSeed] = useState('');
  const [code, setCode] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const generation = useRef(0);
  const active = useRef<AbortController | null>(null);
  const currentAuthorization = useRef(authorization);
  useEffect(() => {
    if (currentAuthorization.current !== authorization) {
      generation.current += 1; active.current?.abort();
      currentAuthorization.current = authorization;
      setSeed(''); setCode(''); setMessage(''); setError(''); setPending(false);
    }
  }, [authorization]);
  useEffect(() => () => { generation.current += 1; active.current?.abort(); }, []);
  async function call(action: 'enroll' | 'login' | 'logout') {
    const current = ++generation.current;
    active.current?.abort();
    const controller = new AbortController(); active.current = controller;
    setPending(true); setError(''); setMessage('');
    try {
      const header = /^(Bearer|Session) /.test(authorization.trim()) ? authorization.trim() : `Bearer ${authorization.trim()}`;
      const response = await fetch(`/api/control-plane/auth/${action}`, { method: 'POST',
        signal: controller.signal,
        headers: { Authorization: header, 'Content-Type': 'application/json' }, body: JSON.stringify(action === 'login' ? { code } : {}) });
      if (!response.ok) throw new Error(`Authentication request failed (${response.status}).`);
      const result = await response.json();
      if (generation.current !== current || controller.signal.aborted) return;
      if (action === 'enroll') {
        if (typeof result.secret !== 'string' || !/^[A-Z2-7]{32}$/.test(result.secret)) throw new Error('Invalid enrollment response.');
        setSeed(result.secret); setMessage('Add this key to your authenticator app, then enter its current six-digit code.');
      } else if (action === 'login') {
        if (typeof result.session_token !== 'string' || result.session_token.length < 32) throw new Error('Invalid session response.');
        currentAuthorization.current = `Session ${result.session_token}`;
        onAuthorization(currentAuthorization.current); setSeed(''); setCode(''); setMessage('MFA session active for 15 minutes. It stays in memory and expires on reload.');
      } else { currentAuthorization.current = ''; onAuthorization(''); setSeed(''); setCode(''); setMessage('Session revoked.'); }
    } catch (failure) {
      if (generation.current === current && !controller.signal.aborted) setError(failure instanceof Error ? failure.message : 'Authentication unavailable.');
    } finally { if (generation.current === current) setPending(false); }
  }
  return <section className="panel research-workspace" aria-label="MFA and session settings">
    <div className="panel-head"><h2>MFA and session settings</h2><span className="mini-tag">AUTHENTICATOR · TOTP</span></div>
    <label htmlFor="security-token">Configured admin authorization<input id="security-token" type="password" autoComplete="off" value={authorization} onChange={event => { onAuthorization(event.target.value); setSeed(''); }} /></label>
    <p>Use your configured role token to enroll or sign in. Enrollment and session tokens are never saved to browser storage.</p>
    <button disabled={pending || !authorization.trim()} onClick={() => { void call('enroll'); }}>Enroll authenticator</button>
    {seed && <p>Authenticator setup key: <code aria-label="Authenticator setup key">{seed}</code></p>}
    <label htmlFor="security-code">Authenticator code<input id="security-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code} onChange={event => setCode(event.target.value)} /></label>
    <button disabled={pending || !/^\d{6}$/.test(code)} onClick={() => { void call('login'); }}>Start MFA session</button>
    <button disabled={pending || !authorization.startsWith('Session ')} onClick={() => { void call('logout'); }}>Revoke session</button>
    {message && <p role="status">{message}</p>}{error && <p role="alert" className="down">{error}</p>}
  </section>;
}
