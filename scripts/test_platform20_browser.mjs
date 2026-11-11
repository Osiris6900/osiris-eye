import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { createHmac } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const fixtureDir = process.env.OSIRIS_BROWSER_FIXTURE_DIR;
if (!fixtureDir) throw new Error('OSIRIS_BROWSER_FIXTURE_DIR is required');
const artifactDir = process.env.OSIRIS_BROWSER_ARTIFACTS || join(fixtureDir, 'artifacts');
await mkdir(artifactDir, {recursive:true});

const fixture = JSON.parse(await readFile(join(fixtureDir, 'fixture.json'), 'utf8'));
const pages = await (await fetch(`http://127.0.0.1:${process.env.OSIRIS_BROWSER_DEBUG_PORT || '19222'}/json/list`)).json();
const target = pages.find(page => page.type === 'page');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let sequence = 0;
const pending = new Map();
ws.onmessage = message => {
  const data = JSON.parse(message.data);
  if (!data.id) return;
  const entry = pending.get(data.id);
  pending.delete(data.id);
  if (data.error) entry.reject(new Error(data.error.message)); else entry.resolve(data.result);
};
function call(method, params = {}) {
  return new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error('Browser evaluation failed');
  return result.result.value;
}
async function wait(expression, label) {
  for (let attempt = 0; attempt < 80; attempt++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Timed out: ' + label);
}
async function click(text) {
  const found = await evaluate(`(() => {const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim().endsWith(${JSON.stringify(text)})); if (!b || b.disabled) return false; b.click(); return true;})()`);
  if (!found) throw new Error('Button unavailable: ' + text);
}
async function input(id, value) {
  await evaluate(`(() => { const e = document.getElementById(${JSON.stringify(id)}); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e, ${JSON.stringify(value)}); e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); })()`);
}
function code(seed) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = '';
  for (const char of seed) bits += alphabet.indexOf(char).toString(2).padStart(5, '0');
  const key = Buffer.from(bits.match(/.{8}/g).map(byte => parseInt(byte, 2)));
  const counter = Buffer.alloc(8); counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const mac = createHmac('sha1', key).update(counter).digest(); const offset = mac[19] & 15;
  return ((mac.readUInt32BE(offset) & 0x7fffffff) % 1000000).toString().padStart(6, '0');
}

const checks = [];
try {
  await call('Page.enable'); await call('Network.enable');
  await call('Network.setBlockedURLs', { urls: ['https://*'] });
  await call('Page.navigate', { url: `http://127.0.0.1:${process.env.OSIRIS_BROWSER_WEB_PORT || '15173'}` });
  await wait(`!!document.querySelector('.sidebar nav')`, 'app navigation');
  await click('Settings'); await wait(`!!document.getElementById('security-token')`, 'MFA settings');
  await input('security-token', 'Bearer ' + fixture.tokens.analyst);
  await click('Enroll authenticator'); await wait(`!!document.querySelector('[aria-label="Authenticator setup key"]')`, 'MFA enrollment');
  const seed = await evaluate(`document.querySelector('[aria-label="Authenticator setup key"]').textContent`);
  await input('security-code', code(seed)); await click('Start MFA session');
  await wait(`document.body.textContent.includes('MFA session active')`, 'MFA session');
  checks.push('browser MFA enrollment and session');
  await click('Backtesting'); await wait(`!!document.getElementById('research-signals')`, 'backtest form');
  await click('Run backtest'); await wait(`!!document.querySelector('[aria-label="Research result"]')`, 'real backend backtest');
  if (!await evaluate(`document.querySelector('[aria-label="Research result"]').textContent.includes('previous') || document.querySelector('[aria-label="Research result"]').textContent.includes('signal_alignment')`)) throw new Error('Missing backtest output');
  checks.push('browser backtest with MFA session');
  await click('Strategy Lab'); await wait(`!!document.getElementById('experiment-id')`, 'strategy form');
  await input('experiment-id', 'browser-flow-experiment'); await click('Submit for independent review');
  await wait(`document.querySelector('[aria-label="Research result"]')?.textContent.includes('awaiting_review')`, 'research review request');
  checks.push('browser research request persisted');
  async function api(path, token, body) {
    return evaluate(`(async () => { const r=await fetch('/api/control-plane/'+${JSON.stringify(path)}, {method:'POST',headers:{'Content-Type':'application/json',Authorization:${JSON.stringify(token)}},body:JSON.stringify(${JSON.stringify(body)})}); if(!r.ok) throw new Error('fixture API request failed'); return r.json(); })()`);
  }
  const enrollment = await api('auth/enroll', 'Bearer ' + fixture.tokens.operator, {});
  const operator = await api('auth/login', 'Bearer ' + fixture.tokens.operator, { code: code(enrollment.secret) });
  await click('Reports'); await wait(`!!document.getElementById('research-auth')`, 'reports workspace');
  await input('research-auth', 'Session ' + operator.session_token); await click('Load reports');
  await wait(`document.body.textContent.includes('Approve browser-flow-experiment')`, 'independent review control');
  await click('Approve browser-flow-experiment');
  await wait(`document.querySelector('table')?.textContent.includes('queued')`, 'approved queued job');
  checks.push('independent operator review from browser');
  const worker = spawnSync(process.env.OSIRIS_TEST_PYTHON || 'python3', ['-m', 'osiris_api.control_plane.cli', 'worker', '--once', '--database', fixture.database], {
    cwd: root, encoding: 'utf8', timeout: 30000,
    env: { ...process.env, OSIRIS_PLATFORM_API_URL: `https://127.0.0.1:${process.env.OSIRIS_BROWSER_API_PORT || '18043'}`, OSIRIS_WORKER_SESSION_REF: 'env:OSIRIS_TEST_WORKER_SESSION',
      OSIRIS_TEST_WORKER_SESSION: operator.session_token, SSL_CERT_FILE: fixture.ca },
  });
  if (worker.status !== 0 || !worker.stdout.includes('completed')) throw new Error('Remote HTTPS worker did not complete its job');
  await click('Load reports'); await wait(`document.querySelector('table')?.textContent.includes('completed')`, 'persisted remote worker result');
  checks.push('separate worker claimed and completed job over verified HTTPS');
  if (await evaluate(`Object.values(localStorage).some(v => v.includes('Session ') || v.includes('session_token'))`)) throw new Error('Credential persisted in browser storage');
  const screenshot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await writeFile(join(artifactDir, 'platform20-browser.png'), Buffer.from(screenshot.data, 'base64'));
  await writeFile(join(artifactDir, 'platform20-browser.json'), JSON.stringify({ checks, status: 'passed', live_execution: false }, null, 2));
  console.log(JSON.stringify({ status: 'passed', checks }));
} finally { ws.close(); }
