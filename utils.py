from __future__ import annotations

import logging
import re
from datetime import date
from math import isfinite
from typing import Any, Iterable


def setup_logging(level: str = "INFO") -> logging.Logger:
    """建立全域 logging 設定並回傳專案 logger。

    basicConfig 只有第一次（root 尚無 handler 時）會掛上 handler，但每次呼叫都會
    重設 level，使 Streamlit rerun 或 CLI 改 log 等級時能即時生效（避免只有第一次有效）。
    """

    resolved_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=resolved_level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    logging.getLogger().setLevel(resolved_level)
    logger = logging.getLogger("stock_screener")
    logger.setLevel(resolved_level)
    return logger


def strip_bom(value: str) -> str:
    """移除 UTF-8 BOM 與前後空白。"""

    return value.replace("\ufeff", "").strip()


def normalize_text(value: Any) -> str:
    """將任何輸入安全轉成乾淨字串。"""

    if value is None:
        return ""
    return strip_bom(str(value).replace("\xa0", " "))


def sanitize_mapping_keys(mapping: dict[str, Any]) -> dict[str, Any]:
    """將 dict key 做 BOM 與空白清洗。"""

    return {normalize_text(key): value for key, value in mapping.items()}


def pick_first_present(mapping: dict[str, Any], aliases: Iterable[str]) -> Any:
    """依欄位別名順序取回第一個存在的值。"""

    for alias in aliases:
        if alias in mapping:
            return mapping[alias]
    return None


def normalize_stock_code(value: Any) -> str:
    """將股票代號清洗成不含空白的英數字字串。"""

    text = normalize_text(value)
    return re.sub(r"[^0-9A-Za-z]", "", text).upper()


def coerce_int(value: Any) -> int | None:
    """將常見數字字串安全轉成 int。"""

    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if isfinite(value) else None
    text = normalize_text(value)
    if text in {"", "--", "-", "N/A", "nan", "None"}:
        return None
    cleaned = re.sub(r"[^0-9\-.]", "", text)
    if cleaned in {"", "-", ".", "-."}:
        return None
    return int(float(cleaned))


def coerce_float(value: Any) -> float | None:
    """將常見數字字串安全轉成 float。"""

    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if isfinite(float(value)) else None
    text = normalize_text(value)
    if text in {"", "--", "-", "N/A", "nan", "None"}:
        return None
    cleaned = re.sub(r"[^0-9\-.]", "", text.replace("X", ""))
    if cleaned in {"", "-", ".", "-."}:
        return None
    return float(cleaned)


def parse_compact_date(value: Any) -> date | None:
    """解析 YYYYMMDD 或民國 yyyMMdd 日期格式。"""

    text = normalize_text(value)
    if not text.isdigit():
        return None
    if len(text) == 8:
        year = int(text[:4])
        month = int(text[4:6])
        day = int(text[6:8])
    elif len(text) == 7:
        year = int(text[:3]) + 1911
        month = int(text[3:5])
        day = int(text[5:7])
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_roc_date(value: Any) -> date | None:
    """解析民國日期格式，例如 115/03/03。"""

    text = normalize_text(value)
    matched = re.fullmatch(r"(\d{2,3})/(\d{1,2})/(\d{1,2})", text)
    if matched is None:
        return None
    year = int(matched.group(1)) + 1911
    month = int(matched.group(2))
    day = int(matched.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_tdcc_or_twse_date(value: Any) -> date | None:
    """依序嘗試解析 TDCC 與 TWSE 常見日期格式。"""

    return parse_compact_date(value) or parse_roc_date(value)


def format_date_compact(value: date) -> str:
    """將 date 轉成 YYYYMMDD 字串。"""

    return value.strftime("%Y%m%d")


def iter_recent_month_starts(anchor_date: date, months: int) -> list[date]:
    """由近到遠回傳最近幾個月份的月初日期。"""

    month_starts: list[date] = []
    year = anchor_date.year
    month = anchor_date.month
    for _ in range(months):
        month_starts.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return month_starts


def format_ratio_as_pct(value: float | None) -> str:
    """將小數比率轉成百分比字串。"""

    if value is None:
        return ""
    return f"{value * 100:.2f}%"


def format_trend_values(values: Iterable[int | None]) -> str:
    """將趨勢數列格式化成適合終端機顯示的文字。"""

    return " -> ".join("-" if value is None else f"{value:,}" for value in values)