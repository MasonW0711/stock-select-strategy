from datetime import date

import pytest

from api_clients import (
    ApiClientError,
    DataNotFoundError,
    TDCCOpenApiClient,
    TDCCPortalHistoryClient,
    TPEXApiClient,
    TWSEApiClient,
)
from config import DEFAULT_HTTP_SETTINGS, CacheSettings, make_cache_settings

DISABLED_CACHE = make_cache_settings(enabled=False)


# --- TDCC OpenAPI 最新快照解析 ---

class _StubLatestClient(TDCCOpenApiClient):
    def __init__(self, payload):
        super().__init__(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=DISABLED_CACHE, logger=None)
        self._payload = payload

    def get_json(self, url, **kwargs):
        return self._payload


def test_tdcc_latest_keeps_only_latest_date_and_reads_total():
    payload = [
        {"資料日期": "20260411", "證券代號": "2330", "證券名稱": "台積電", "持股分級": 1, "人數": "100", "股數": "1000", "占集保庫存數比例%": "0.5"},
        {"資料日期": "20260411", "證券代號": "2330", "持股分級": 15, "人數": "5", "股數": "9000000", "占集保庫存數比例%": "40"},
        {"資料日期": "20260411", "證券代號": "2330", "持股分級": 17, "人數": "12345", "股數": "9100000", "占集保庫存數比例%": "100"},
        {"資料日期": "20260404", "證券代號": "2330", "持股分級": 1, "人數": "999", "股數": "1000", "占集保庫存數比例%": "0.5"},
    ]
    client = _StubLatestClient(payload)
    snapshots, latest = client.fetch_latest_market_snapshots()

    assert latest == date(2026, 4, 11)
    assert set(snapshots) == {"2330"}
    snap = snapshots["2330"]
    assert snap.data_date == date(2026, 4, 11)
    assert [b.bucket_id for b in snap.buckets] == [1, 15, 17]  # 已依 bucket_id 排序、舊日期被濾掉
    assert snap.total_holder_count() == 12345
    assert abs(snap.buckets[0].ratio - 0.005) < 1e-9  # 比例已 / 100


def test_tdcc_latest_raises_on_non_list_payload():
    client = _StubLatestClient({"unexpected": "shape"})
    with pytest.raises(ApiClientError):
        client.fetch_latest_market_snapshots()


# --- TDCC 歷史查詢頁 HTML 解析 ---

def _history_client():
    return TDCCPortalHistoryClient(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=DISABLED_CACHE, logger=None)


_VALID_HISTORY_HTML = """
<html><body><table>
  <tr><th>序</th><th>持股/單位數分級</th><th>人數</th><th>股數/單位數</th><th>占集保庫存數比例 (%)</th></tr>
  <tr><td>1</td><td>1-999</td><td>100</td><td>1,000</td><td>0.50</td></tr>
  <tr><td>15</td><td>1,000,001以上</td><td>5</td><td>9,000,000</td><td>40.00</td></tr>
  <tr><td>17</td><td>合 計</td><td>12,345</td><td>9,100,000</td><td>100.00</td></tr>
</table></body></html>
"""


def test_history_html_parse_reads_buckets_and_total():
    client = _history_client()
    snap = client._parse_snapshot_html(html=_VALID_HISTORY_HTML, stock_code="2330", query_date=date(2026, 4, 4))
    assert snap.data_date == date(2026, 4, 4)
    assert [b.bucket_id for b in snap.buckets] == [1, 15, 17]
    assert snap.total_holder_count() == 12345
    total_bucket = snap.bucket_map()[17]
    assert total_bucket.is_total is True


def test_history_html_no_data_raises_data_not_found():
    client = _history_client()
    html = "<html><body><p>查無資料</p></body></html>"
    with pytest.raises(DataNotFoundError):
        client._parse_snapshot_html(html=html, stock_code="2330", query_date=date(2026, 4, 4))


def test_history_html_missing_table_raises_api_error():
    client = _history_client()
    html = "<html><body><table><tr><th>無關表頭</th></tr></table></body></html>"
    with pytest.raises(ApiClientError):
        client._parse_snapshot_html(html=html, stock_code="2330", query_date=date(2026, 4, 4))


# --- TDCC 歷史「查無資料」負快取 ---

def test_history_negative_cache_round_trips_on_disk(tmp_path):
    settings = make_cache_settings(enabled=True, cache_dir=str(tmp_path))
    client = TDCCPortalHistoryClient(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=settings, logger=None)
    key = "2330_20260404"
    assert client._load_not_found("2330", date(2026, 4, 4), key) is False
    client._save_not_found("2330", date(2026, 4, 4), key)
    assert client._load_not_found("2330", date(2026, 4, 4), key) is True

    # 另一個（全新）instance 仍能從磁碟讀到負快取
    fresh = TDCCPortalHistoryClient(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=settings, logger=None)
    assert fresh._load_not_found("2330", date(2026, 4, 4), key) is True


def test_fetch_snapshot_short_circuits_on_negative_cache_without_network():
    client = _history_client()  # cache 關閉，且未設定任何網路 stub
    client._not_found_cache.add(("2330", date(2026, 4, 4)))
    with pytest.raises(DataNotFoundError):
        client.fetch_snapshot(stock_code="2330", query_date=date(2026, 4, 4))


# --- TWSE / TPEX 個股日線 JSON 解析 ---

class _StubTWSE(TWSEApiClient):
    def __init__(self, payload):
        super().__init__(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=DISABLED_CACHE, logger=None)
        self._payload = payload

    def get_json(self, url, **kwargs):
        return self._payload


def test_twse_stock_day_parses_close_prices():
    payload = {
        "stat": "OK",
        "fields": ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌價差", "成交筆數"],
        "data": [
            ["115/04/01", "1,000", "100,000", "50.0", "51.0", "49.0", "50.5", "+0.5", "100"],
            ["115/04/02", "2,000", "200,000", "50.5", "52.0", "50.0", "51.5", "+1.0", "200"],
        ],
    }
    bars = _StubTWSE(payload).fetch_stock_day_history("2330", months=1)
    assert [b.trade_date for b in bars] == [date(2026, 4, 1), date(2026, 4, 2)]
    assert [b.close_price for b in bars] == [50.5, 51.5]
    assert bars[0].volume == 1000


def test_twse_stock_day_skips_non_ok_payload():
    payload = {"stat": "查詢日期大於可查詢最大日期", "fields": [], "data": []}
    assert _StubTWSE(payload).fetch_stock_day_history("2330", months=1) == []


class _StubTPEX(TPEXApiClient):
    def __init__(self, payload):
        super().__init__(http_settings=DEFAULT_HTTP_SETTINGS, cache_settings=DISABLED_CACHE, logger=None)
        self._payload = payload

    def get_json(self, url, **kwargs):
        return self._payload


def test_tpex_stock_day_parses_close_prices():
    payload = {
        "stat": "ok",
        "tables": [
            {
                "data": [
                    ["115/04/01", "1,000", "100,000", "50.0", "51.0", "49.0", "50.5", "0", "0"],
                    ["115/04/02", "2,000", "200,000", "50.5", "52.0", "50.0", "51.5", "0", "0"],
                ]
            }
        ],
    }
    bars = _StubTPEX(payload).fetch_stock_day_history("6488", months=1)
    assert [b.trade_date for b in bars] == [date(2026, 4, 1), date(2026, 4, 2)]
    assert [b.close_price for b in bars] == [50.5, 51.5]
