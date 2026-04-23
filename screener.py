from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from api_clients import DataNotFoundError, TDCCOpenApiClient, TDCCPortalHistoryClient, TPEXApiClient, TWSEApiClient
from config import ScreenParameters, TDCC_BUCKET_DEFINITIONS, TDCC_HOLDER_GROUPS
from models import PriceBar, ScreenRunSummary, ShareholdingSnapshot, StockInfo, StockScreenResult
from utils import format_ratio_as_pct, format_trend_values


class StockScreener:
    """封裝台股籌碼選股的完整執行流程。"""

    def __init__(
        self,
        tdcc_latest_client: TDCCOpenApiClient,
        tdcc_history_client: TDCCPortalHistoryClient,
        twse_client: TWSEApiClient,
        tpex_client: TPEXApiClient | None,
        screen_params: ScreenParameters,
        logger: logging.Logger | None = None,
    ) -> None:
        """初始化資料 client、參數與分級設定。"""

        self.tdcc_latest_client = tdcc_latest_client
        self.tdcc_history_client = tdcc_history_client
        self.twse_client = twse_client
        self.tpex_client = tpex_client
        self.screen_params = screen_params
        self.logger = logger or logging.getLogger("stock_screener")
        self.bucket_definitions = {item["bucket_id"]: item for item in TDCC_BUCKET_DEFINITIONS}
        self.group_bucket_ids = self._resolve_group_bucket_ids()

    def run_screening(
        self,
        stock_limit: int | None = None,
        markets: tuple[str, ...] = ("listed",),
        start_date: date | None = None,
    ) -> tuple[list[StockScreenResult], ScreenRunSummary]:
        """執行完整選股流程並回傳結果與摘要。"""

        started_at = time.perf_counter()
        listed_stocks = self._load_stock_universe(markets=markets)
        latest_snapshots, latest_tdcc_date = self.tdcc_latest_client.fetch_latest_market_snapshots()
        if latest_tdcc_date is None:
            raise RuntimeError("TDCC 最新快照沒有可用日期")

        available_tdcc_dates = self.tdcc_history_client.get_available_dates()
        warnings: list[str] = []
        target_tdcc_dates = self._resolve_target_tdcc_dates(
            available_tdcc_dates, latest_tdcc_date, start_date=start_date, warnings=warnings
        )
        if len(target_tdcc_dates) < self.screen_params.min_history_weeks:
            warnings.append("TDCC 可用週數少於最低需求，結果可能不完整。")

        universe_codes = [code for code in listed_stocks if code in latest_snapshots]
        if stock_limit is not None:
            universe_codes = universe_codes[:stock_limit]

        missing_latest_tdcc = len(listed_stocks) - len([code for code in listed_stocks if code in latest_snapshots])
        if missing_latest_tdcc > 0:
            warnings.append(f"有 {missing_latest_tdcc} 檔上市股票缺少最新 TDCC 快照。")

        # 回測模式：以 target_tdcc_dates[0] 為錨點，動態計算需要抓多久的價格資料
        effective_as_of_date: date | None = None
        effective_price_months = self.screen_params.price_history_months
        if start_date is not None and target_tdcc_dates:
            effective_as_of_date = target_tdcc_dates[0]
            today = date.today()
            months_since_anchor = (today.year - effective_as_of_date.year) * 12 + (today.month - effective_as_of_date.month) + 1
            effective_price_months = max(self.screen_params.price_history_months, min(months_since_anchor + 4, 24))

        results: list[StockScreenResult] = []
        for index, stock_code in enumerate(universe_codes, start=1):
            result = self._screen_single_stock(
                stock_info=listed_stocks[stock_code],
                latest_snapshot=latest_snapshots[stock_code],
                target_tdcc_dates=target_tdcc_dates,
                as_of_date=effective_as_of_date,
                effective_price_months=effective_price_months,
            )
            results.append(result)
            if index % 50 == 0 or index == len(universe_codes):
                self.logger.info("已處理 %s/%s 檔股票", index, len(universe_codes))

        summary = self._build_run_summary(
            results=results,
            total_universe=len(listed_stocks),
            latest_tdcc_date=latest_tdcc_date,
            target_tdcc_dates=target_tdcc_dates,
            market_counts=dict(Counter(stock.market for stock in listed_stocks.values())),
            skipped_count=(len(listed_stocks) - len(universe_codes)),
            warnings=warnings,
            elapsed_seconds=time.perf_counter() - started_at,
            backtest_anchor_date=effective_as_of_date,
        )
        return results, summary

    def _load_stock_universe(self, markets: tuple[str, ...]) -> dict[str, StockInfo]:
        """依市場選項載入上市與上櫃股票 universe。"""

        universe: dict[str, StockInfo] = {}
        if "listed" in markets:
            universe.update(self.twse_client.fetch_listed_stocks())
        if "otc" in markets and self.tpex_client is not None:
            otc_stocks = self.tpex_client.fetch_otc_stocks()
            duplicate_codes = sorted(set(universe).intersection(otc_stocks))
            for stock_code in duplicate_codes:
                self.logger.warning("上市與上櫃清單發現重複代號，保留先載入者：%s", stock_code)
                otc_stocks.pop(stock_code, None)
            universe.update(otc_stocks)
        return dict(sorted(universe.items()))

    def _resolve_group_bucket_ids(self) -> dict[str, list[int]]:
        """依分級 metadata 自動推導三組持股區間對應的 bucket ids。"""

        resolved: dict[str, list[int]] = {}
        for group_key, rule in TDCC_HOLDER_GROUPS.items():
            bucket_ids: list[int] = []
            for bucket_id, definition in self.bucket_definitions.items():
                if definition["is_adjustment"] or definition["is_total"]:
                    continue
                min_shares = definition["min_shares"]
                max_shares = definition["max_shares"]
                selector_mode = rule["selector_mode"]
                if selector_mode == "upper_bound" and max_shares is not None and max_shares <= rule["max_shares"]:
                    bucket_ids.append(bucket_id)
                elif selector_mode == "inside_range" and min_shares is not None and max_shares is not None:
                    if min_shares >= rule["min_shares"] and max_shares <= rule["max_shares"]:
                        bucket_ids.append(bucket_id)
                elif selector_mode == "lower_bound" and min_shares is not None and min_shares >= rule["min_shares"]:
                    bucket_ids.append(bucket_id)
            resolved[group_key] = sorted(bucket_ids)
        return resolved

    def _resolve_target_tdcc_dates(
        self,
        available_dates: list[date],
        latest_tdcc_date: date,
        start_date: date | None = None,
        warnings: list[str] | None = None,
    ) -> list[date]:
        """依 anchor 日期（預設為 latest_tdcc_date 或使用者指定的 start_date）與可選日期列表決定本次要追的週數。

        若使用者提供 `start_date`，會以該日期為錨點向回選取最近 N 週；若 `start_date` 晚於最新 TDCC，會回退使用 latest_tdcc_date 並加入 warning（若提供 warnings list）。
        """

        required_weeks = max(self.screen_params.consecutive_weeks, self.screen_params.min_history_weeks)
        # 決定 anchor（以 start_date 為優先，但不超過 latest_tdcc_date）
        anchor = latest_tdcc_date
        if start_date is not None:
            if start_date > latest_tdcc_date:
                if warnings is not None:
                    warnings.append("指定回測起始日晚於最新 TDCC，使用最新 TDCC 日期作為 anchor。")
                anchor = latest_tdcc_date
            else:
                anchor = start_date

        ordered_dates = sorted({value for value in available_dates if value <= anchor}, reverse=True)
        # 若沒有可用的日期（例如 start_date 早於所有可用日期），退回到可用清單並提示
        if not ordered_dates:
            if warnings is not None:
                warnings.append("指定回測起始日早於可用 TDCC 最早日期，改為使用可用日期列表。")
            ordered_dates = sorted(available_dates, reverse=True)

        # 若沒有指定 start_date，保留原行為：確保 latest_tdcc_date 在序列中
        if start_date is None and latest_tdcc_date not in ordered_dates:
            ordered_dates.insert(0, latest_tdcc_date)

        return ordered_dates[:required_weeks]

    def _screen_single_stock(
        self,
        stock_info: StockInfo,
        latest_snapshot: ShareholdingSnapshot,
        target_tdcc_dates: list[date],
        as_of_date: date | None = None,
        effective_price_months: int | None = None,
    ) -> StockScreenResult:
        """執行單一股票的籌碼與價格篩選。"""

        result = StockScreenResult(code=stock_info.code, name=stock_info.short_name or stock_info.name, market=stock_info.market)
        snapshots = self._load_tdcc_snapshots(
            stock_info=stock_info,
            latest_snapshot=latest_snapshot,
            target_tdcc_dates=target_tdcc_dates,
            result=result,
        )
        result.tdcc_dates = [snapshot.data_date for snapshot in snapshots]
        result.latest_tdcc_date = snapshots[0].data_date if snapshots else None
        result.tdcc_weeks_loaded = len(snapshots)

        small_trend = self._build_holder_trend(snapshots, "small_holders")
        mid_trend = self._build_holder_trend(snapshots, "mid_holders")
        large_trend = self._build_holder_trend(snapshots, "large_holders")
        result.small_holder_trend = small_trend
        result.mid_holder_trend = mid_trend
        result.large_holder_trend = large_trend

        if len(snapshots) < self.screen_params.min_history_weeks:
            result.fail_reasons.append("TDCC 週資料不足")
            result.passed = False
            return result

        if any(value is None for value in small_trend):
            result.fail_reasons.append("小股東分級資料缺漏")
        if any(value is None for value in mid_trend):
            result.fail_reasons.append("400~800 張分級資料缺漏")
        if any(value is None for value in large_trend):
            result.fail_reasons.append("大戶分級資料缺漏")
        if result.fail_reasons:
            result.passed = False
            return result

        if not self._is_strict_monotonic(small_trend, direction="decrease"):
            result.fail_reasons.append("小股東未連續下降")
        if not self._is_strict_monotonic(mid_trend, direction="increase"):
            result.fail_reasons.append("400~800 張未連續增加")
        if not self._is_strict_monotonic(large_trend, direction="increase"):
            result.fail_reasons.append("大戶未連續增加")

        result.passed_shareholding = len(result.fail_reasons) == 0
        if not result.passed_shareholding:
            result.passed = False
            return result

        price_months = effective_price_months if effective_price_months is not None else self.screen_params.price_history_months
        price_client = self._resolve_price_client(stock_info.market)
        price_bars = price_client.fetch_stock_day_history(
            stock_code=stock_info.code,
            months=price_months,
        )
        result.price_days_loaded = len(price_bars)
        self._apply_price_filters(price_bars=price_bars, result=result, as_of_date=as_of_date)
        # 回測模式：計算篩選日後的遠期報酬率（僅針對通過籌碼條件的股票）
        if as_of_date is not None and result.passed_shareholding:
            result.forward_returns = self._compute_forward_returns(price_bars=price_bars, anchor_date=as_of_date)
        result.passed = result.passed_shareholding and result.passed_price and len(result.fail_reasons) == 0
        return result

    def _resolve_price_client(self, market: str) -> TWSEApiClient | TPEXApiClient:
        """依股票市場選擇對應的價格資料 client。"""

        if market == "上市":
            return self.twse_client
        if market == "上櫃" and self.tpex_client is not None:
            return self.tpex_client
        raise RuntimeError(f"找不到市場對應的價格資料 client：{market}")

    def _load_tdcc_snapshots(
        self,
        stock_info: StockInfo,
        latest_snapshot: ShareholdingSnapshot,
        target_tdcc_dates: list[date],
        result: StockScreenResult,
    ) -> list[ShareholdingSnapshot]:
        """依目標日期序列抓取單一股票的所有 TDCC 週資料。"""

        snapshots: list[ShareholdingSnapshot] = []
        for target_date in target_tdcc_dates:
            if latest_snapshot.data_date == target_date:
                latest_snapshot.stock_name = stock_info.short_name or stock_info.name
                snapshots.append(latest_snapshot)
                continue
            try:
                historical_snapshot = self.tdcc_history_client.fetch_snapshot(stock_code=stock_info.code, query_date=target_date)
                historical_snapshot.stock_name = stock_info.short_name or stock_info.name
                snapshots.append(historical_snapshot)
            except DataNotFoundError:
                result.source_notes.append(f"TDCC 缺少 {target_date.isoformat()} 週資料")
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("TDCC 歷史資料抓取失敗：%s %s (%s)", stock_info.code, target_date.isoformat(), exc)
                result.source_notes.append(f"TDCC 歷史查詢失敗：{target_date.isoformat()}")

        snapshots.sort(key=lambda snapshot: snapshot.data_date, reverse=True)
        return snapshots

    def _build_holder_trend(self, snapshots: list[ShareholdingSnapshot], group_key: str) -> list[int | None]:
        """將連續週快照聚合成單一持股族群的人數序列。"""

        return [self._aggregate_holder_count(snapshot, group_key) for snapshot in snapshots[: self.screen_params.consecutive_weeks]]

    def _aggregate_holder_count(self, snapshot: ShareholdingSnapshot, group_key: str) -> int | None:
        """依分級 metadata 將同一族群的持有人數加總。"""

        bucket_map = snapshot.bucket_map()
        values: list[int] = []
        for bucket_id in self.group_bucket_ids[group_key]:
            bucket = bucket_map.get(bucket_id)
            if bucket is None or bucket.holder_count is None:
                return None
            values.append(bucket.holder_count)
        return sum(values)

    def _is_strict_monotonic(self, values: list[int | None], direction: str) -> bool:
        """判斷最近 N 週是否符合嚴格單調變化。"""

        if any(value is None for value in values) or len(values) < self.screen_params.consecutive_weeks:
            return False
        normalized = [int(value) for value in values if value is not None]
        if direction == "increase":
            return all(normalized[index] > normalized[index + 1] for index in range(len(normalized) - 1))
        return all(normalized[index] < normalized[index + 1] for index in range(len(normalized) - 1))

    def _apply_price_filters(self, price_bars: list[PriceBar], result: StockScreenResult, as_of_date: date | None = None) -> None:
        """計算價格指標並套用近三個月漲幅與月線距離條件。回測模式下只使用 as_of_date 當天或之前的資料。"""

        fail_count_before = len(result.fail_reasons)
        bars_for_screen = [bar for bar in price_bars if as_of_date is None or bar.trade_date <= as_of_date]
        valid_bars = [bar for bar in bars_for_screen if bar.close_price is not None]
        if len(valid_bars) < self.screen_params.min_price_days:
            result.fail_reasons.append("價格資料不足")
            result.passed_price = False
            return

        latest_bar = valid_bars[-1]
        latest_close = latest_bar.close_price
        if latest_close is None:
            result.fail_reasons.append("最新收盤價缺漏")
            result.passed_price = False
            return

        ma_window_bars = valid_bars[-self.screen_params.ma_window :]
        if len(ma_window_bars) < self.screen_params.ma_window:
            result.fail_reasons.append("20MA 所需日數不足")
            result.passed_price = False
            return

        base_window = valid_bars[-self.screen_params.min_price_days :]
        base_close = base_window[0].close_price
        if base_close in {None, 0}:
            result.fail_reasons.append("近三個月基準價缺漏")
            result.passed_price = False
            return

        ma20 = sum(bar.close_price for bar in ma_window_bars if bar.close_price is not None) / self.screen_params.ma_window
        result.latest_close = latest_close
        result.latest_price_date = latest_bar.trade_date
        result.three_month_return = latest_close / base_close - 1
        result.distance_to_ma20 = abs(latest_close - ma20) / ma20 if ma20 else None

        if result.three_month_return is None:
            result.fail_reasons.append("無法計算近三個月漲幅")
        elif result.three_month_return > self.screen_params.max_3m_return:
            result.fail_reasons.append("近三個月漲幅過熱")

        if result.distance_to_ma20 is None:
            result.fail_reasons.append("無法計算 20MA 距離")
        elif result.distance_to_ma20 > self.screen_params.max_distance_to_ma:
            result.fail_reasons.append("股價距月線過遠")

        result.passed_price = len(result.fail_reasons) == fail_count_before

    def _compute_forward_returns(
        self,
        price_bars: list[PriceBar],
        anchor_date: date,
        windows_days: tuple[int, ...] = (30, 90),
    ) -> dict[int, float | None]:
        """計算從 anchor_date 起 N 日後的報酬率，用於回測策略驗證。"""

        valid_bars = [bar for bar in price_bars if bar.close_price is not None]
        if not valid_bars:
            return {w: None for w in windows_days}

        # 錨點價格：anchor_date 當天或其後最近一個有收盤價的交易日
        anchor_bars = [bar for bar in valid_bars if bar.trade_date >= anchor_date]
        if not anchor_bars:
            return {w: None for w in windows_days}
        anchor_price = anchor_bars[0].close_price
        if not anchor_price:
            return {w: None for w in windows_days}

        result: dict[int, float | None] = {}
        for days in windows_days:
            target_date = anchor_date + timedelta(days=days)
            future_bars = [bar for bar in valid_bars if bar.trade_date >= target_date]
            if not future_bars:
                result[days] = None
            else:
                future_price = future_bars[0].close_price
                result[days] = (future_price / anchor_price - 1) if future_price else None
        return result

    def _build_run_summary(
        self,
        results: list[StockScreenResult],
        total_universe: int,
        latest_tdcc_date: date | None,
        target_tdcc_dates: list[date],
        market_counts: dict[str, int],
        skipped_count: int,
        warnings: list[str],
        elapsed_seconds: float,
        backtest_anchor_date: date | None = None,
    ) -> ScreenRunSummary:
        """彙整整次篩選執行的摘要資訊。"""

        failure_counter: Counter[str] = Counter()
        for result in results:
            if not result.passed:
                failure_counter[self._categorize_failure(result.fail_reasons)] += 1

        return ScreenRunSummary(
            run_timestamp=datetime.now(),
            total_universe=total_universe,
            latest_tdcc_date=latest_tdcc_date,
            target_tdcc_dates=target_tdcc_dates,
            market_counts=market_counts,
            processed_count=len(results),
            passed_count=sum(1 for result in results if result.passed),
            failed_count=sum(1 for result in results if not result.passed),
            skipped_count=skipped_count,
            price_checked_count=sum(1 for result in results if result.price_days_loaded > 0),
            elapsed_seconds=elapsed_seconds,
            failure_category_counts=dict(failure_counter),
            warnings=warnings,
            backtest_anchor_date=backtest_anchor_date,
        )

    def _categorize_failure(self, fail_reasons: list[str]) -> str:
        """將失敗原因壓成適合 summary 的分類名稱。"""

        if not fail_reasons:
            return "未分類"
        primary_reason = fail_reasons[0]
        return primary_reason.split("：", 1)[0].split(":", 1)[0]


