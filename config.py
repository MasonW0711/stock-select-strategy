from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TWSE_BASE_URL = "https://www.twse.com.tw"
TWSE_OPENAPI_BASE_URL = "https://openapi.twse.com.tw/v1"
TPEX_BASE_URL = "https://www.tpex.org.tw"
TDCC_OPENAPI_BASE_URL = "https://openapi.tdcc.com.tw/v1"
TDCC_PORTAL_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"

TWSE_LISTED_COMPANIES_URL = f"{TWSE_OPENAPI_BASE_URL}/opendata/t187ap03_L"
TWSE_STOCK_DAY_URL = f"{TWSE_BASE_URL}/rwd/zh/afterTrading/STOCK_DAY"
TWSE_STOCK_DAY_ALL_URL = f"{TWSE_OPENAPI_BASE_URL}/exchangeReport/STOCK_DAY_ALL"
TPEX_OTC_COMPANIES_URL = f"{TPEX_BASE_URL}/openapi/v1/mopsfin_t187ap03_O"
TPEX_OTC_STOCK_DAY_URL = f"{TPEX_BASE_URL}/www/zh-tw/afterTrading/tradingStock"
TDCC_LATEST_SHAREHOLDING_URL = f"{TDCC_OPENAPI_BASE_URL}/opendata/1-5"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

VALID_LISTED_STOCK_CODE_LENGTH = 4
EXCLUDED_SHORT_NAME_KEYWORDS = ("-創",)
DEFAULT_OUTPUT_BASENAME = "stock_screen_result"
DEFAULT_MARKETS = ("listed", "otc")
MARKET_LABELS = {"listed": "上市", "otc": "上櫃"}


@dataclass(frozen=True)
class HttpSettings:
    """集中管理 HTTP timeout、retry 與節流參數。"""

    timeout_seconds: int = 20
    max_retries: int = 3
    backoff_seconds: float = 1.2
    min_interval_seconds: float = 0.2


@dataclass(frozen=True)
class ScreenParameters:
    """集中管理選股規則與最低資料量需求。"""

    consecutive_weeks: int = 3
    max_3m_return: float = 0.40
    ma_window: int = 20
    max_distance_to_ma: float = 0.08
    min_history_weeks: int = 3
    min_price_days: int = 60
    price_history_months: int = 4
    tdcc_date_buffer_weeks: int = 6
    trend_mode: Literal["strict"] = "strict"
    # 近 N 個交易日報酬回看窗，與 min_price_days（最低資料量）解耦
    return_lookback_days: int = 60
    # 集保總戶數下降條件（融合自原 stock_chip_selector 三關之一，改吃線上 TDCC 合計列）
    require_holder_decrease: bool = True
    holder_decrease_weeks: int = 3
    min_holder_decrease_ratio: float = 0.0


@dataclass(frozen=True)
class CacheSettings:
    """集中管理本地磁碟快取設定。"""

    enabled: bool = True
    cache_dir: str = ".cache"
    universe_ttl_seconds: int = 24 * 60 * 60
    latest_snapshot_ttl_seconds: int = 6 * 60 * 60
    tdcc_dates_ttl_seconds: int = 6 * 60 * 60
    price_ttl_seconds: int = 24 * 60 * 60
    historical_snapshot_ttl_seconds: int = 30 * 24 * 60 * 60


DEFAULT_HTTP_SETTINGS = HttpSettings()
DEFAULT_SCREEN_PARAMETERS = ScreenParameters()
DEFAULT_CACHE_SETTINGS = CacheSettings()

TDCC_BUCKET_DEFINITIONS = (
    {"bucket_id": 1, "label": "1-999", "min_shares": 1, "max_shares": 999, "is_adjustment": False, "is_total": False},
    {"bucket_id": 2, "label": "1,000-5,000", "min_shares": 1_000, "max_shares": 5_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 3, "label": "5,001-10,000", "min_shares": 5_001, "max_shares": 10_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 4, "label": "10,001-15,000", "min_shares": 10_001, "max_shares": 15_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 5, "label": "15,001-20,000", "min_shares": 15_001, "max_shares": 20_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 6, "label": "20,001-30,000", "min_shares": 20_001, "max_shares": 30_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 7, "label": "30,001-40,000", "min_shares": 30_001, "max_shares": 40_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 8, "label": "40,001-50,000", "min_shares": 40_001, "max_shares": 50_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 9, "label": "50,001-100,000", "min_shares": 50_001, "max_shares": 100_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 10, "label": "100,001-200,000", "min_shares": 100_001, "max_shares": 200_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 11, "label": "200,001-400,000", "min_shares": 200_001, "max_shares": 400_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 12, "label": "400,001-600,000", "min_shares": 400_001, "max_shares": 600_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 13, "label": "600,001-800,000", "min_shares": 600_001, "max_shares": 800_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 14, "label": "800,001-1,000,000", "min_shares": 800_001, "max_shares": 1_000_000, "is_adjustment": False, "is_total": False},
    {"bucket_id": 15, "label": "1,000,001以上", "min_shares": 1_000_001, "max_shares": None, "is_adjustment": False, "is_total": False},
    {"bucket_id": 16, "label": "差異數調整（說明4）", "min_shares": None, "max_shares": None, "is_adjustment": True, "is_total": False},
    {"bucket_id": 17, "label": "合 計", "min_shares": None, "max_shares": None, "is_adjustment": False, "is_total": True},
)

