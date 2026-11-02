import datetime as dt

import pandas as pd
import pytest
import yaml

from auction_alpha.rules.adapters import get_adapter
from auction_alpha.rules.registry import VERIFICATION_STATUSES, RuleNotFound, RuleRegistry, default_registry


def test_registry_loads_all_markets():
    reg = default_registry()
    assert set(reg.markets) == {"SET", "SGX", "HKEX"}
    for row in reg.audit_rows():
        assert row["status"] in VERIFICATION_STATUSES


def test_verified_rules_cite_loaded_official_source():
    reg = default_registry()
    for m, mr in reg.markets.items():
        for vs in mr.rules.values():
            for v in vs:
                if v.status == "verified_official":
                    assert mr.sources[v.source].fetched_ok
                    assert mr.sources[v.source].url.startswith("https://")


def test_sgx_has_no_verified_rules_because_pages_failed_to_load():
    reg = default_registry()
    assert all(r["status"] != "verified_official" for r in reg.audit_rows() if r["market"] == "SGX")


def test_unknown_start_date_is_flagged_historically_assumed():
    reg = default_registry()
    old = reg.lookup("SET", "auction.close.random_window", "2018-06-01")
    new = reg.lookup("SET", "auction.close.random_window", "2026-11-02")
    assert old.historically_assumed and not old.trusted
    assert not new.historically_assumed and new.trusted
    # effective-dated rule is NOT historically assumed
    tick = reg.lookup("SET", "tick_size.table", "2018-06-01")
    assert not tick.historically_assumed


def test_effective_dated_versions_hk_stamp_duty():
    reg = default_registry()
    assert reg["HKEX"].get("fees.exchange", "2021-05-01")["stamp_duty_bps"] == 10.0
    assert reg["HKEX"].get("fees.exchange", "2022-06-01")["stamp_duty_bps"] == 13.0
    assert reg["HKEX"].get("fees.exchange", "2023-11-16")["stamp_duty_bps"] == 13.0
    assert reg["HKEX"].get("fees.exchange", "2023-11-17")["stamp_duty_bps"] == 10.0
    assert reg["HKEX"].get("auction.close.exists", "2016-07-22") is False
    assert reg["HKEX"].get("auction.close.exists", "2016-07-26") is True


def test_overlapping_versions_rejected(tmp_path):
    doc = {"market": "XX", "exchange_name": "x", "timezone": "UTC", "currency": "X", "registry_version": "1",
           "sources": [], "rules": [
               {"key": "k", "status": "assumption", "value": 1, "effective_from": "2020-01-01"},
               {"key": "k", "status": "assumption", "value": 2, "effective_from": "2021-01-01"}]}
    (tmp_path / "xx.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match="overlapping"):
        RuleRegistry(tmp_path)


def test_verified_without_source_rejected(tmp_path):
    doc = {"market": "XX", "exchange_name": "x", "timezone": "UTC", "currency": "X", "registry_version": "1",
           "sources": [{"id": "s", "url": "https://x", "fetched_ok": False}],
           "rules": [{"key": "k", "status": "verified_official", "value": 1, "source": "s"}]}
    (tmp_path / "xx.yaml").write_text(yaml.safe_dump(doc))
    with pytest.raises(ValueError, match="verified_official"):
        RuleRegistry(tmp_path)


def test_missing_rule_raises():
    with pytest.raises(RuleNotFound):
        default_registry().lookup("SET", "does.not.exist", "2020-01-01")


def test_set_closing_window_local_times():
    w = get_adapter("SET").close_auction_window(dt.date(2024, 3, 5))
    tz = "Asia/Bangkok"
    assert w.auction_start.tz_convert(tz).strftime("%H:%M") == "16:30"
    assert w.random_start.tz_convert(tz).strftime("%H:%M") == "16:35"
    assert w.random_end.tz_convert(tz).strftime("%H:%M") == "16:40"
    assert w.tac_start is None


def test_hkex_cas_windows_full_and_half_day():
    ad = get_adapter("HKEX")
    tz = "Asia/Hong_Kong"
    w = ad.close_auction_window(dt.date(2024, 3, 5))
    assert [x.tz_convert(tz).strftime("%H:%M") for x in (w.auction_start, w.cancel_cutoff, w.random_start, w.random_end)] == \
        ["16:01", "16:06", "16:08", "16:10"]
    h = ad.close_auction_window(dt.date(2024, 12, 24), halfday=True)
    assert h.random_end.tz_convert(tz).strftime("%H:%M") == "12:10"


def test_sgx_tac_window():
    w = get_adapter("SGX").close_auction_window(dt.date(2024, 3, 5))
    assert w.tac_start is not None and w.tac_start >= w.random_end


def test_tick_sizes_and_rounding():
    ad = get_adapter("SET")
    d = dt.date(2024, 1, 2)
    assert ad.tick_size(1.5, d) == 0.01
    assert ad.tick_size(24.9, d) == 0.10
    assert ad.tick_size(25.0, d) == 0.25
    assert ad.tick_size(450, d) == 2.0
    assert ad.round_to_tick(24.97, d, side=+1) == 24.9
    assert ad.round_to_tick(24.91, d, side=-1) == 25.0


def test_price_limits_set_only():
    d = dt.date(2024, 1, 2)
    lo, hi = get_adapter("SET").price_limits(100.0, d)
    assert lo == 70.0 and hi == 130.0
    assert get_adapter("HKEX").price_limits(100.0, d) is None


def test_fee_schedule_hk_stamp_duty_rounds_up():
    fs = get_adapter("HKEX").fee_schedule(dt.date(2024, 1, 2))
    c = fs.cost(100_000.0)
    expected = 100_000 * (5.0 + 0.565 + 0.27 + 0.015) / 1e4 + 100.0  # stamp 0.1% = 100 exactly
    assert c == pytest.approx(expected)
    c2 = fs.cost(100_001.0)
    assert c2 - 100_001 * (5.0 + 0.85) / 1e4 == pytest.approx(101.0)  # rounded up to the next dollar


def test_rule_usage_is_counted():
    ad = get_adapter("SGX")
    ad.fee_schedule(dt.date(2019, 1, 2))
    summ = ad.usage_summary()
    assert any("secondary_unverified" in k for k in summ)
