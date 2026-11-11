#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
test_python="${OSIRIS_TEST_PYTHON:-python3}"
chrome_binary="${OSIRIS_TEST_CHROME:-$(command -v google-chrome || command -v chromium)}"
fixture_dir="$(mktemp -d -t osiris-platform20-browser.XXXXXXXX)"
export OSIRIS_BROWSER_FIXTURE_DIR="$fixture_dir"
export OSIRIS_TEST_PYTHON="$test_python"
export OSIRIS_BROWSER_API_PORT="${OSIRIS_BROWSER_API_PORT:-18043}"
export OSIRIS_BROWSER_WEB_PORT="${OSIRIS_BROWSER_WEB_PORT:-15173}"
export OSIRIS_BROWSER_DEBUG_PORT="${OSIRIS_BROWSER_DEBUG_PORT:-19222}"
export OSIRIS_BROWSER_ARTIFACTS="${OSIRIS_BROWSER_ARTIFACTS:-/tmp/osiris-platform20-artifacts}"
mkdir -p "$OSIRIS_BROWSER_ARTIFACTS"
runtime_pids=()
cleanup() {
  for process_id in "${runtime_pids[@]}"; do kill -- "-$process_id" 2>/dev/null || true; done
  for process_id in "${runtime_pids[@]}"; do wait "$process_id" 2>/dev/null || true; done
  rm -rf -- "$fixture_dir"
}
trap cleanup EXIT
setsid "$test_python" scripts/browser_platform20_fixture.py > "$fixture_dir/api.log" 2>&1 &
runtime_pids+=("$!")
ready=false
for attempt in {1..300}; do
  if [ -f "$fixture_dir/ca.pem" ] && curl --noproxy 127.0.0.1 --cacert "$fixture_dir/ca.pem" -fsS "https://127.0.0.1:$OSIRIS_BROWSER_API_PORT/health" > /dev/null 2>&1; then ready=true; break; fi
  sleep .1
done
if [ "$ready" != true ]; then
  echo 'Disposable TLS API did not start'
  tail -n 60 "$fixture_dir/api.log" || true
  exit 1
fi
setsid env NODE_EXTRA_CA_CERTS="$fixture_dir/ca.pem" OSIRIS_API_TARGET="https://127.0.0.1:$OSIRIS_BROWSER_API_PORT" \
  npm --prefix apps/web run dev -- --host 127.0.0.1 --port "$OSIRIS_BROWSER_WEB_PORT" > "$fixture_dir/vite.log" 2>&1 &
runtime_pids+=("$!")
setsid "$chrome_binary" --headless=new --no-sandbox --disable-dev-shm-usage --disable-gpu \
  --no-first-run --no-default-browser-check --disable-background-networking --disable-component-update \
  --user-data-dir="$fixture_dir/chrome" --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$OSIRIS_BROWSER_DEBUG_PORT" --window-size=1440,1100 about:blank > "$fixture_dir/chrome.log" 2>&1 &
runtime_pids+=("$!")
ready=false
for attempt in {1..100}; do
  if curl --noproxy 127.0.0.1 -fsS "http://127.0.0.1:$OSIRIS_BROWSER_DEBUG_PORT/json/list" > /dev/null 2>&1 && \
     curl --noproxy 127.0.0.1 -fsS "http://127.0.0.1:$OSIRIS_BROWSER_WEB_PORT" > /dev/null 2>&1; then ready=true; break; fi
  sleep .1
done
if [ "$ready" != true ]; then
  echo 'Disposable browser or web preview did not start'
  echo 'Browser process diagnostics:'
  ps -p "${runtime_pids[2]}" -o pid=,stat=,args= || true
  echo 'Web process diagnostics:'
  ps -p "${runtime_pids[1]}" -o pid=,stat=,args= || true
  echo 'Browser log (last 60 lines):'
  tail -n 60 "$fixture_dir/chrome.log" || true
  echo 'Web log (last 60 lines):'
  tail -n 60 "$fixture_dir/vite.log" || true
  exit 1
fi
node scripts/test_platform20_browser.mjs
