from datetime import date, datetime

from api_clients import ApiClientError, TWSEApiClient
from config import DEFAULT_CACHE_SETTINGS, DEFAULT_HTTP_SETTINGS, DEFAULT_SCREEN_PARAMETERS
from config import DEFAULT_SCREEN_PARAMETERS
from models import PriceBar, ScreenRunSummary, StockScreenResult
from screener import StockScreener, build_output_frames


def _make_screener():
    """建立最小化 StockScreener（clients 設為 None，不會實際呼叫 API）。"""
    return StockScreener(
        tdcc_latest_client=None,
        tdcc_history_client=None,
        twse_client=None,
        tpex_client=None,
        screen_params=DEFAULT_SCREEN_PARAMETERS,
        logger=None,
    )


# --- _resolve_target_tdcc_dates tests ---

def test_resolve_target_dates_with_start_date():
    screener = _make_screener()
    available = [
        date(2026, 4, 10),
        date(2026, 4, 3),
        date(2026, 3, 27),
        date(2026, 3, 20),
        date(2026, 3, 13),
    ]
    latest = date(2026, 4, 10)
    start = date(2026, 4, 3)
    resolved = screener._resolve_target_tdcc_dates(available, latest, start_date=start, warnings=[])
    assert resolved[0] == start
    assert len(resolved) == max(DEFAULT_SCREEN_PARAMETERS.consecutive_weeks, DEFAULT_SCREEN_PARAMETERS.min_history_weeks)


def test_resolve_target_dates_without_start_date_includes_latest():
    screener = _make_screener()
    available = [date(2026, 4, 3), date(2026, 3, 27), date(2026, 3, 20)]
    latest = date(2026, 4, 10)
    resolved = screener._resolve_target_tdcc_dates(available, latest, start_date=None, warnings=[])
    assert resolved[0] == latest
    assert len(resolved) == max(DEFAULT_SCREEN_PARAMETERS.consecutive_weeks, DEFAULT_SCREEN_PARAMETERS.min_history_weeks)


def test_resolve_target_dates_start_date_after_latest_uses_latest():
    """start_date 晚於 latest_tdcc_date 時應回退並加入 warning。"""
    screener = _make_screener()
    available = [date(2026, 4, 10), date(2026, 4, 3), date(2026, 3, 27)]
    latest = date(2026, 4, 10)
    future_start = date(2026, 5, 1)
    warnings: list[str] = []
    resolved = screener._resolve_target_tdcc_dates(available, latest, start_date=future_start, warnings=warnings)
    assert resolved[0] == latest
    assert any("晚於" in w or "anchor" in w for w in warnings)


def test_resolve_target_dates_start_date_before_all_available():
    """start_date 早於所有可用日期時，應退回使用整個可用清單並加入 warning。"""
    screener = _make_screener()
    available = [date(2026, 4, 10), date(2026, 4, 3), date(2026, 3, 27)]
    latest = date(2026, 4, 10)
    very_old_start = date(2020, 1, 1)
    warnings: list[str] = []
    resolved = screener._resolve_target_tdcc_dates(available, latest, start_date=very_old_start, warnings=warnings)
    assert len(resolved) > 0
    assert any("最早" in w or "早於" in w for w in warnings)


def test_resolve_target_dates_latest_not_in_available_inserted():
    """latest_tdcc_date 不在 available 清單內且無 start_date 時，應被插入序列首位。"""
    screener = _make_screener()
    available = [date(2026, 4, 3), date(2026, 3, 27), date(2026, 3, 20)]
    latest = date(2026, 4, 10)  # 不在 available 裡
    resolved = screener._resolve_target_tdcc_dates(available, latest, start_date=None, warnings=[])
    assert resolved[0] == latest


# --- _compute_forward_returns tests ---

def _make_price_bars(start: date, count: int, base_price: float = 100.0) -> list[PriceBar]:
    """產生從 start 起 count 天的連續價格資料（每天漲 1%）。"""
    from datetime import timedelta
    bars = []
    for i in range(count):
        d = start + timedelta(days=i)
        price = round(base_price * (1.01 ** i), 2)
        bars.append(PriceBar(
            trade_date=d,
            open_price=price,
            high_price=price * 1.01,
            low_price=price * 0.99,
            close_price=price,
            volume=1000,
            turnover=100000,
        ))
    return bars


