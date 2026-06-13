from datetime import date, timedelta

from config import DEFAULT_SCREEN_PARAMETERS
from models import PriceBar, ShareholdingBucketRecord, ShareholdingSnapshot, StockInfo
from screener import StockScreener

DATES = [date(2026, 4, 25), date(2026, 4, 18), date(2026, 4, 11), date(2026, 4, 4)]
CODES = ["1101", "2330", "2454", "3008", "6505", "2603", "1216"]


def _bucket(bid, count, total=False):
    return ShareholdingBucketRecord(bucket_id=bid, label=str(bid), holder_count=count, share_count=None, ratio=0.0, is_total=total)


def _snapshot(d, small, mid, large, total):
    buckets = [_bucket(1, small), _bucket(2, 0), _bucket(3, 0), _bucket(12, mid), _bucket(13, 0), _bucket(15, large), _bucket(17, total, True)]
    return ShareholdingSnapshot(stock_code="X", stock_name="t", data_date=d, buckets=buckets, source="test")


def _passing_snapshots():
    smalls, mids, larges, totals = [100, 110, 120, 130], [130, 120, 110, 100], [130, 120, 110, 100], [9400, 9600, 9800, 10000]
    return {d: _snapshot(d, s, m, l, t) for d, s, m, l, t in zip(DATES, smalls, mids, larges, totals)}


class _LatestStub:
    def fetch_latest_market_snapshots(self):
        snaps = _passing_snapshots()
        return ({code: snaps[DATES[0]] for code in CODES}, DATES[0])


class _HistoryStub:
    def get_available_dates(self):
        return list(DATES)

    def fetch_snapshot(self, stock_code, query_date):
        return _passing_snapshots()[query_date]


class _PriceStub:
    def fetch_stock_day_history(self, stock_code, months):
        bars, day = [], date(2026, 1, 1)
        while day <= date(2026, 8, 1):
            bars.append(PriceBar(trade_date=day, open_price=100, high_price=100, low_price=100, close_price=100, volume=1, turnover=1))
            day += timedelta(days=1)
        return bars


def _make_screener(workers):
    screener = StockScreener(
        tdcc_latest_client=_LatestStub(),
        tdcc_history_client=_HistoryStub(),
        twse_client=_PriceStub(),
        tpex_client=None,
        screen_params=DEFAULT_SCREEN_PARAMETERS,
        logger=None,
        price_fetch_workers=workers,
    )
    universe = {c: StockInfo(code=c, name=c, short_name=c, market="上市", listed_date=date(2010, 1, 1), industry=None, is_ky=False) for c in CODES}
    screener._load_stock_universe = lambda markets: universe
    return screener


def _signature(results):
    return [(r.code, r.passed, r.score) for r in results]


def test_concurrent_price_stage_matches_sequential_results_and_order():
    seq_results, seq_summary = _make_screener(1).run_screening(markets=("listed",))
    con_results, con_summary = _make_screener(6).run_screening(markets=("listed",))

    assert _signature(seq_results) == _signature(con_results)  # 結果與順序一致
    assert con_summary.passed_count == seq_summary.passed_count == len(CODES)


def test_concurrent_path_handles_all_failing_chip_stage():
    # 全部在籌碼關就失敗（沒有任何抓價任務）時，併發路徑也不應出錯
    screener = _make_screener(4)
    screener.tdcc_history_client = _HistoryStub()

    # 用會破壞遞減的快照讓所有股票在籌碼關淘汰
    broken = {
        DATES[0]: _snapshot(DATES[0], 130, 100, 100, 10000),
        DATES[1]: _snapshot(DATES[1], 100, 130, 130, 9400),
        DATES[2]: _snapshot(DATES[2], 90, 140, 140, 9300),
        DATES[3]: _snapshot(DATES[3], 80, 150, 150, 9200),
    }
    screener.tdcc_history_client.fetch_snapshot = lambda stock_code, query_date: broken[query_date]
    screener.tdcc_latest_client = type("L", (), {"fetch_latest_market_snapshots": lambda self: ({c: broken[DATES[0]] for c in CODES}, DATES[0])})()

    results, summary = screener.run_screening(markets=("listed",))
    assert summary.passed_count == 0
    assert len(results) == len(CODES)
