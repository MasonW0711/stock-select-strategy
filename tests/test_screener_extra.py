from datetime import date, timedelta

import pytest

from config import DEFAULT_SCREEN_PARAMETERS
from main import parse_markets
from models import PriceBar, StockScreenResult
from screener import StockScreener


def _make_screener():
    return StockScreener(
        tdcc_latest_client=None, tdcc_history_client=None, twse_client=None,
        tpex_client=None, screen_params=DEFAULT_SCREEN_PARAMETERS, logger=None,
    )


# --- parse_markets 驗證 ---

def test_parse_markets_valid_and_dedup():
    assert parse_markets("listed,otc") == ("listed", "otc")
    assert parse_markets("OTC, listed , otc") == ("otc", "listed")


def test_parse_markets_empty_falls_back_to_default():
    assert parse_markets("") == ("listed", "otc")
    assert parse_markets("  ,  ") == ("listed", "otc")


def test_parse_markets_all_invalid_raises():
    with pytest.raises(ValueError):
        parse_markets("foo")
    with pytest.raises(ValueError):
        parse_markets("foo,bar")


# --- 價格跳空標註（未還原價） ---

def _bars(closes, start=date(2026, 1, 1)):
    return [
        PriceBar(trade_date=start + timedelta(days=i), open_price=c, high_price=c, low_price=c, close_price=c, volume=1, turnover=1)
        for i, c in enumerate(closes)
    ]


def test_price_discontinuity_flagged_on_large_single_day_gap():
    screener = _make_screener()
    result = StockScreenResult(code="9999", name="t", market="上市")
    # 70 根：在中間放一個 -40% 的跳空（疑似減資/除權息）
    closes = [100.0] * 35 + [60.0] * 35
    screener._apply_price_filters(_bars(closes), result)
    assert any("單日跳空" in note for note in result.source_notes)


def test_price_discontinuity_not_flagged_on_normal_moves():
    screener = _make_screener()
    result = StockScreenResult(code="9999", name="t", market="上市")
    closes = [100.0 + i * 0.1 for i in range(70)]  # 平緩走勢，無跳空
    screener._apply_price_filters(_bars(closes), result)
    assert not any("單日跳空" in note for note in result.source_notes)


# --- 遠期報酬缺口上限 ---

def test_forward_returns_none_when_gap_exceeds_tolerance():
    screener = _make_screener()
    anchor = date(2026, 1, 1)
    # 只有 anchor 當天，以及 anchor+60 天才有資料 → 30 天視窗最近的未來 bar 落在 +60 天，超過容忍
    bars = [
        PriceBar(trade_date=anchor, open_price=100, high_price=100, low_price=100, close_price=100, volume=1, turnover=1),
        PriceBar(trade_date=anchor + timedelta(days=60), open_price=120, high_price=120, low_price=120, close_price=120, volume=1, turnover=1),
    ]
    returns = screener._compute_forward_returns(bars, anchor_date=anchor, windows_days=(30,), max_gap_days=15)
    assert returns[30] is None


def test_forward_returns_accepts_bar_within_tolerance():
    screener = _make_screener()
    anchor = date(2026, 1, 1)
    bars = [
        PriceBar(trade_date=anchor, open_price=100, high_price=100, low_price=100, close_price=100, volume=1, turnover=1),
        PriceBar(trade_date=anchor + timedelta(days=33), open_price=110, high_price=110, low_price=110, close_price=110, volume=1, turnover=1),
    ]
    returns = screener._compute_forward_returns(bars, anchor_date=anchor, windows_days=(30,), max_gap_days=15)
    assert returns[30] is not None and abs(returns[30] - 0.10) < 1e-9