def build_output_frames(results: list[StockScreenResult], summary: ScreenRunSummary) -> dict[str, pd.DataFrame]:
    """將結果與摘要轉成適合 CLI 與 Excel 輸出的 DataFrame。"""

    display_rows = [_build_display_row(result) for result in results]
    display_frame = pd.DataFrame(display_rows).sort_values(by=["是否通過篩選", "股票代號"], ascending=[False, True]) if display_rows else pd.DataFrame()

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
            "籌碼是否通過": "",
            "價格是否通過": "",
            "是否通過": f"pass={summary.passed_count} fail={summary.failed_count}",
            "失敗原因": "; ".join(f"{key}:{value}" for key, value in summary.failure_category_counts.items()),
            "備註": "; ".join(summary.warnings),
        },
    )
    raw_frame = pd.DataFrame(raw_rows)

    pass_frame = display_frame[display_frame["是否通過篩選"] == "Y"].reset_index(drop=True) if not display_frame.empty else pd.DataFrame(columns=_display_columns())
    fail_frame = display_frame[display_frame["是否通過篩選"] == "N"].reset_index(drop=True) if not display_frame.empty else pd.DataFrame(columns=_display_columns())
    frames: dict[str, pd.DataFrame] = {"pass": pass_frame, "fail": fail_frame, "raw_data_summary": raw_frame}
    # 若有回測遠期報酬資料，加入 backtest frame（只包含通過的股票，含數值報酬率）
    if any(result.forward_returns for result in results):
        backtest_rows = [_build_backtest_row(result) for result in results if result.passed]
        frames["backtest"] = pd.DataFrame(backtest_rows) if backtest_rows else pd.DataFrame(columns=["股票代號", "股票名稱", "篩選日收盤價", "1個月後報酬率", "3個月後報酬率"])
    return frames


