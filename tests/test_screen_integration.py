from datetime import date, timedelta

from config import DEFAULT_SCREEN_PARAMETERS
from models import ShareholdingBucketRecord, ShareholdingSnapshot, PriceBar, StockInfo
from screener import StockScreener


def _bucket(bucket_id, count, is_total=False):
    return ShareholdingBucketRecord(
        bucket_id=bucket_id, label=str(bucket_id), holder_count=count, share_count=None, ratio=0.0, is_total=is_total
    )


def _snapshot(d, small, mid, large, total):
    buckets = [
        _bucket(1, small), _bucket(2, 0), _bucket(3, 0),
        _bucket(12, mid), _bucket(13, 0),
        _bucket(15, large),
        _bucket(17, total, is_total=True),
    ]
    return ShareholdingSnapshot(stock_code="2330", stock_name="台積電", data_date=d, buckets=buckets, source="test")


class _StubHistory:
    def __init__(self, by_date):
        self.by_date = by_date
        self.calls = []

    def fetch_snapshot(self, stock_code, query_date):
        self.calls.append(query_date)
        return self.by_date[query_date]


class _StubPrice:
    def __init__(self, bars):
        self.bars = bars

    def fetch_stock_day_history(self, stock_code, months):
        return self.bars


def _flat_bars(start, end, close=100.0):
    bars, day = [], start
    while day <= end:
        bars.append(PriceBar(trade_date=day, open_price=close, high_price=close, low_price=close, close_price=close, volume=1000, turnover=100000))
        day += timedelta(days=1)
    return bars


def _build_screener(history, price):
    screener = StockScreener(
        tdcc_latest_client=None, tdcc_history_client=history, twse_client=price,
        tpex_client=None, screen_params=DEFAULT_SCREEN_PARAMETERS, logger=None,
    )
    return screener


def _passing_snapshots():
    dates = [date(2026, 4, 25), date(2026, 4, 18), date(2026, 4, 11), date(2026, 4, 4)]
    # 新到舊：小股東遞減（值遞增）、中實戶/大戶遞增（值遞減）、集保總戶數遞減
    smalls = [100, 110, 120, 130]
    mids = [130, 120, 110, 100]
    larges = [130, 120, 110, 100]
    totals = [9400, 9600, 9800, 10000]
    return {d: _snapshot(d, s, m, l, t) for d, s, m, l, t in zip(dates, smalls, mids, larges, totals)}


def test_full_screen_passes_and_scores():
    snaps = _passing_snapshots()
    history = _StubHistory(snaps)
    price = _StubPrice(_flat_bars(date(2026, 1, 1), date(2026, 8, 1)))
    screener = _build_screener(history, price)

    stock = StockInfo(code="2330", name="台積電", short_name="台積電", market="上市", listed_date=date(2010, 1, 1), industry=None, is_ky=False)
    result = screener._screen_single_stock(
        stock_info=stock,
        latest_snapshot=None,
        target_tdcc_dates=list(snaps.keys()),
        as_of_date=date(2026, 4, 9),
        effective_price_months=6,
    )

    assert result.passed is True
    assert result.passed_shareholding is True
    assert result.passed_holder_decrease is True
    assert result.passed_price is True
    assert result.score is not None and result.score > 0
    assert result.score_label != ""
    # 集保總戶數下降 6%
    assert abs(result.holder_change_ratio - (-0.06)) < 1e-9
    # 回測遠期報酬：以 as_of 當天/之前的收盤為基準（平盤 → 0%）
    assert result.forward_returns.get(30) == 0.0
    assert result.forward_returns.get(90) == 0.0


def test_full_screen_rejected_when_holder_not_decreasing():
    snaps = _passing_snapshots()
    # 把集保總戶數改成上升（最新最多）→ 應在集保關被擋下
    for d, snap in snaps.items():
        for b in snap.buckets:
            if b.is_total:
                b.holder_count = {date(2026, 4, 25): 10000, date(2026, 4, 18): 9800, date(2026, 4, 11): 9600, date(2026, 4, 4): 9400}[d]
    history = _StubHistory(snaps)
    price = _StubPrice(_flat_bars(date(2026, 1, 1), date(2026, 8, 1)))
    screener = _build_screener(history, price)

    stock = StockInfo(code="2330", name="台積電", short_name="台積電", market="上市", listed_date=date(2010, 1, 1), industry=None, is_ky=False)
    result = screener._screen_single_stock(
        stock_info=stock, latest_snapshot=None, target_tdcc_dates=list(snaps.keys()),
        as_of_date=None, effective_price_months=6,
    )

    assert result.passed is False
    assert result.passed_holder_decrease is False
    assert "集保總戶數未下降" in result.fail_reasons


def test_early_exit_stops_history_fetch_when_trend_broken():
    # 小股東第二週就上升（破壞遞減）→ 抓到第 2 週即應停止，不再查第 3、4 週
    dates = [date(2026, 4, 25), date(2026, 4, 18), date(2026, 4, 11), date(2026, 4, 4)]
    snaps = {
        dates[0]: _snapshot(dates[0], 120, 130, 130, 9400),  # small 最新=120
        dates[1]: _snapshot(dates[1], 100, 120, 120, 9600),  # small 舊=100 → 120>100 上升，破壞遞減
        dates[2]: _snapshot(dates[2], 90, 110, 110, 9800),
        dates[3]: _snapshot(dates[3], 80, 100, 100, 10000),
    }
    history = _StubHistory(snaps)
    price = _StubPrice(_flat_bars(date(2026, 1, 1), date(2026, 8, 1)))
    screener = _build_screener(history, price)

    stock = StockInfo(code="2330", name="台積電", short_name="台積電", market="上市", listed_date=date(2010, 1, 1), industry=None, is_ky=False)
    result = screener._screen_single_stock(
        stock_info=stock, latest_snapshot=None, target_tdcc_dates=dates,
        as_of_date=None, effective_price_months=6,
    )

    assert result.passed is False
    # early-exit：只查了前 2 週，沒有續查第 3、4 週
    assert history.calls == [dates[0], dates[1]]
