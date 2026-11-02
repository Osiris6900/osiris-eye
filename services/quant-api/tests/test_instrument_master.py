from osiris_api.instrument_master import InstrumentMasterStore

def instrument(**overrides):
    value = {"asset_class":"equity","venue":"NSE","symbol":"RELIANCE","quote_currency":"INR",
             "timezone":"Asia/Kolkata","session_type":"exchange","tick_size":"0.05","lot_size":1}
    value.update(overrides)
    return value

def test_persistence_and_point_in_time_versions(tmp_path):
    store=InstrumentMasterStore(str(tmp_path/"master.sqlite"))
    store.put(instrument(tick_size="0.05"),effective_at="2025-01-01T00:00:00Z")
    store.put(instrument(tick_size="0.10"),effective_at="2025-04-01T00:00:00Z")
    assert store.get("equity:NSE:RELIANCE",as_of="2025-02-01T00:00:00Z")["instrument"]["tick_size"]=="0.05"
    assert store.get("equity:NSE:RELIANCE",as_of="2025-05-01T00:00:00Z")["instrument"]["tick_size"]=="0.1"
    assert len(store.search(venue="NSE",as_of="2025-05-01T00:00:00Z"))==1

def test_duplicate_version_and_bad_tick_rejected(tmp_path):
    store=InstrumentMasterStore(str(tmp_path/"master.sqlite")); store.put(instrument())
    for record in (instrument(),instrument(tick_size=0)):
        try: store.put(record)
        except ValueError: pass
        else: raise AssertionError("invalid write was accepted")

def test_provider_mapping_requires_canonical_instrument(tmp_path):
    store=InstrumentMasterStore(str(tmp_path/"master.sqlite"))
    store.put(instrument(),effective_at="2025-01-01T00:00:00Z")
    store.put_mapping(provider="fixture",provider_symbol="RELIANCE.NS",
        instrument_id="equity:NSE:RELIANCE",effective_at="2025-01-01T00:00:00Z")
    assert store.resolve("fixture","RELIANCE.NS",as_of="2025-02-01T00:00:00Z")=="equity:NSE:RELIANCE"

def test_options_require_contract_terms(tmp_path):
    store=InstrumentMasterStore(str(tmp_path/"master.sqlite"))
    option=instrument(asset_class="option",symbol="NIFTY-OPT")
    try: store.put(option)
    except ValueError as exc: assert "option_requires" in str(exc)
    else: raise AssertionError("incomplete option contract was accepted")
