from datetime import date

from config import DEFAULT_SCREEN_PARAMETERS
from models import ShareholdingBucketRecord, ShareholdingSnapshot, StockScreenResult
from screener import StockScreener


def _make_screener(**param_overrides):
    params = DEFAULT_SCREEN_PARAMETERS
    if param_overrides:
        from dataclasses import replace

        params = replace(params, **param_overrides)
    return StockScreener(
        tdcc_latest_client=None,
        tdcc_history_client=None,
        twse_client=None,
        tpex_client=None,
        screen_params=params,
        logger=None,
    )


def _bucket(bucket_id, count, is_total=False):
    return ShareholdingBucketRecord(
        bucket_id=bucket_id, label=str(bucket_id), holder_count=count, share_count=None, ratio=0.0, is_total=is_total
    )


def _snapshot(d, small, mid, large, total):
    """small→bucket1, mid→bucket12, large→bucket15, total→bucket17(合計)。"""
    buckets = [
        _bucket(1, small), _bucket(2, 0), _bucket(3, 0),
        _bucket(12, mid), _bucket(13, 0),
        _bucket(15, large),
        _bucket(17, total, is_total=True),
    ]
    return ShareholdingSnapshot(stock_code="0000", stock_name="t", data_date=d, buckets=buckets, source="test")


# --- total_holder_count ---

def test_total_holder_count_reads_total_row():
    snap = _snapshot(date(2026, 4, 25), small=100, mid=10, large=5, total=12345)
    assert snap.total_holder_count() == 12345


def test_total_holder_count_none_when_no_total_row():
    snap = ShareholdingSnapshot(
        stock_code="0000", stock_name="t", data_date=date(2026, 4, 25),
        buckets=[_bucket(1, 100)], source="test",
    )
    assert snap.total_holder_count() is None


# --- _apply_holder_decrease（無靜默 fallback） ---

def _decreasing_snapshots():
    # 新到舊：總戶數 9400 < 9600 < 9800 < 10000（最新最少 = 籌碼集中）
    return [
        _snapshot(date(2026, 4, 25), 100, 10, 5, 9400),
        _snapshot(date(2026, 4, 18), 100, 10, 5, 9600),
        _snapshot(date(2026, 4, 11), 100, 10, 5, 9800),
        _snapshot(date(2026, 4, 4), 100, 10, 5, 10000),
    ]


def test_holder_decrease_pass_uses_true_n_weeks_baseline():
    screener = _make_screener()  # holder_decrease_weeks=3 → 比 totals[0] vs totals[3]
    result = StockScreenResult(code="0000", name="t")
    screener._apply_holder_decrease(_decreasing_snapshots(), result)
    assert result.passed_holder_decrease is True
    assert result.latest_total_holders == 9400
    assert result.early_total_holders == 10000
    assert abs(result.holder_change_ratio - (-0.06)) < 1e-9


def test_holder_decrease_insufficient_history_fails_without_fallback():
    screener = _make_screener()  # 需要 4 筆，只給 2 筆
    result = StockScreenResult(code="0000", name="t")
    screener._apply_holder_decrease(_decreasing_snapshots()[:2], result)
    assert result.passed_holder_decrease is False
    assert "集保總戶數資料不足" in result.fail_reasons
    # 關鍵：沒有用較短窗硬算出一個變化率（不靜默 fallback）
    assert result.holder_change_ratio is None


def test_holder_decrease_increase_is_rejected():
    screener = _make_screener()
    result = StockScreenResult(code="0000", name="t")
    increasing = list(reversed(_decreasing_snapshots()))  # 改成由少到多 = 戶數上升
    # reversed 後仍是 4 筆，但 totals[0] > totals[3]
    screener._apply_holder_decrease(increasing, result)
    assert result.passed_holder_decrease is False
    assert "集保總戶數未下降" in result.fail_reasons


def test_holder_decrease_min_ratio_enforced():
    screener = _make_screener(min_holder_decrease_ratio=0.10)  # 要求降 10%
    result = StockScreenResult(code="0000", name="t")
    screener._apply_holder_decrease(_decreasing_snapshots(), result)  # 實際只降 6%
    assert result.passed_holder_decrease is False
    assert "集保總戶數下降幅度不足" in result.fail_reasons


def test_holder_decrease_optional_when_not_required():
    screener = _make_screener(require_holder_decrease=False)
    result = StockScreenResult(code="0000", name="t")
    screener._apply_holder_decrease(_decreasing_snapshots()[:2], result)  # 資料不足
    assert result.passed_holder_decrease is False
    assert result.fail_reasons == []  # 非硬性條件時不應加入失敗原因


# --- _growth_ratio ---

def test_growth_ratio():
    screener = _make_screener()
    assert screener._growth_ratio([120, 110, 100]) == 0.2
    assert screener._growth_ratio([120, None, 100]) == 0.2
    assert screener._growth_ratio([100]) is None
    assert screener._growth_ratio([]) is None


# --- early-exit：分級趨勢提前判定 ---

def test_trend_doomed_detects_broken_prefix():
    screener = _make_screener()
    # 小股東「最新 110 > 舊 100」= 上升（應下降）→ 已不可能通過
    snaps = [
        _snapshot(date(2026, 4, 25), small=110, mid=110, large=110, total=9400),
        _snapshot(date(2026, 4, 18), small=100, mid=100, large=100, total=9600),
    ]
    assert screener._shareholding_trend_doomed(snaps) is True


def test_trend_not_doomed_when_prefix_consistent():
    screener = _make_screener()
    # 小股東下降、中實戶與大戶上升，前綴仍一致 → 尚未判定不符
    snaps = [
        _snapshot(date(2026, 4, 25), small=100, mid=110, large=110, total=9400),
        _snapshot(date(2026, 4, 18), small=110, mid=100, large=100, total=9600),
    ]
    assert screener._shareholding_trend_doomed(snaps) is False


def test_trend_not_doomed_with_single_snapshot():
    screener = _make_screener()
    snaps = [_snapshot(date(2026, 4, 25), 100, 110, 110, 9400)]
    assert screener._shareholding_trend_doomed(snaps) is False
