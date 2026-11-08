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
    adapter = OSIRISUltraFastAdapter(config=UltraFastConfig(RING_BUFFER_SIZE=8))
    now = 1_800_000_000_000_000_000
    assert not adapter.feed.ingest("X", 1, 1, now - 101_000_000, 1, received_ns=now)
    assert adapter.feed.ingest("X", 1, 1, now - 99_000_000, 1, received_ns=now)