def _build_display_row(result: StockScreenResult) -> dict[str, Any]:
    """將單一結果轉成終端機與 Excel 共用顯示列。"""

    return {
        "市場": result.market,
        "股票代號": result.code,
        "股票名稱": result.name,
        "最新收盤價": "" if result.latest_close is None else round(result.latest_close, 2),
        "近3個月漲幅": format_ratio_as_pct(result.three_month_return),
        "距離20MA百分比": format_ratio_as_pct(result.distance_to_ma20),
        "小於10張人數最近N週趨勢": format_trend_values(result.small_holder_trend),
        "400~800張人數最近N週趨勢": format_trend_values(result.mid_holder_trend),
        "大於1000張人數最近N週趨勢": format_trend_values(result.large_holder_trend),
        "是否通過篩選": "Y" if result.passed else "N",
        "不通過原因": "；".join(result.fail_reasons),
        "1個月後報酬": format_ratio_as_pct(result.forward_returns.get(30)),
        "3個月後報酬": format_ratio_as_pct(result.forward_returns.get(90)),
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
        "籌碼是否通過": "Y" if result.passed_shareholding else "N",
        "價格是否通過": "Y" if result.passed_price else "N",
        "是否通過": "Y" if result.passed else "N",
        "失敗原因": "；".join(result.fail_reasons),
        "備註": "；".join(result.source_notes),
    }


def _build_backtest_row(result: StockScreenResult) -> dict[str, Any]:
    """將通過篩選的結果轉成回測績效驗證列（含數值報酬率，供統計計算）。"""

    return {
        "股票代號": result.code,
        "股票名稱": result.name,
        "市場": result.market,
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
        "最新收盤價",
        "近3個月漲幅",
        "距離20MA百分比",
        "小於10張人數最近N週趨勢",
        "400~800張人數最近N週趨勢",
        "大於1000張人數最近N週趨勢",
        "是否通過篩選",
        "不通過原因",
        "1個月後報酬",
        "3個月後報酬",
    ]