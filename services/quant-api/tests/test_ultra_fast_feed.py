import struct

from osiris_api.adapters.ultra_fast_feed import (
    BoundedTickBuffer, OSIRISUltraFastAdapter, UltraFastMarketFeed,
)


def test_capacity_validation():
    try:
        BoundedTickBuffer(2_000_000)
        assert False, "non-power-of-two capacity accepted"
    except ValueError:
        pass


def test_ingest_and_duplicate_rejection():
    feed = UltraFastMarketFeed(buffer_size=2)
    now = 1_800_000_000_000_000_000
    assert feed.ingest("BTCUSDT", 10, 2, now - 1000, 5, received_ns=now)
    assert not feed.ingest("BTCUSDT", 10, 2, now - 1000, 5, received_ns=now)
    assert not feed.ingest("BTCUSDT", -1, 2, now - 1000, 6, received_ns=now)
    assert feed.ingest("BTCUSDT", 11, 3, now - 1000, 6, received_ns=now)
    assert not feed.ingest("BTCUSDT", 12, 3, now - 1000, 7, received_ns=now)
    assert feed.stats()["overflow"] == 1
    assert [x.sequence for x in feed.get_batch()] == [5, 6]
    assert feed.ingest("BTCUSDT", 12, 3, now - 1000, 7, received_ns=now)


def test_stale_future_and_invalid_binary():
    feed = UltraFastMarketFeed()
    now = 1_800_000_000_000_000_000
    assert not feed.ingest("X", 1, 1, now + 1, 1, received_ns=now)
    assert not feed.ingest("X", 1, 1, now - 31_000_000_000, 1, received_ns=now)
    assert not feed.ingest_binary(b"bad", "X", received_ns=now)
    assert feed.ingest_binary(struct.pack("<QddI", now - 1, 10, 2, 1),
                              "X", received_ns=now)


def test_binance_trade_id_not_buyer_maker_flag():
    adapter = OSIRISUltraFastAdapter()
    now = 1_800_000_000_000_000_000
    message = {"s": "BTCUSDT", "p": "100", "q": "0.1",
               "T": (now - 1_000_000) // 1_000_000, "t": 123, "m": True}
    assert adapter.process_websocket_message(message, received_ns=now)
    assert adapter.feed.get_batch()[0].sequence == 123
    assert not adapter.process_websocket_message(message, received_ns=now)


def test_callbacks_and_invalid_json():
    adapter = OSIRISUltraFastAdapter()
    assert not adapter.process_websocket_message({"s": "BTCUSDT"})
    assert adapter.feed.stats()["rejected"] == 1


def test_configuration_rejects_unsupported_acceleration():
    from osiris_api.config.ultra_fast_config import UltraFastConfig, DEVELOPMENT_CONFIG
    assert DEVELOPMENT_CONFIG.RING_BUFFER_SIZE == 131_072
    for kwargs in ({"RING_BUFFER_SIZE": 100_000},
                   {"ENABLE_MULTIPATH": True},
                   {"USE_MEMORY_MAPPING": True},
                   {"FAIL_CLOSED_ON_STALE": False}):
        try:
            UltraFastConfig(**kwargs)
            assert False, f"unsupported configuration accepted: {kwargs}"
        except ValueError:
            pass


def test_adapter_enforces_configured_100ms_staleness():
    from osiris_api.config.ultra_fast_config import UltraFastConfig
    adapter = OSIRISUltraFastAdapter(config=UltraFastConfig(RING_BUFFER_SIZE=8, BATCH_SIZE=8))
    now = 1_800_000_000_000_000_000
    assert not adapter.feed.ingest("X", 1, 1, now - 101_000_000, 1, received_ns=now)
    assert adapter.feed.ingest("X", 1, 1, now - 99_000_000, 1, received_ns=now)


def test_batch_processing_preserves_order():
    feed = UltraFastMarketFeed(buffer_size=2048)
    now = 1_800_000_000_000_000_000
    for i in range(1000):
        assert feed.ingest("ETHUSDT", 3000 + i, 10, now - 1000, i,
                           received_ns=now)
    first = feed.get_batch(500)
    second = feed.get_batch(500)
    assert len(first) == len(second) == 500
    assert [t.sequence for t in first] == list(range(500))
    assert [t.sequence for t in second] == list(range(500, 1000))
    assert feed.stats()["buffer_utilization"] == 0


def test_binary_round_trip_and_callback_delivery():
    feed = UltraFastMarketFeed(buffer_size=8)
    received = []
    feed.subscribe("SOLUSDT", received.append)
    now = 1_800_000_000_000_000_000
    payload = struct.pack("<QddI", now - 1000, 150.0, 2.5, 99)
    assert feed.ingest_binary(payload, "SOLUSDT", received_ns=now)
    assert len(received) == 1
    assert received[0].price == 150.0
    assert received[0].size == 2.5
    assert received[0].sequence == 99
    assert feed.get_batch()[0] == received[0]


def test_callback_exception_does_not_discard_accepted_tick():
    feed = UltraFastMarketFeed(buffer_size=8)
    def broken_callback(tick):
        raise RuntimeError("subscriber failed")
    feed.subscribe("BTCUSDT", broken_callback)
    now = 1_800_000_000_000_000_000
    assert feed.ingest("BTCUSDT", 10, 1, now - 1, 1, received_ns=now)
    assert feed.stats()["accepted"] == 1
    assert len(feed.get_batch()) == 1


def test_performance_counters_are_consistent():
    feed = UltraFastMarketFeed(buffer_size=2)
    now = 1_800_000_000_000_000_000
    for i in range(3):
        feed.ingest("X", 100, 1, now - 1, i, received_ns=now)
    stats = feed.stats()
    assert stats["accepted"] == 2
    assert stats["rejected"] == 1
    assert stats["overflow"] == 1
    assert stats["buffer_utilization"] == 1.0


def test_invalid_batch_size_rejected():
    feed = UltraFastMarketFeed(buffer_size=8)
    try:
        feed.get_batch(0)
        assert False, "zero batch size accepted"
    except ValueError:
        pass


def test_standalone_synthetic_benchmark_counts_without_overflow():
    from osiris_api.ultra_fast_engine import simulate_network_traffic
    stats = simulate_network_traffic(1000, buffer_size=128, batch_size=64)
    assert stats["mode"] == "synthetic_local_only"
    assert stats["total_ticks"] == 1000
    assert stats["rejected"] == 0
    assert stats["ticks_per_second"] > 0


def test_standalone_benchmark_rejects_oversized_batch():
    from osiris_api.ultra_fast_engine import simulate_network_traffic
    try:
        simulate_network_traffic(10, buffer_size=8, batch_size=9)
        assert False, "oversized batch accepted"
    except ValueError:
        pass
