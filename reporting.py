from __future__ import annotations

from typing import Any

import pandas as pd

from models import ScreenRunSummary, StockScreenResult
from utils import format_ratio_as_pct, format_trend_values


def build_output_frames(results: list[StockScreenResult], summary: ScreenRunSummary) -> dict[str, pd.DataFrame]:
    """將結果與摘要轉成適合 CLI 與 Excel 輸出的 DataFrame。"""

    display_rows = [_build_display_row(result) for result in results]
    if display_rows:
        display_frame = (
            pd.DataFrame(display_rows)
            .sort_values(by=["是否通過篩選", "_sort_score", "股票代號"], ascending=[False, False, True])
            .drop(columns=["_sort_score"])
            .reset_index(drop=True)
        )
    else:
        display_frame = pd.DataFrame()

    raw_rows = [_build_raw_summary_row(result) for result in results]
    raw_rows.insert(
        0,
        {
            "市場": "摘要",
            "股票代號": "_RUN_SUMMARY_",
            "股票名稱": "整體摘要",
            "最新TDCC日期": summary.latest_tdcc_date.isoformat() if summary.latest_tdcc_date else "",
            "TDCC日期序列": ", ".join(value.isoformat() for value in summary.target_tdcc_dates),
            "TDCC週數": len(summary.target_tdcc_dates),
            "價格天數": "",
            "最新價格日期": "",
            "最新收盤價": "",
            "近3個月漲幅": "",
            "距20MA": "",
            "集保總戶數變化": "",
            "籌碼是否通過": "",
            "集保下降是否通過": "",
            "價格是否通過": "",
            "是否通過": f"pass={summary.passed_count} fail={summary.failed_count}",
            "選股分數": "",
            "失敗原因": "; ".join(f"{key}:{value}" for key, value in summary.failure_category_counts.items()),
            "備註": "; ".join(summary.warnings),
        },
    )
    raw_frame = pd.DataFrame(raw_rows)

    pass_frame = display_frame[display_frame["是否通過篩選"] == "Y"].reset_index(drop=True) if not display_frame.empty else pd.DataFrame(columns=_display_columns())
    fail_frame = display_frame[display_frame["是否通過篩選"] == "N"].reset_index(drop=True) if not display_frame.empty else pd.DataFrame(columns=_display_columns())
    frames: dict[str, pd.DataFrame] = {"pass": pass_frame, "fail": fail_frame, "raw_data_summary": raw_frame}
    if summary.backtest_anchor_date is not None:
        backtest_columns = ["股票代號", "股票名稱", "市場", "選股分數", "評級", "篩選日收盤價", "篩選日期", "1個月後報酬率", "3個月後報酬率"]
        backtest_rows = [_build_backtest_row(result) for result in results if result.passed]
        frames["backtest"] = pd.DataFrame(backtest_rows, columns=backtest_columns)
    return frames


def _build_display_row(result: StockScreenResult) -> dict[str, Any]:
    """將單一結果轉成終端機與 Excel 共用顯示列。"""

    return {
        "市場": result.market,
        "股票代號": result.code,
        "股票名稱": result.name,
        "選股分數": "" if result.score is None else result.score,
        "評級": result.score_label,
        "最新收盤價": "" if result.latest_close is None else round(result.latest_close, 2),
        "近3個月漲幅": format_ratio_as_pct(result.three_month_return),
        "距離20MA百分比": format_ratio_as_pct(result.distance_to_ma20),
        "集保總戶數變化": format_ratio_as_pct(result.holder_change_ratio),
        "小於10張人數最近N週趨勢": format_trend_values(result.small_holder_trend),
        "400~800張人數最近N週趨勢": format_trend_values(result.mid_holder_trend),
        "大於1000張人數最近N週趨勢": format_trend_values(result.large_holder_trend),
        "是否通過篩選": "Y" if result.passed else "N",
        "不通過原因": "；".join(result.fail_reasons),
        "1個月後報酬": format_ratio_as_pct(result.forward_returns.get(30)),
        "3個月後報酬": format_ratio_as_pct(result.forward_returns.get(90)),
        "_sort_score": result.score if result.score is not None else -1,
    }


def _build_raw_summary_row(result: StockScreenResult) -> dict[str, Any]:
    """將單一結果轉成 raw_data_summary 工作表列。"""

    return {
        "市場": result.market,
        "股票代號": result.code,
        "股票名稱": result.name,
        "最新TDCC日期": result.latest_tdcc_date.isoformat() if result.latest_tdcc_date else "",
        "TDCC日期序列": ", ".join(value.isoformat() for value in result.tdcc_dates),
        "TDCC週數": result.tdcc_weeks_loaded,
        "價格天數": result.price_days_loaded,
        "最新價格日期": result.latest_price_date.isoformat() if result.latest_price_date else "",
        "最新收盤價": "" if result.latest_close is None else round(result.latest_close, 2),
        "近3個月漲幅": format_ratio_as_pct(result.three_month_return),
        "距20MA": format_ratio_as_pct(result.distance_to_ma20),
        "集保總戶數變化": format_ratio_as_pct(result.holder_change_ratio),
        "籌碼是否通過": "Y" if result.passed_shareholding else "N",
        "集保下降是否通過": "Y" if result.passed_holder_decrease else "N",
        "價格是否通過": "Y" if result.passed_price else "N",
        "是否通過": "Y" if result.passed else "N",
        "選股分數": "" if result.score is None else result.score,
        "失敗原因": "；".join(result.fail_reasons),
        "備註": "；".join(result.source_notes),
    }


def _build_backtest_row(result: StockScreenResult) -> dict[str, Any]:
    """將通過篩選的結果轉成回測績效驗證列（含數值報酬率，供統計計算）。"""

    return {
        "股票代號": result.code,
        "股票名稱": result.name,
        "市場": result.market,
        "選股分數": result.score,
        "評級": result.score_label,
        "篩選日收盤價": result.latest_close,
        "篩選日期": result.latest_price_date.isoformat() if result.latest_price_date else "",
        "1個月後報酬率": result.forward_returns.get(30),
        "3個月後報酬率": result.forward_returns.get(90),
    }


def _display_columns() -> list[str]:
    """回傳終端機顯示欄位的固定順序。"""

    return [
        "市場",
        "股票代號",
        "股票名稱",
        "選股分數",
        "評級",
        "最新收盤價",
        "近3個月漲幅",
        "距離20MA百分比",
        "集保總戶數變化",
        "小於10張人數最近N週趨勢",
        "400~800張人數最近N週趨勢",
        "大於1000張人數最近N週趨勢",
        "是否通過篩選",
        "不通過原因",
        "1個月後報酬",
        "3個月後報酬",
    ]
