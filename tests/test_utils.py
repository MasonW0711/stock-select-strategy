from datetime import date

from utils import (
    coerce_float,
    coerce_int,
    normalize_stock_code,
    normalize_text,
    parse_compact_date,
    parse_roc_date,
    parse_tdcc_or_twse_date,
    pick_first_present,
    strip_bom,
)


# --- coerce_int ---

def test_coerce_int_handles_common_inputs():
    assert coerce_int(None) is None
    assert coerce_int(True) == 1
    assert coerce_int(5) == 5
    assert coerce_int(5.9) == 5  # 浮點截斷
    assert coerce_int("1,234") == 1234  # 千分位
    assert coerce_int(" 42 ") == 42
    assert coerce_int("3.0") == 3
    assert coerce_int("-5") == -5


def test_coerce_int_treats_placeholders_as_none():
    for placeholder in ("", "--", "-", "N/A", "nan", "None"):
        assert coerce_int(placeholder) is None


def test_coerce_int_rejects_non_finite_float():
    assert coerce_int(float("nan")) is None
    assert coerce_int(float("inf")) is None


# --- coerce_float ---

def test_coerce_float_handles_common_inputs():
    assert coerce_float(None) is None
    assert coerce_float("3.21%") == 3.21  # 去掉百分號
    assert coerce_float("1,234.5") == 1234.5
    assert coerce_float("-0.5") == -0.5
    assert coerce_float(2) == 2.0


def test_coerce_float_strips_x_and_placeholders():
    assert coerce_float("X") is None
    assert coerce_float("--") is None
    assert coerce_float(float("inf")) is None


# --- 日期解析 ---

def test_parse_compact_date_western_and_roc():
    assert parse_compact_date("20260413") == date(2026, 4, 13)
    assert parse_compact_date("1150413") == date(2026, 4, 13)  # 民國 115 → 2026


def test_parse_compact_date_invalid():
    assert parse_compact_date("20261332") is None  # 月日不合法
    assert parse_compact_date("abc") is None
    assert parse_compact_date("") is None
    assert parse_compact_date("202604") is None  # 長度不符


def test_parse_roc_date():
    assert parse_roc_date("115/03/03") == date(2026, 3, 3)
    assert parse_roc_date("115/3/3") == date(2026, 3, 3)
    assert parse_roc_date("115/13/03") is None  # 月份不合法
    assert parse_roc_date("2026/03/03") is None  # 西元四碼不吃


def test_parse_tdcc_or_twse_date_tries_both():
    assert parse_tdcc_or_twse_date("20260413") == date(2026, 4, 13)
    assert parse_tdcc_or_twse_date("115/03/03") == date(2026, 3, 3)
    assert parse_tdcc_or_twse_date("不是日期") is None


# --- 文字/代號清洗 ---

def test_strip_bom_and_normalize_text():
    assert strip_bom("﻿資料日期") == "資料日期"
    assert normalize_text(None) == ""
    assert normalize_text("﻿ 台積電 ") == "台積電"
    assert normalize_text("a\xa0b") == "a b"  # 不換行空白轉一般空白


def test_normalize_stock_code():
    assert normalize_stock_code(" 2330 ") == "2330"
    assert normalize_stock_code("00 50") == "0050"
    assert normalize_stock_code("2330.TW") == "2330TW"
    assert normalize_stock_code("ky01") == "KY01"


def test_pick_first_present_respects_alias_order():
    mapping = {"證券代號": "2330", "股票代號": "ignored"}
    assert pick_first_present(mapping, ["證券代號", "股票代號"]) == "2330"
    assert pick_first_present(mapping, ["股票代號", "證券代號"]) == "ignored"
    assert pick_first_present(mapping, ["不存在"]) is None
