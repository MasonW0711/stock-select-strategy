from __future__ import annotations

import argparse
from io import BytesIO
from datetime import date
from pathlib import Path

import pandas as pd

from api_clients import TDCCOpenApiClient, TDCCPortalHistoryClient, TPEXApiClient, TWSEApiClient
from config import (
    DEFAULT_CACHE_SETTINGS,
    DEFAULT_HTTP_SETTINGS,
    DEFAULT_MARKETS,
    DEFAULT_OUTPUT_BASENAME,
    DEFAULT_SCREEN_PARAMETERS,
    CacheSettings,
    ScreenParameters,
)
from screener import StockScreener, build_output_frames
from utils import setup_logging, parse_tdcc_or_twse_date


def build_argument_parser() -> argparse.ArgumentParser:
    """建立 CLI 參數解析器。"""

    parser = argparse.ArgumentParser(description="台股籌碼選股工具")
    parser.add_argument("--stock-limit", type=int, default=None, help="限制要處理的股票數量，方便 smoke test")
    parser.add_argument("--weeks", type=int, default=DEFAULT_SCREEN_PARAMETERS.consecutive_weeks, help="連續觀察週數")
    parser.add_argument("--max-3m-return", type=float, default=DEFAULT_SCREEN_PARAMETERS.max_3m_return, help="近三個月最大漲幅上限，例如 0.4")
    parser.add_argument("--max-distance-to-ma", type=float, default=DEFAULT_SCREEN_PARAMETERS.max_distance_to_ma, help="距離 20MA 最大允許比例，例如 0.08")
    parser.add_argument("--min-price-days", type=int, default=DEFAULT_SCREEN_PARAMETERS.min_price_days, help="最低價格資料日數")
    parser.add_argument("--price-history-months", type=int, default=DEFAULT_SCREEN_PARAMETERS.price_history_months, help="往回抓取 TWSE 月資料的月份數")
    parser.add_argument("--markets", type=str, default=",".join(DEFAULT_MARKETS), help="市場範圍，例如 listed,otc 或 listed")
    parser.add_argument("--disable-cache", action="store_true", help="停用本地磁碟快取")
    parser.add_argument("--cache-dir", type=str, default=DEFAULT_CACHE_SETTINGS.cache_dir, help="指定快取資料夾路徑")
    parser.add_argument("--log-level", type=str, default="INFO", help="logging level，例如 INFO 或 DEBUG")
    parser.add_argument("--output", type=str, default="", help="指定輸出的 xlsx 檔名")
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="回測起始日期（選填，格式 YYYYMMDD 或 YYYY-MM-DD）",
    )
    return parser


def build_screen_parameters(args: argparse.Namespace) -> ScreenParameters:
    """依 CLI 參數覆寫預設選股設定。"""

    return ScreenParameters(
        consecutive_weeks=args.weeks,
        max_3m_return=args.max_3m_return,
        ma_window=DEFAULT_SCREEN_PARAMETERS.ma_window,
        max_distance_to_ma=args.max_distance_to_ma,
        min_history_weeks=max(args.weeks, DEFAULT_SCREEN_PARAMETERS.min_history_weeks),
        min_price_days=args.min_price_days,
        price_history_months=max(args.price_history_months, 1),
        tdcc_date_buffer_weeks=DEFAULT_SCREEN_PARAMETERS.tdcc_date_buffer_weeks,
        trend_mode=DEFAULT_SCREEN_PARAMETERS.trend_mode,
    )


def parse_markets(markets_argument: str) -> tuple[str, ...]:
    """將 markets 字串轉成標準市場代號序列。"""

    normalized = [value.strip().lower() for value in markets_argument.split(",") if value.strip()]
    resolved = [value for value in normalized if value in {"listed", "otc"}]
    return tuple(dict.fromkeys(resolved)) or DEFAULT_MARKETS


def build_cache_settings(args: argparse.Namespace) -> CacheSettings:
    """依 CLI 參數建立 cache 設定。"""

    return CacheSettings(
        enabled=not args.disable_cache,
        cache_dir=args.cache_dir,
        universe_ttl_seconds=DEFAULT_CACHE_SETTINGS.universe_ttl_seconds,
        latest_snapshot_ttl_seconds=DEFAULT_CACHE_SETTINGS.latest_snapshot_ttl_seconds,
        tdcc_dates_ttl_seconds=DEFAULT_CACHE_SETTINGS.tdcc_dates_ttl_seconds,
        price_ttl_seconds=DEFAULT_CACHE_SETTINGS.price_ttl_seconds,
        historical_snapshot_ttl_seconds=DEFAULT_CACHE_SETTINGS.historical_snapshot_ttl_seconds,
    )


