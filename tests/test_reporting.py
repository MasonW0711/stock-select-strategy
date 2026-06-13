from datetime import date, datetime

from models import ScreenRunSummary, StockScreenResult
from reporting import build_output_frames


def _passed(code, score):
    return StockScreenResult(
        code=code, name=f"股{code}", market="上市", passed=True, score=score,
        score_label="⭐", latest_close=50.0, three_month_return=0.05, distance_to_ma20=0.02,
    )


def _failed(code, reason):
    return StockScreenResult(code=code, name=f"股{code}", market="上市", passed=False, fail_reasons=[reason])


def _summary(**kwargs):
    base = dict(
        run_timestamp=datetime(2026, 4, 24, 9, 0, 0),
        total_universe=3,
        latest_tdcc_date=date(2026, 4, 11),
        target_tdcc_dates=[date(2026, 4, 11), date(2026, 4, 4)],
        passed_count=2,
        failed_count=1,
    )
    base.update(kwargs)
    return ScreenRunSummary(**base)


def test_build_output_frames_splits_and_sorts_by_score_desc():
    results = [_passed("2330", 80), _passed("1101", 90), _failed("9999", "近三個月漲幅過熱")]
    frames = build_output_frames(results, _summary())

    # pass：依分數由高到低（1101=90 在 2330=80 之前）
    assert list(frames["pass"]["股票代號"]) == ["1101", "2330"]
    assert list(frames["fail"]["股票代號"]) == ["9999"]
    assert "backtest" not in frames  # 非回測不產生 backtest 工作表


def test_build_output_frames_raw_summary_row_first():
    frames = build_output_frames([_passed("2330", 80)], _summary())
    raw = frames["raw_data_summary"]
    assert raw.iloc[0]["股票代號"] == "_RUN_SUMMARY_"
    assert raw.iloc[0]["是否通過"] == "pass=2 fail=1"


def test_build_output_frames_includes_backtest_when_anchor_set():
    results = [_passed("2330", 80), _failed("9999", "x")]
    frames = build_output_frames(results, _summary(backtest_anchor_date=date(2026, 1, 10)))
    assert "backtest" in frames
    # 只放通過股票
    assert list(frames["backtest"]["股票代號"]) == ["2330"]


def test_build_output_frames_empty_results_have_columns():
    frames = build_output_frames([], _summary(total_universe=0, passed_count=0, failed_count=0))
    assert "股票代號" in frames["pass"].columns
    assert frames["pass"].empty
