"""Minimal educational P2P chat node; NOT a payment system."""
import argparse
import asyncio
import json
import secrets
import time

MAX_LINE = 8192
MAX_TTL = 5

class Node:
    def __init__(self, port):
        self.port = port
        self.peers = set()
        self.seen = set()

    async def connect(self, host, port):
        reader, writer = await asyncio.open_connection(host, port)
        self.peers.add(writer)
        asyncio.create_task(self.receive(reader, writer))

    async def accept(self, reader, writer):
        self.peers.add(writer)
        await self.receive(reader, writer)

    async def receive(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                if len(line) > MAX_LINE:
                    break
                try:
                    msg = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue
                mid, body, ttl = msg.get("id"), msg.get("body"), msg.get("ttl")
                if not (isinstance(mid, str) and len(mid) == 32
                        and all(c in "0123456789abcdef" for c in mid)
                        and isinstance(body, str) and len(body) <= 1000
                        and type(ttl) is int and 0 <= ttl <= MAX_TTL):
                    continue
                if mid in self.seen:
                    continue
                if len(self.seen) > 10000:
                    self.seen.clear()
                self.seen.add(mid)
                print(f"peer: {body}", flush=True)
                if ttl > 0:
                    await self.broadcast({**msg, "ttl": ttl - 1}, exclude=writer)
        finally:
            self.peers.discard(writer)
            writer.close()
            await writer.wait_closed()

    async def broadcast(self, msg, exclude=None):
        wire = (json.dumps(msg, ensure_ascii=False) + "\n").encode()
        for writer in list(self.peers):
            if writer is exclude:
                continue
            try:
                writer.write(wire)
                await writer.drain()
            except (OSError, ConnectionError):
                self.peers.discard(writer)

    async def input_loop(self):
        while True:
            body = await asyncio.to_thread(input, "> ")
            if not body or len(body) > 1000:
                continue
            mid = secrets.token_hex(16)
            self.seen.add(mid)
            await self.broadcast({"id": mid, "body": body, "ttl": MAX_TTL,
                                  "created": int(time.time())})

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peer", help="host:port of another node")
    args = parser.parse_args()
    node = Node(args.port)
    server = await asyncio.start_server(node.accept, "127.0.0.1", args.port)
    if args.peer:
        host, port = args.peer.rsplit(":", 1)
        await node.connect(host, int(port))
    print(f"Local node listening on 127.0.0.1:{args.port}")
    async with server:
        await node.input_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, EOFError):
        pass