def test_compute_forward_returns_basic():
    screener = _make_screener()
    bars = _make_price_bars(date(2025, 10, 1), 150)
    anchor = date(2025, 10, 1)
    returns = screener._compute_forward_returns(bars, anchor_date=anchor, windows_days=(30, 90))
    assert 30 in returns
    assert 90 in returns
    assert returns[30] is not None and returns[30] > 0
    assert returns[90] is not None and returns[90] > 0


def test_compute_forward_returns_window_not_available():
    """當資料不足以計算某個 window 的報酬率時，應回傳 None。"""
    screener = _make_screener()
    bars = _make_price_bars(date(2025, 10, 1), 20)  # 只有 20 天
    anchor = date(2025, 10, 1)
    returns = screener._compute_forward_returns(bars, anchor_date=anchor, windows_days=(30, 90))
    assert returns[30] is None  # 20 天不夠計算 30 天後
    assert returns[90] is None


def test_compute_forward_returns_no_bars():
    screener = _make_screener()
    returns = screener._compute_forward_returns([], anchor_date=date(2025, 10, 1), windows_days=(30,))
    assert returns[30] is None


# --- _apply_price_filters as_of_date tests ---

def test_apply_price_filters_uses_as_of_date_for_screening():
    """回測模式下，_apply_price_filters 應只使用 as_of_date 之前的資料判斷篩選條件。"""
    screener = _make_screener()
    result = StockScreenResult(code="9999", name="測試股", market="上市")
    # 建立 120 天資料：前 60 天漲幅正常，後 60 天暴漲
    from datetime import timedelta
    all_bars = []
    for i in range(120):
        d = date(2025, 10, 1) + timedelta(days=i)
        if i < 60:
            price = 100.0
        else:
            price = 200.0  # 暴漲，距 MA 很遠
        all_bars.append(PriceBar(
            trade_date=d,
            open_price=price,
            high_price=price,
            low_price=price,
            close_price=price,
            volume=1000,
            turnover=100000,
        ))
    # 以 2025-11-29 (第 60 天) 當 as_of_date → 只看前 60 天 → 應該通過（近 3 個月漲幅 = 0%）
    as_of = date(2025, 11, 29)
    screener._apply_price_filters(all_bars, result, as_of_date=as_of)
    # 前 60 天都是 100，應通過（漲幅 0%）
    assert result.passed_price is True


def test_apply_price_filters_passed_price_correctness():
    """passed_price 應正確反映是否有新增的失敗原因。"""
    screener = _make_screener()
    result = StockScreenResult(code="1234", name="test", market="上市")
    # 給太少的資料 → 應 passed_price = False
    few_bars = _make_price_bars(date(2025, 10, 1), 5)
    screener._apply_price_filters(few_bars, result)
    assert result.passed_price is False
    assert any("價格資料不足" in r for r in result.fail_reasons)


def test_build_output_frames_creates_empty_backtest_frame_when_requested():
    summary = ScreenRunSummary(
        run_timestamp=datetime(2026, 4, 24, 9, 0, 0),
        total_universe=0,
        latest_tdcc_date=None,
        target_tdcc_dates=[],
        backtest_anchor_date=date(2025, 10, 1),
    )

    frames = build_output_frames([], summary)

    assert "backtest" in frames
    assert frames["backtest"].empty
    assert list(frames["backtest"].columns) == [
        "股票代號",
        "股票名稱",
        "市場",
        "篩選日收盤價",
        "篩選日期",
        "1個月後報酬率",
        "3個月後報酬率",
    ]


class _AlwaysFailTWSEClient(TWSEApiClient):
    def get_json(self, url: str, **kwargs):
        raise ApiClientError("bad json payload")


def test_twse_fetch_stock_day_history_skips_months_on_api_client_error():
    client = _AlwaysFailTWSEClient(
        http_settings=DEFAULT_HTTP_SETTINGS,
        cache_settings=DEFAULT_CACHE_SETTINGS,
        logger=None,
    )

    bars = client.fetch_stock_day_history("2330", months=1)

    assert bars == []
