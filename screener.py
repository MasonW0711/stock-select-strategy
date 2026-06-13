from __future__ import annotations

import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

from api_clients import ApiClientError, DataNotFoundError, TDCCOpenApiClient, TDCCPortalHistoryClient, TPEXApiClient, TWSEApiClient
from config import PRICE_DISCONTINUITY_ALERT_RATIO, ScreenParameters, TDCC_BUCKET_DEFINITIONS, TDCC_HOLDER_GROUPS
from models import PriceBar, ScreenRunSummary, ShareholdingSnapshot, StockInfo, StockScreenResult
from scoring import calculate_score, get_score_label


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
        price_fetch_workers: int = 1,
    ) -> None:
        """初始化資料 client、參數與分級設定。

        price_fetch_workers > 1 時，run_screening 會「先序列跑籌碼關（TDCC client 具狀態、
        非執行緒安全），再對需要抓價的子集併發抓價」；預設 1 維持純序列行為。
        """

        self.tdcc_latest_client = tdcc_latest_client
        self.tdcc_history_client = tdcc_history_client
        self.twse_client = twse_client
        self.tpex_client = tpex_client
        self.screen_params = screen_params
        self.logger = logger or logging.getLogger("stock_screener")
        self.price_fetch_workers = max(1, price_fetch_workers)
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

        eligible_stocks = listed_stocks
        if start_date is not None:
            eligible_stocks, excluded_future_listing_count, unknown_listing_date_count = self._filter_stock_universe_for_backtest(
                listed_stocks,
                anchor_date=start_date,
            )
            if excluded_future_listing_count > 0:
                warnings.append(f"已依上市/上櫃日期排除 {excluded_future_listing_count} 檔回測日尚未可交易股票。")
            if unknown_listing_date_count > 0:
                warnings.append(f"有 {unknown_listing_date_count} 檔股票缺少上市/上櫃日期，回測時先保留在股票集合中。")

        if start_date is None:
            universe_codes = [code for code in eligible_stocks if code in latest_snapshots]
        else:
            universe_codes = list(eligible_stocks)
        if stock_limit is not None:
            universe_codes = universe_codes[:stock_limit]

        missing_latest_tdcc = len(eligible_stocks) - len([code for code in eligible_stocks if code in latest_snapshots])
        if missing_latest_tdcc > 0:
            if start_date is None:
                warnings.append(f"有 {missing_latest_tdcc} 檔上市股票缺少最新 TDCC 快照。")
            else:
                warnings.append(f"回測模式有 {missing_latest_tdcc} 檔股票缺少最新 TDCC 快照，改由歷史 TDCC 週資料補查。")

        # 回測模式下，TDCC 週資料可向前對齊，但價格篩選與後續報酬仍應以使用者指定日為錨點。
        # 抓價窗需同時涵蓋：anchor 之前足夠的 K 線（>= min_price_days 與 return_lookback_days）、
        # 以及 anchor 之後最大遠期報酬窗（90 天 ≈ 5 個月）。
        # 依「所需交易日數」反推抓價月份下限：約 20 交易日/月，再加 2 個月緩衝吸收假期，
        # 確保即使農曆年等假期密集月份也不會誤判「價格資料不足」。
        trading_days_needed = max(self.screen_params.min_price_days, self.screen_params.return_lookback_days)
        months_floor = trading_days_needed // 20 + 2

        effective_as_of_date: date | None = None
        effective_price_months = max(self.screen_params.price_history_months, months_floor)
        if start_date is not None:
            effective_as_of_date = start_date
            today = date.today()
            months_before_anchor = max(self.screen_params.price_history_months, months_floor)
            months_forward = 5  # 覆蓋 90 天遠期報酬
            months_since_anchor = (today.year - effective_as_of_date.year) * 12 + (today.month - effective_as_of_date.month)
            # iter_recent_month_starts 由 today 往回數，因此要把「今天到 anchor」的距離一併納入
            effective_price_months = min(months_since_anchor + months_before_anchor + months_forward, 48)

        results = self._screen_universe(
            universe_codes=universe_codes,
            eligible_stocks=eligible_stocks,
            latest_snapshots=latest_snapshots,
            target_tdcc_dates=target_tdcc_dates,
            as_of_date=effective_as_of_date,
            effective_price_months=effective_price_months,
        )

        summary = self._build_run_summary(
            results=results,
            total_universe=len(eligible_stocks),
            latest_tdcc_date=latest_tdcc_date,
            target_tdcc_dates=target_tdcc_dates,
            market_counts=dict(Counter(stock.market for stock in eligible_stocks.values())),
            skipped_count=(len(eligible_stocks) - len(universe_codes)),
            warnings=warnings,
            elapsed_seconds=time.perf_counter() - started_at,
            backtest_anchor_date=effective_as_of_date,
            backtest_tdcc_anchor_date=target_tdcc_dates[0] if start_date is not None and target_tdcc_dates else None,
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

    def _filter_stock_universe_for_backtest(
        self,
        universe: dict[str, StockInfo],
        anchor_date: date,
    ) -> tuple[dict[str, StockInfo], int, int]:
        """依回測日期排除尚未上市/上櫃的股票，讓 universe 更接近當時可交易集合。"""

        eligible: dict[str, StockInfo] = {}
        excluded_future_listing_count = 0
        unknown_listing_date_count = 0
        for stock_code, stock_info in universe.items():
            if stock_info.listed_date is None:
                unknown_listing_date_count += 1
                eligible[stock_code] = stock_info
                continue
            if stock_info.listed_date <= anchor_date:
                eligible[stock_code] = stock_info
            else:
                excluded_future_listing_count += 1
        return eligible, excluded_future_listing_count, unknown_listing_date_count

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

        required_weeks = max(
            self.screen_params.consecutive_weeks,
            self.screen_params.min_history_weeks,
            self.screen_params.holder_decrease_weeks + 1,
        )
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

    def _screen_universe(
        self,
        *,
        universe_codes: list[str],
        eligible_stocks: dict[str, StockInfo],
        latest_snapshots: dict[str, ShareholdingSnapshot],
        target_tdcc_dates: list[date],
        as_of_date: date | None,
        effective_price_months: int | None,
    ) -> list[StockScreenResult]:
        """掃描整批股票。

        workers <= 1：純序列（行為與舊版一致，仍走 _screen_single_stock，方便測試 monkeypatch）。
        workers > 1：先序列跑籌碼關（TDCC client 具狀態、非執行緒安全），再對「需要抓價」的子集
        以執行緒池併發抓價；result 物件在原序中就地更新，輸出順序不變。
        """

        total = len(universe_codes)
        if self.price_fetch_workers <= 1:
            results: list[StockScreenResult] = []
            for index, stock_code in enumerate(universe_codes, start=1):
                result = self._screen_single_stock(
                    stock_info=eligible_stocks[stock_code],
                    latest_snapshot=latest_snapshots.get(stock_code),
                    target_tdcc_dates=target_tdcc_dates,
                    as_of_date=as_of_date,
                    effective_price_months=effective_price_months,
                )
                results.append(result)
                if index % 50 == 0 or index == total:
                    self.logger.info("已處理 %s/%s 檔股票", index, total)
            return results

        results = []
        price_jobs: list[tuple[StockScreenResult, StockInfo]] = []
        for index, stock_code in enumerate(universe_codes, start=1):
            stock_info = eligible_stocks[stock_code]
            result, needs_price = self._screen_chip_stage(
                stock_info=stock_info,
                latest_snapshot=latest_snapshots.get(stock_code),
                target_tdcc_dates=target_tdcc_dates,
            )
            results.append(result)
            if needs_price:
                price_jobs.append((result, stock_info))
            if index % 50 == 0 or index == total:
                self.logger.info("已完成籌碼篩選 %s/%s 檔（待抓價 %s 檔）", index, total, len(price_jobs))

        if price_jobs:
            completed = 0
            with ThreadPoolExecutor(max_workers=self.price_fetch_workers) as executor:
                futures = [
                    executor.submit(
                        self._screen_price_stage,
                        result,
                        stock_info,
                        as_of_date=as_of_date,
                        effective_price_months=effective_price_months,
                    )
                    for result, stock_info in price_jobs
                ]
                for future in as_completed(futures):
                    future.result()  # 讓非預期例外浮現（price stage 內已自行吞掉預期錯誤）
                    completed += 1
                    if completed % 25 == 0 or completed == len(price_jobs):
                        self.logger.info("已抓價並計分 %s/%s 檔", completed, len(price_jobs))
        return results

    def _screen_single_stock(
        self,
        stock_info: StockInfo,
        latest_snapshot: ShareholdingSnapshot | None,
        target_tdcc_dates: list[date],
        as_of_date: date | None = None,
        effective_price_months: int | None = None,
    ) -> StockScreenResult:
        """執行單一股票的籌碼與價格篩選（序列路徑；= 籌碼關 + 抓價關）。"""

        result, needs_price = self._screen_chip_stage(
            stock_info=stock_info,
            latest_snapshot=latest_snapshot,
            target_tdcc_dates=target_tdcc_dates,
        )
        if needs_price:
            self._screen_price_stage(
                result,
                stock_info,
                as_of_date=as_of_date,
                effective_price_months=effective_price_months,
            )
        return result

    def _screen_chip_stage(
        self,
        stock_info: StockInfo,
        latest_snapshot: ShareholdingSnapshot | None,
        target_tdcc_dates: list[date],
    ) -> tuple[StockScreenResult, bool]:
        """籌碼／集保關（含 TDCC 歷史抓取，須序列執行）。回傳 (result, 是否需要進入抓價關)。"""

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
        result.large_holder_growth = self._growth_ratio(large_trend)

        if len(snapshots) < self.screen_params.min_history_weeks:
            result.fail_reasons.append("TDCC 週資料不足")
            result.passed = False
            return result, False

        if any(value is None for value in small_trend):
            result.fail_reasons.append("小股東分級資料缺漏")
        if any(value is None for value in mid_trend):
            result.fail_reasons.append("400~800 張分級資料缺漏")
        if any(value is None for value in large_trend):
            result.fail_reasons.append("大戶分級資料缺漏")
        if result.fail_reasons:
            result.passed = False
            return result, False

        if not self._is_strict_monotonic(small_trend, direction="decrease"):
            result.fail_reasons.append("小股東未連續下降")
        if not self._is_strict_monotonic(mid_trend, direction="increase"):
            result.fail_reasons.append("400~800 張未連續增加")
        if not self._is_strict_monotonic(large_trend, direction="increase"):
            result.fail_reasons.append("大戶未連續增加")

        result.passed_shareholding = len(result.fail_reasons) == 0
        if not result.passed_shareholding:
            result.passed = False
            return result, False

        # 集保總戶數下降條件（融合自 stock_chip_selector，改吃 TDCC 合計列總戶數）。
        # 資料週數不足時直接判不通過，不做靜默 fallback，避免短窗訊號被誤標成長窗。
        self._apply_holder_decrease(snapshots=snapshots, result=result)
        if self.screen_params.require_holder_decrease and not result.passed_holder_decrease:
            result.passed = False
            return result, False

        return result, True

    def _screen_price_stage(
        self,
        result: StockScreenResult,
        stock_info: StockInfo,
        as_of_date: date | None = None,
        effective_price_months: int | None = None,
    ) -> None:
        """抓價關：抓日線、套價格條件、回測遠期報酬與評分。就地更新 result，可於執行緒池中平行執行。"""

        price_months = effective_price_months if effective_price_months is not None else self.screen_params.price_history_months
        price_client = self._resolve_price_client(stock_info.market)
        try:
            price_bars = price_client.fetch_stock_day_history(
                stock_code=stock_info.code,
                months=price_months,
            )
        except ApiClientError as exc:
            self.logger.warning("價格資料抓取失敗：%s (%s)", stock_info.code, exc)
            result.fail_reasons.append("價格資料抓取失敗")
            result.source_notes.append(str(exc))
            result.passed_price = False
            result.passed = False
            return
        result.price_days_loaded = len(price_bars)
        self._apply_price_filters(price_bars=price_bars, result=result, as_of_date=as_of_date)
        # 回測模式：計算篩選日後的遠期報酬率（僅針對通過籌碼條件的股票）
        if as_of_date is not None and result.passed_shareholding:
            result.forward_returns = self._compute_forward_returns(price_bars=price_bars, anchor_date=as_of_date)
        holder_ok = result.passed_holder_decrease or not self.screen_params.require_holder_decrease
        result.passed = result.passed_shareholding and result.passed_price and holder_ok and len(result.fail_reasons) == 0
        if result.passed:
            decline_for_score = max(-(result.holder_change_ratio or 0.0), 0.0)
            result.score = calculate_score(
                large_holder_growth=result.large_holder_growth,
                holder_decline=decline_for_score,
                distance_to_ma=result.distance_to_ma20,
                three_month_return=result.three_month_return,
            )
            result.score_label = get_score_label(result.score)

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
        latest_snapshot: ShareholdingSnapshot | None,
        target_tdcc_dates: list[date],
        result: StockScreenResult,
    ) -> list[ShareholdingSnapshot]:
        """依目標日期序列抓取單一股票的所有 TDCC 週資料。"""

        snapshots: list[ShareholdingSnapshot] = []
        # target_tdcc_dates 由新到舊排列。逐週抓取，一旦分級三條任一已不可能通過，
        # 就停止後續週查詢（early-exit），可在全市場掃描時省下大量 TDCC POST。
        for target_date in target_tdcc_dates:
            if latest_snapshot is not None and latest_snapshot.data_date == target_date:
                latest_snapshot.stock_name = stock_info.short_name or stock_info.name
                snapshots.append(latest_snapshot)
            else:
                try:
                    historical_snapshot = self.tdcc_history_client.fetch_snapshot(stock_code=stock_info.code, query_date=target_date)
                    historical_snapshot.stock_name = stock_info.short_name or stock_info.name
                    snapshots.append(historical_snapshot)
                except DataNotFoundError:
                    result.source_notes.append(f"TDCC 缺少 {target_date.isoformat()} 週資料")
                except ApiClientError as exc:
                    # 預期內的網路/解析失敗：單檔略過、不中斷整批掃描。
                    self.logger.warning("TDCC 歷史資料抓取失敗：%s %s (%s)", stock_info.code, target_date.isoformat(), exc)
                    result.source_notes.append(f"TDCC 歷史查詢失敗：{target_date.isoformat()}")
                except Exception:  # noqa: BLE001
                    # 非預期錯誤（疑似程式 bug）：保住整批進度，但用 exc_info 印出完整 traceback，
                    # 避免被當成「正常的單檔失敗」而靜默吞掉。
                    self.logger.warning(
                        "TDCC 歷史資料發生非預期錯誤：%s %s", stock_info.code, target_date.isoformat(), exc_info=True
                    )
                    result.source_notes.append(f"TDCC 歷史查詢非預期錯誤：{target_date.isoformat()}")

            if self._shareholding_trend_doomed(snapshots):
                result.source_notes.append("分級趨勢提前判定不符，已略過後續週查詢")
                break

        snapshots.sort(key=lambda snapshot: snapshot.data_date, reverse=True)
        return snapshots

    def _shareholding_trend_doomed(self, snapshots: list[ShareholdingSnapshot]) -> bool:
        """已抓到的最新數週中，分級三條任一已不可能再符合 → 可提前結束抓取。"""

        if len(snapshots) < 2:
            return False
        window = sorted(snapshots, key=lambda snapshot: snapshot.data_date, reverse=True)[: self.screen_params.consecutive_weeks]
        checks = (("small_holders", "decrease"), ("mid_holders", "increase"), ("large_holders", "increase"))
        for group_key, direction in checks:
            values = [self._aggregate_holder_count(snapshot, group_key) for snapshot in window]
            if not self._prefix_can_still_pass(values, direction):
                return True
        return False

    @staticmethod
    def _prefix_can_still_pass(values: list[int | None], direction: str) -> bool:
        """判斷由新到舊的人數前綴是否仍有機會在補滿後維持嚴格單調。"""

        if any(value is None for value in values):
            return False
        if direction == "increase":
            return all(values[index] > values[index + 1] for index in range(len(values) - 1))
        return all(values[index] < values[index + 1] for index in range(len(values) - 1))

    @staticmethod
    def _growth_ratio(trend: list[int | None]) -> float | None:
        """以「最新 vs 觀察窗最舊」計算族群人數增幅 ratio（新到舊序列）。"""

        valid = [value for value in trend if value is not None]
        if len(valid) < 2 or not valid[-1]:
            return None
        return (valid[0] - valid[-1]) / valid[-1]

    def _apply_holder_decrease(self, snapshots: list[ShareholdingSnapshot], result: StockScreenResult) -> None:
        """套用集保總戶數近 N 週下降條件；資料不足直接判不通過，不靜默 fallback。"""

        weeks = self.screen_params.holder_decrease_weeks
        totals = [snapshot.total_holder_count() for snapshot in snapshots]
        result.total_holder_trend = totals[: weeks + 1]

        if len(totals) < weeks + 1 or totals[0] is None or totals[weeks] is None:
            result.passed_holder_decrease = False
            if self.screen_params.require_holder_decrease:
                result.fail_reasons.append("集保總戶數資料不足")
            return

        latest_total, early_total = totals[0], totals[weeks]
        result.latest_total_holders = latest_total
        result.early_total_holders = early_total
        if early_total == 0:
            result.passed_holder_decrease = False
            if self.screen_params.require_holder_decrease:
                result.fail_reasons.append("集保總戶數基準為 0")
            return

        change_ratio = (latest_total - early_total) / early_total
        result.holder_change_ratio = change_ratio
        decline = -change_ratio  # 下降時為正
        passed = decline > 0 and decline >= self.screen_params.min_holder_decrease_ratio
        result.passed_holder_decrease = passed
        if not passed and self.screen_params.require_holder_decrease:
            result.fail_reasons.append("集保總戶數未下降" if decline <= 0 else "集保總戶數下降幅度不足")

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

        # 報酬回看窗與「最低資料量」解耦：用 return_lookback_days 決定漲幅基準，
        # 資料不足該窗時退回目前最早一根，避免把最低資料量參數誤當回看窗。
        lookback = min(self.screen_params.return_lookback_days, len(valid_bars))
        base_close = valid_bars[-lookback].close_price
        if base_close in {None, 0}:
            result.fail_reasons.append("近三個月基準價缺漏")
            result.passed_price = False
            return

        ma20 = sum(bar.close_price for bar in ma_window_bars if bar.close_price is not None) / self.screen_params.ma_window
        result.latest_close = latest_close
        result.latest_price_date = latest_bar.trade_date
        result.three_month_return = latest_close / base_close - 1
        result.distance_to_ma20 = abs(latest_close - ma20) / ma20 if ma20 else None

        # 價格未還原（TWSE/TPEX 原始收盤）：偵測回看窗內是否有超過台股單日漲跌幅上限的跳空，
        # 這幾乎只可能來自除權息/減資等公司行動，會使均線與報酬失真，標註以提醒人工判讀。
        self._flag_price_discontinuity(valid_bars[-lookback:], result)

        # three_month_return 已由非零 base_close 計算，必為數值（不需 None 判斷）；
        # distance_to_ma20 僅在 ma20 為 0（資料異常）時才會是 None，故保留該防呆。
        if result.three_month_return > self.screen_params.max_3m_return:
            result.fail_reasons.append("近三個月漲幅過熱")

        if result.distance_to_ma20 is None:
            result.fail_reasons.append("無法計算 20MA 距離")
        elif result.distance_to_ma20 > self.screen_params.max_distance_to_ma:
            result.fail_reasons.append("股價距月線過遠")

        result.passed_price = len(result.fail_reasons) == fail_count_before

    @staticmethod
    def _flag_price_discontinuity(window_bars: list[PriceBar], result: StockScreenResult) -> None:
        """偵測回看窗內最大單日跳空；超過警示門檻時於 source_notes 標註（不影響 pass/fail）。"""

        max_jump = 0.0
        for previous_bar, current_bar in zip(window_bars, window_bars[1:]):
            if previous_bar.close_price and current_bar.close_price:
                jump = abs(current_bar.close_price / previous_bar.close_price - 1)
                max_jump = max(max_jump, jump)
        if max_jump >= PRICE_DISCONTINUITY_ALERT_RATIO:
            result.source_notes.append(
                f"價格區間含 {max_jump * 100:.0f}% 單日跳空，可能為除權息/減資（價格未還原，均線與報酬恐失真）"
            )

    def _compute_forward_returns(
        self,
        price_bars: list[PriceBar],
        anchor_date: date,
        windows_days: tuple[int, ...] = (30, 90),
        max_gap_days: int = 15,
    ) -> dict[int, float | None]:
        """計算從 anchor_date 起 N 日後的報酬率，用於回測策略驗證。

        只接受落在 [target, target + max_gap_days] 內的第一根交易日；若最接近的未來
        交易日距目標超過 max_gap_days（例如停牌、下市造成長缺口），回傳 None，避免把
        遠超視窗的 bar 誤標成「1/3 個月後報酬」。max_gap_days 預設 15 天可吸收農曆年連假。
        """

        valid_bars = [bar for bar in price_bars if bar.close_price is not None]
        if not valid_bars:
            return {w: None for w in windows_days}

        # 錨點 = 篩選日當天或其前最近一個有收盤價的交易日，與 _apply_price_filters 顯示的
        # 「篩選日收盤價」為同一根 bar，確保報酬基準與顯示價一致（含非交易日 anchor）。
        anchor_bars = [bar for bar in valid_bars if bar.trade_date <= anchor_date]
        if not anchor_bars:
            return {w: None for w in windows_days}
        anchor_bar = anchor_bars[-1]
        anchor_price = anchor_bar.close_price
        if not anchor_price:
            return {w: None for w in windows_days}

        result: dict[int, float | None] = {}
        for days in windows_days:
            target_date = anchor_bar.trade_date + timedelta(days=days)
            future_bars = [bar for bar in valid_bars if bar.trade_date >= target_date]
            if not future_bars or (future_bars[0].trade_date - target_date).days > max_gap_days:
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
        backtest_tdcc_anchor_date: date | None = None,
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
            backtest_tdcc_anchor_date=backtest_tdcc_anchor_date,
        )

    def _categorize_failure(self, fail_reasons: list[str]) -> str:
        """將失敗原因壓成適合 summary 的分類名稱。"""

        if not fail_reasons:
            return "未分類"
        primary_reason = fail_reasons[0]
        return primary_reason.split("：", 1)[0].split(":", 1)[0]