"""Read-only, authenticated market-feed diagnostics and tick stream."""
import asyncio
import hmac
import os

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from .adapters.ultra_fast_feed import OSIRISUltraFastAdapter

router = APIRouter()


def _feed(app):
    feed = getattr(app.state, "ultra_fast_adapter", None)
    if feed is None:
        raise HTTPException(503, "Market feed unavailable")
    return feed


@router.get("/api/v1/market-data/performance")
async def get_market_data_performance(request: Request):
    adapter = _feed(request.app)
    return {"status": "operational", "engine": "validated_paper_feed_v1",
            "mode": "paper_only", "metrics": adapter.feed.stats()}


@router.websocket("/ws/market-data/ticks/{symbol}")
async def websocket_ticks(websocket: WebSocket, symbol: str):
    # HTTP middleware does not protect websocket handshakes.
    token = os.getenv("OSIRIS_API_TOKEN", "")
    supplied = websocket.headers.get("authorization", "")
    if not token or not hmac.compare_digest(
            supplied.encode("utf-8"), f"Bearer {token}".encode("utf-8")):
        await websocket.close(code=1008)
        return
    if not symbol or len(symbol) > 40 or not all(
            c.isalnum() or c in "-_." for c in symbol):
        await websocket.close(code=1008)
        return
    adapter = getattr(websocket.app.state, "ultra_fast_adapter", None)
    if adapter is None:
        await websocket.close(code=1013)
        return
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    loop = asyncio.get_running_loop()
    overflow = asyncio.Event()

    def on_tick(tick):
        # Feed is single-loop by contract. No cross-thread websocket operations.
        if loop.is_closed():
            return
        try:
            queue.put_nowait({"symbol": tick.symbol, "price": tick.price,
                              "size": tick.size, "timestamp_ns": tick.timestamp_ns,
                              "exchange_ts_ns": tick.exchange_ts,
                              "sequence": tick.sequence})
        except asyncio.QueueFull:
            # Signal failure rather than silently stream an incomplete sequence.
            overflow.set()

    await websocket.accept()
    adapter.feed.subscribe(symbol, on_tick)
    async def sender():
        while True:
            if overflow.is_set():
                await websocket.close(code=1013)
                return
            tick = await queue.get()
            await websocket.send_json(tick)

    task = asyncio.create_task(sender())
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        adapter.feed.unsubscribe(symbol, on_tick)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def initialize_market_feed(app):
    app.state.ultra_fast_adapter = OSIRISUltraFastAdapter(app)