def build_screener(screen_parameters: ScreenParameters, logger: object, cache_settings: CacheSettings, markets: tuple[str, ...]) -> StockScreener:
    """建立本次執行要使用的 screener 與各 market client。"""

    tdcc_latest_client = TDCCOpenApiClient(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=cache_settings, logger=logger)
    tdcc_history_client = TDCCPortalHistoryClient(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=cache_settings, logger=logger)
    twse_client = TWSEApiClient(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=cache_settings, logger=logger)
    tpex_client = TPEXApiClient(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=cache_settings, logger=logger) if "otc" in markets else None
    return StockScreener(
        tdcc_latest_client=tdcc_latest_client,
        tdcc_history_client=tdcc_history_client,
        twse_client=twse_client,
        tpex_client=tpex_client,
        screen_params=screen_parameters,
        logger=logger,
    )


def execute_screening(
    screen_parameters: ScreenParameters,
    logger: object,
    stock_limit: int | None,
    markets: tuple[str, ...],
    cache_settings: CacheSettings,
    start_date: date | None = None,
) -> tuple[list[object], object, dict[str, pd.DataFrame]]:
    """執行一次完整篩選並回傳結果、摘要與輸出 frames。"""

    screener = build_screener(
        screen_parameters=screen_parameters,
        logger=logger,
        cache_settings=cache_settings,
        markets=markets,
    )
    results, summary = screener.run_screening(stock_limit=stock_limit, markets=markets, start_date=start_date)
    frames = build_output_frames(results=results, summary=summary)
    return results, summary, frames


def print_terminal_output(frames: dict[str, pd.DataFrame], summary_text: list[str]) -> None:
    """將摘要與 pass/fail 表格輸出到終端機。"""

    print("\n".join(summary_text))
    print("\n=== PASS ===")
    print(frames["pass"].to_string(index=False) if not frames["pass"].empty else "無符合條件股票")
    print("\n=== FAIL ===")
    print(frames["fail"].to_string(index=False) if not frames["fail"].empty else "無失敗股票")


def build_summary_text(summary: object) -> list[str]:
    """將執行摘要整理成終端機可讀文字。"""

    market_counts = "、".join(f"{market}:{count}" for market, count in summary.market_counts.items()) if getattr(summary, "market_counts", None) else "無"
    # 回測起始日：取 target_tdcc_dates 最早的日期（若有）
    start_date_str = ""
    if getattr(summary, "target_tdcc_dates", None):
        try:
            start_date_str = summary.target_tdcc_dates[-1].isoformat()
        except Exception:
            start_date_str = ""

    return [
        f"執行時間：{summary.run_timestamp.strftime('%Y-%m-%d %H:%M:%S')}",
        f"市場股票總數：{summary.total_universe}",
        f"市場分布：{market_counts}",
        f"實際處理檔數：{summary.processed_count}",
        f"共分析檔數：{summary.processed_count}",
        f"略過檔數：{summary.skipped_count}",
        f"價格檢查檔數：{summary.price_checked_count}",
        f"通過檔數：{summary.passed_count}",
        f"共篩選出檔數：{summary.passed_count}",
        f"失敗檔數：{summary.failed_count}",
        f"最新 TDCC 日期：{summary.latest_tdcc_date.isoformat() if summary.latest_tdcc_date else ''}",
        f"TDCC 觀察日期：{', '.join(value.isoformat() for value in summary.target_tdcc_dates)}",
        f"回測起始日：{start_date_str}",
        f"耗時秒數：{summary.elapsed_seconds:.2f}",
        f"警告：{'; '.join(summary.warnings) if summary.warnings else '無'}",
    ]


def resolve_output_path(output_argument: str) -> Path:
    """決定本次執行要輸出的 xlsx 路徑。"""

    if output_argument:
        return Path(output_argument).expanduser().resolve()
    return Path.cwd() / f"{DEFAULT_OUTPUT_BASENAME}_{date.today():%Y%m%d}.xlsx"


def frames_to_excel_bytes(frames: dict[str, pd.DataFrame]) -> bytes:
    """將 pass、fail、raw_data_summary 轉成 xlsx bytes。"""

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for sheet_name, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet_name, index=False)
    return buffer.getvalue()


def export_excel(frames: dict[str, pd.DataFrame], output_path: Path) -> None:
    """將 pass、fail、raw_data_summary 輸出成 xlsx。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(frames_to_excel_bytes(frames))


def main() -> int:
    """CLI 入口，負責串接參數、執行篩選與輸出結果。"""

    parser = build_argument_parser()
    args = parser.parse_args()
    logger = setup_logging(args.log_level)

    screen_parameters = build_screen_parameters(args)
    markets = parse_markets(args.markets)
    cache_settings = build_cache_settings(args)
    start_date = parse_tdcc_or_twse_date(args.start_date) if args.start_date else None

    _, summary, frames = execute_screening(
        screen_parameters=screen_parameters,
        logger=logger,
        stock_limit=args.stock_limit,
        markets=markets,
        cache_settings=cache_settings,
        start_date=start_date,
    )
    print_terminal_output(frames=frames, summary_text=build_summary_text(summary))

    output_path = resolve_output_path(args.output)
    export_excel(frames=frames, output_path=output_path)
    print(f"\n已輸出 Excel：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())