TDCC_HOLDER_GROUPS = {
    "small_holders": {
        "label": "持股 < 10 張",
        "direction": "decrease",
        "selector_mode": "upper_bound",
        "min_shares": 1,
        "max_shares": 10_000,
        "note": "受 TDCC 官方分級粒度限制，<10 張會整桶納入 5,001-10,000 股。",
    },
    "mid_holders": {
        "label": "持股 400~800 張",
        "direction": "increase",
        "selector_mode": "inside_range",
        "min_shares": 400_000,
        "max_shares": 800_000,
        "note": "依官方分級以完整落在區間內的桶計算，會排除剛好 400,000 股的邊界。",
    },
    "large_holders": {
        "label": "持股 > 1000 張",
        "direction": "increase",
        "selector_mode": "lower_bound",
        "min_shares": 1_000_001,
        "max_shares": None,
        "note": "依官方分級採 1,000,001 股以上的最大戶桶。",
    },
}

TDCC_OPENAPI_FIELD_ALIASES = {
    "data_date": ["資料日期", "\ufeff資料日期"],
    "stock_code": ["證券代號", "股票代號"],
    "stock_name": ["證券名稱", "股票名稱", "公司名稱"],
    "bucket_id": ["持股分級"],
    "holder_count": ["人數"],
    "share_count": ["股數", "股數/單位數"],
    "ratio": ["占集保庫存數比例%", "占集保庫存數比例 (%)"],
}

TWSE_COMPANY_FIELD_ALIASES = {
    "stock_code": ["公司代號"],
    "stock_name": ["公司名稱"],
    "short_name": ["公司簡稱"],
    "industry": ["產業別"],
    "listed_date": ["上市日期"],
}

TPEX_COMPANY_FIELD_ALIASES = {
    "stock_code": ["SecuritiesCompanyCode"],
    "stock_name": ["CompanyName"],
    "short_name": ["CompanyAbbreviation"],
    "industry": ["SecuritiesIndustryCode"],
    "listed_date": ["DateOfListing"],
}

# ── 選股評分門檻（0-100，scoring.py 的唯一真實來源） ──────────────────────
# 每組為 (門檻, 分數) 由「最佳」往「最差」排列；scoring 取第一個符合者。
# 四個維度滿分相加 = 30 + 25 + 25 + 20 = 100。
#
# 設計理念（融合 stock_chip_selector 的評分精神，改吃線上可得訊號）：
#   大戶人數增幅  ← 取代「連續買超強度／主力吸籌」
#   集保總戶數降幅 ← 對應原「集保戶數下降」
#   距 20MA       ← 取代原「現價接近主力成本」
#   近三月漲幅溫和 ← 價格未過熱、仍有上行空間

# 大戶（>1000 張）人數自最早觀察週到最新週的增幅（ratio，越大越好）→ 分數
SCORE_LARGE_HOLDER_GROWTH = ((0.10, 30), (0.05, 24), (0.02, 18), (0.0, 12))
# 集保總戶數降幅（取下降的絕對 ratio，越大越好）→ 分數
SCORE_HOLDER_DECLINE = ((0.05, 25), (0.03, 20), (0.01, 15), (0.0, 10))
# 距 20MA 的距離（ratio，越小越好；門檻為「不超過」）→ 分數
SCORE_MA_PROXIMITY = ((0.02, 25), (0.04, 20), (0.06, 15), (0.08, 10))
# 近三月漲幅（ratio，越溫和越好；門檻為「不超過」）→ 分數
SCORE_RETURN_MILDNESS = ((0.10, 20), (0.20, 15), (0.30, 10), (0.40, 5))

# 綜合分數的評級標籤門檻（分數 >= 門檻 即套用該標籤，由高往低）
SCORE_LABEL_THRESHOLDS = ((85, "⭐⭐⭐ 強力推薦"), (70, "⭐⭐ 值得關注"), (55, "⭐ 符合條件"), (0, "基本符合"))