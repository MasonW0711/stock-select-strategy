from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(slots=True)
class StockInfo:
    """描述單一股票的基本資料。"""

    code: str
    name: str
    short_name: str
    market: str
    listed_date: date | None
    industry: str | None
    is_ky: bool
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ShareholdingBucketRecord:
    """描述單一持股分級桶的籌碼資料。"""

    bucket_id: int
    label: str
    holder_count: int | None
    share_count: int | None
    ratio: float | None
    is_adjustment: bool = False
    is_total: bool = False


@dataclass(slots=True)
class ShareholdingSnapshot:
    """描述單一股票在特定週別的完整股權分散快照。"""

    stock_code: str
    stock_name: str
    data_date: date
    buckets: list[ShareholdingBucketRecord]
    source: str

    def bucket_map(self) -> dict[int, ShareholdingBucketRecord]:
        """將 buckets 轉成 bucket_id 對照表。"""

        return {bucket.bucket_id: bucket for bucket in self.buckets}

    def total_holder_count(self) -> int | None:
        """回傳合計列的集保總戶數（找不到合計列時為 None）。"""

        for bucket in self.buckets:
            if bucket.is_total:
                return bucket.holder_count
        return None


@dataclass(slots=True)
class PriceBar:
    """描述單一交易日的個股日線資料。"""

    trade_date: date
    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float | None
    volume: int | None
    turnover: int | None


@dataclass(slots=True)
class StockScreenResult:
    """描述單一股票的最終篩選結果與原因。"""

    code: str
    name: str
    market: str = ""
    latest_close: float | None = None
    latest_price_date: date | None = None
    three_month_return: float | None = None
    distance_to_ma20: float | None = None
    small_holder_trend: list[int | None] = field(default_factory=list)
    mid_holder_trend: list[int | None] = field(default_factory=list)
    large_holder_trend: list[int | None] = field(default_factory=list)
    total_holder_trend: list[int | None] = field(default_factory=list)
    latest_total_holders: int | None = None
    early_total_holders: int | None = None
    holder_change_ratio: float | None = None
    large_holder_growth: float | None = None
    passed_holder_decrease: bool = False
    score: int | None = None
    score_label: str = ""
    tdcc_dates: list[date] = field(default_factory=list)
    latest_tdcc_date: date | None = None
    tdcc_weeks_loaded: int = 0
    price_days_loaded: int = 0
    passed_shareholding: bool = False
    passed_price: bool = False
    passed: bool = False
    fail_reasons: list[str] = field(default_factory=list)
    source_notes: list[str] = field(default_factory=list)
    forward_returns: dict[int, float | None] = field(default_factory=dict)

    def primary_fail_reason(self) -> str:
        """回傳最主要的失敗原因。"""

        return self.fail_reasons[0] if self.fail_reasons else ""


@dataclass(slots=True)
class ScreenRunSummary:
    """描述整次執行的摘要資訊。"""

    run_timestamp: datetime
    total_universe: int
    latest_tdcc_date: date | None
    target_tdcc_dates: list[date]
    market_counts: dict[str, int] = field(default_factory=dict)
    processed_count: int = 0
    passed_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    price_checked_count: int = 0
    elapsed_seconds: float = 0.0
    failure_category_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    backtest_anchor_date: date | None = None
    backtest_tdcc_anchor_date: date | None = None