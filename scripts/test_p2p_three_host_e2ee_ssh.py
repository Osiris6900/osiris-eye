#!/usr/bin/env python3
"""Three-host recipient-only E2EE acceptance. Host A runs locally; B and C are SSH-launched."""
import argparse, hashlib, json, os, queue, shlex, subprocess, tempfile, threading, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOPIC = "encrypted_message_discarded reason=not_for_this_recipient_or_sender_mismatch"

def pump(name, stream, events):
    for raw in iter(stream.readline, ""):
        line = raw.rstrip()
        print(f"[{name}] {line}", flush=True)
        events.put((name, line))
    events.put((name, "<stdout-closed>"))

def wait_for(events, processes, predicate, timeout, description):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        try:
            name, line = events.get(timeout=min(.25, max(.01, deadline-time.monotonic())))
        except queue.Empty:
            dead = [(n,p.returncode) for n,p in processes if p.poll() is not None]
            if dead: raise RuntimeError(f"process exited {dead}; recent={seen[-30:]}")
            continue
        seen.append(f"{name}: {line}")
        if predicate(name, line): return line
    raise TimeoutError(f"timeout waiting for {description}; recent={seen[-30:]}")

def remote_command(binary, bootstrap):
    b = binary if binary.startswith(("/", "./")) else "./" + binary
    return ("set -eu; cd \"$HOME\"; umask 077; tmp=$(mktemp -d /tmp/osiris-xhost-3-e2ee.XXXXXX); "
      "trap 'rm -rf \"$tmp\"' EXIT; "
      "export OSIRIS_NODE_KEY=\"$tmp/identity\" OSIRIS_TRANSPORT_KEY=\"$tmp/transport\" "
      "OSIRIS_PEER_KEY_STORE=\"$tmp/peer-keys.sqlite3\" OSIRIS_REPLAY_CACHE=\"$tmp/replay\" "
      "OSIRIS_E2EE_ENABLED=1 OSIRIS_LISTEN_ADDR=/ip4/0.0.0.0/tcp/0 "
      "OSIRIS_HEALTH_HTTP_ADDR=127.0.0.1:0 OSIRIS_BOOTSTRAP_PEERS=" + shlex.quote(bootstrap) +
      "; " + shlex.quote(b) + " <&0")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ssh-target-b", required=True)
    p.add_argument("--ssh-target-c", required=True)
    p.add_argument("--host-a-ip", required=True)
    p.add_argument("--port", type=int, default=4001)
    p.add_argument("--remote-binary", required=True)
    p.add_argument("--binary")
    p.add_argument("--ssh-option", action="append", default=[])
    a=p.parse_args()
    binary=Path(a.binary).expanduser() if a.binary else ROOT/"target/release/osiris-p2p-node"
    if not binary.is_file(): raise SystemExit("Build release binary before running this acceptance test")
    if not 1 <= a.port <= 65535: raise SystemExit("invalid port")
    events=queue.Queue(); processes=[]; local=None; remotes=[]
    instrument="OSIRIS-3HOST-"+uuid.uuid4().hex[:12].upper()
    payload="three-host recipient-only E2EE probe "+uuid.uuid4().hex
    digest=hashlib.sha256(payload.encode()).hexdigest()
    try:
      with tempfile.TemporaryDirectory(prefix="osiris-three-host-e2ee-") as d:
        d=Path(d); recipients=d/"recipients.txt"; recipients.write_text("")
        env=os.environ.copy()
        env.update(OSIRIS_NODE_KEY=str(d/"a.identity"),OSIRIS_TRANSPORT_KEY=str(d/"a.transport"),
          OSIRIS_PEER_KEY_STORE=str(d/"a.peer.sqlite3"),OSIRIS_REPLAY_CACHE=str(d/"a.replay"),
          OSIRIS_E2EE_ENABLED="1",OSIRIS_E2EE_RECIPIENTS_FILE=str(recipients),
          OSIRIS_LISTEN_ADDR=f"/ip4/0.0.0.0/tcp/{a.port}",
          OSIRIS_EXTERNAL_ADDRS=f"/ip4/{a.host_a_ip}/tcp/{a.port}",
          OSIRIS_HEALTH_HTTP_ADDR="127.0.0.1:0")
        local=subprocess.Popen([str(binary)],cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
          stderr=subprocess.STDOUT,text=True,bufsize=1)
        processes.append(("A",local)); threading.Thread(target=pump,args=("A",local.stdout,events),daemon=True).start()
        peerline=wait_for(events,processes,lambda n,l:n=="A" and l.startswith("peer_id="),20,"Host A PeerId")
        peer_a=peerline.split("=",1)[1].strip()
        wait_for(events,processes,lambda n,l:n=="A" and l.startswith("listener_ready ") and f"/tcp/{a.port}" in l,20,"Host A listener")
        bootstrap=f"/ip4/{a.host_a_ip}/tcp/{a.port}/p2p/{peer_a}"
        for name,target in (("B",a.ssh_target_b),("C",a.ssh_target_c)):
          proc=subprocess.Popen(["ssh","-T",*a.ssh_option,target,remote_command(a.remote_binary,bootstrap)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
          remotes.append(proc); processes.append((name,proc))
          threading.Thread(target=pump,args=(name,proc.stdout,events),daemon=True).start()
        peers={}
        for name in ("B","C"):
          line=wait_for(events,processes,lambda n,l:n==name and l.startswith("peer_id="),35,f"Host {name} PeerId")
          peers[name]=line.split("=",1)[1].strip()
          wait_for(events,processes,lambda n,l:n=="A" and f"key_binding_accepted peer={peers[name]} " in l,50,f"A accepts {name} key binding")
        # Give both subscribers time to join the encrypted GossipSub topic.
        wait_for(events,processes,lambda n,l:n=="B" and f"key_binding_accepted peer={peer_a} " in l,30,"B accepts A key binding")
        wait_for(events,processes,lambda n,l:n=="C" and f"key_binding_accepted peer={peer_a} " in l,30,"C accepts A key binding")
        recipients.write_text(peers["B"]+"\n")
        observation={"schema":"osiris.observation.v1","instrument":instrument,
          "observed_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
          "provider":"osiris-three-host-e2ee-acceptance-test","payload_sha256":digest,"payload":payload}
        local.stdin.write(json.dumps(observation,separators=(",",":"))+"\n"); local.stdin.flush()
        wait_for(events,processes,lambda n,l:n=="A" and f"encrypted_observation_published recipient={peers['B']} " in l,20,"A publishes only to B")
        wait_for(events,processes,lambda n,l:n=="B" and "encrypted_observation_received" in l and f"instrument={instrument}" in l and f"payload_sha256={digest}" in l,35,"B decrypts exact payload")
        wait_for(events,processes,lambda n,l:n=="C" and TOPIC in l,35,"C explicitly discards non-recipient ciphertext")
        print("THREE_HOST_E2EE_RECIPIENT_ONLY=PASS",flush=True)
        print(f"host_a_peer_id={peer_a}",flush=True); print(f"host_b_peer_id={peers['B']}",flush=True)
        print(f"host_c_peer_id={peers['C']}",flush=True); print(f"recipient={peers['B']}",flush=True)
        print(f"non_recipient={peers['C']}",flush=True); print(f"payload_sha256={digest}",flush=True)
    finally:
      for proc in [*remotes,local]:
        if proc is not None and proc.poll() is None: proc.terminate()
      for proc in [*remotes,local]:
        if proc is not None:
          try: proc.wait(timeout=8)
          except subprocess.TimeoutExpired: proc.kill(); proc.wait()

if __name__=="__main__":
  try: main()
  except (OSError,RuntimeError,TimeoutError,subprocess.SubprocessError) as e:
    print(f"THREE_HOST_E2EE_RECIPIENT_ONLY=FAIL: {e}",flush=True); raise SystemExit(1)
