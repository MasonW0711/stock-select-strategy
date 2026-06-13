from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3
from bs4 import BeautifulSoup

from config import (
    CacheSettings,
    DEFAULT_HEADERS,
    EXCLUDED_SHORT_NAME_KEYWORDS,
    HttpSettings,
    TDCC_BUCKET_DEFINITIONS,
    TDCC_LATEST_SHAREHOLDING_URL,
    TDCC_OPENAPI_FIELD_ALIASES,
    TDCC_PORTAL_URL,
    TPEX_COMPANY_FIELD_ALIASES,
    TPEX_OTC_COMPANIES_URL,
    TPEX_OTC_STOCK_DAY_URL,
    TWSE_COMPANY_FIELD_ALIASES,
    TWSE_LISTED_COMPANIES_URL,
    TWSE_STOCK_DAY_URL,
    VALID_LISTED_STOCK_CODE_LENGTH,
)
from models import PriceBar, ShareholdingBucketRecord, ShareholdingSnapshot, StockInfo
from utils import (
    coerce_float,
    coerce_int,
    format_date_compact,
    iter_recent_month_starts,
    normalize_stock_code,
    normalize_text,
    parse_tdcc_or_twse_date,
    pick_first_present,
    sanitize_mapping_keys,
)


class ApiClientError(RuntimeError):
    """代表資料來源請求或解析失敗。"""


class DataNotFoundError(ApiClientError):
    """代表資料來源回應成功，但沒有找到目標資料。"""


class AccessBlockedError(ApiClientError):
    """官方站台以 WAF／安全頁（HTTP 200 但內容為封鎖訊息）拒絕請求。

    常見於 Streamlit Cloud 等雲端/資料中心 IP 被 TWSE/TPEX/TDCC 阻擋；非程式錯誤，
    且重試同一 IP 無效，故獨立成型別讓上層能給出明確、可行動的提示。
    """


# 官方安全頁的特徵字串（英文標記為 ASCII，編碼異常時仍可比對）。
ACCESS_BLOCK_MARKERS = ("FOR SECURITY REASONS", "THIS PAGE CAN NOT BE ACCESSED", "因為安全性考量")


class BaseHttpClient:
    """提供 requests.Session、retry 與簡單 rate limit 的基底 client。"""

    def __init__(
        self,
        http_settings: HttpSettings,
        cache_settings: CacheSettings,
        logger: logging.Logger | None = None,
    ) -> None:
        """初始化共用 session 與連線設定。"""

        self.http_settings = http_settings
        self.cache_settings = cache_settings
        self.logger = logger or logging.getLogger("stock_screener")
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        # 跨執行緒節流：以「下一個可送出時刻」配額排程，threads 之間互相錯開最低請求間隔，
        # 但 sleep 不在鎖內進行，因此併發抓價時網路等待可重疊（吞吐受 min_interval 上限保護）。
        self._rate_lock = threading.Lock()
        self._next_allowed_at = 0.0
        self._cache_root = Path(self.cache_settings.cache_dir).expanduser().resolve()
        self._ssl_fallback_warned_hosts: set[str] = set()
        self._ssl_fallback_hosts = {
            "openapi.twse.com.tw",
            "www.twse.com.tw",
            "openapi.tdcc.com.tw",
            "www.tdcc.com.tw",
            "www.tpex.org.tw",
        }

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """執行 HTTP 請求並套用 retry/backoff。"""

        for attempt in range(self.http_settings.max_retries + 1):
            self._respect_rate_limit()
            try:
                response = self.session.request(method=method, url=url, timeout=self.http_settings.timeout_seconds, **kwargs)
            except requests.exceptions.SSLError as exc:
                response = self._request_with_ssl_fallback(method=method, url=url, original_kwargs=kwargs, original_error=exc)
            except requests.RequestException as exc:
                if attempt >= self.http_settings.max_retries:
                    raise ApiClientError(f"請求失敗：{url}") from exc
                self._sleep_before_retry(attempt, f"網路錯誤，準備重試：{url}")
                continue

            if response.status_code == 404:
                raise DataNotFoundError(f"找不到資料：{url}")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self.http_settings.max_retries:
                    raise ApiClientError(f"伺服器回應異常：{url} ({response.status_code})")
                self._sleep_before_retry(attempt, f"伺服器忙碌，準備重試：{url} ({response.status_code})")
                continue
            if response.status_code >= 400:
                raise ApiClientError(f"請求失敗：{url} ({response.status_code})")

            response.encoding = response.apparent_encoding or response.encoding
            self._raise_if_access_blocked(url, response)
            return response

        raise ApiClientError(f"請求失敗：{url}")

    def _raise_if_access_blocked(self, url: str, response: requests.Response) -> None:
        """偵測官方 WAF／安全頁（200 但內容為封鎖訊息）；命中即丟 AccessBlockedError，不重試。"""

        content_type = normalize_text(response.headers.get("Content-Type")).lower()
        if "html" not in content_type:
            return
        body_preview = response.text[:1000]
        if any(marker in body_preview for marker in ACCESS_BLOCK_MARKERS):
            self.logger.error("資料來源以安全頁拒絕此主機請求（疑似雲端/資料中心 IP 被阻擋）：%s", url)
            raise AccessBlockedError(
                f"官方資料站台以安全頁拒絕此主機的請求（很可能是雲端/資料中心 IP 被阻擋）：{url}"
            )

    def _request_with_ssl_fallback(
        self,
        method: str,
        url: str,
        original_kwargs: dict[str, Any],
        original_error: requests.exceptions.SSLError,
    ) -> requests.Response:
        """在已知官方資料主機遇到憑證驗證問題時改用 verify=False 重試。"""

        host = urlparse(url).hostname or ""
        if host not in self._ssl_fallback_hosts:
            raise original_error
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        if host not in self._ssl_fallback_warned_hosts:
            self.logger.warning("偵測到官方資料站台 SSL 驗證問題，改用 verify=False 重試：%s", url)
            self._ssl_fallback_warned_hosts.add(host)
        return self.session.request(
            method=method,
            url=url,
            timeout=self.http_settings.timeout_seconds,
            verify=False,
            **original_kwargs,
        )

    def get_json(self, url: str, **kwargs: Any) -> Any:
        """執行 GET 並解析 JSON。"""

        cache_namespace = kwargs.pop("cache_namespace", None)
        cache_key = kwargs.pop("cache_key", None)
        cache_max_age_seconds = kwargs.pop("cache_max_age_seconds", None)
        resolved_cache_key = cache_key or self._build_cache_key(url=url, kwargs=kwargs)
        if cache_namespace:
            cached_text = self._load_cache_text(
                namespace=cache_namespace,
                cache_key=resolved_cache_key,
                max_age_seconds=cache_max_age_seconds,
            )
            if cached_text is not None:
                try:
                    return json.loads(cached_text)
                except ValueError:
                    pass

        response = self.request("GET", url, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            content_type = normalize_text(response.headers.get("Content-Type")) or "unknown"
            preview = normalize_text(response.text).replace("\n", " ")[:180] or "<empty>"
            self.logger.warning("JSON 解析失敗：%s content-type=%s preview=%s", url, content_type, preview)
            raise ApiClientError(f"JSON 解析失敗：{url} (content-type={content_type})") from exc

        if cache_namespace:
            self._save_cache_text(
                namespace=cache_namespace,
                cache_key=resolved_cache_key,
                text=response.text,
            )
        return payload

    def get_text(self, url: str, **kwargs: Any) -> str:
        """執行 GET 並回傳文字內容。"""

        cache_namespace = kwargs.pop("cache_namespace", None)
        cache_key = kwargs.pop("cache_key", None)
        cache_max_age_seconds = kwargs.pop("cache_max_age_seconds", None)
        if cache_namespace:
            cached_text = self._load_cache_text(
                namespace=cache_namespace,
                cache_key=cache_key or self._build_cache_key(url=url, kwargs=kwargs),
                max_age_seconds=cache_max_age_seconds,
            )
            if cached_text is not None:
                return cached_text

        response = self.request("GET", url, **kwargs)
        if cache_namespace:
            self._save_cache_text(
                namespace=cache_namespace,
                cache_key=cache_key or self._build_cache_key(url=url, kwargs=kwargs),
                text=response.text,
            )
        return response.text

    def post_text(self, url: str, **kwargs: Any) -> str:
        """執行 POST 並回傳文字內容。"""

        cache_namespace = kwargs.pop("cache_namespace", None)
        cache_key = kwargs.pop("cache_key", None)
        cache_max_age_seconds = kwargs.pop("cache_max_age_seconds", None)
        if cache_namespace:
            cached_text = self._load_cache_text(
                namespace=cache_namespace,
                cache_key=cache_key or self._build_cache_key(url=url, kwargs=kwargs),
                max_age_seconds=cache_max_age_seconds,
            )
            if cached_text is not None:
                return cached_text

        response = self.request("POST", url, **kwargs)
        if cache_namespace:
            self._save_cache_text(
                namespace=cache_namespace,
                cache_key=cache_key or self._build_cache_key(url=url, kwargs=kwargs),
                text=response.text,
            )
        return response.text

    def _build_cache_key(self, url: str, kwargs: dict[str, Any]) -> str:
        """依請求內容產生穩定 cache key。"""

        normalized_kwargs = {key: kwargs[key] for key in sorted(kwargs)}
        return json.dumps({"url": url, "kwargs": normalized_kwargs}, ensure_ascii=False, sort_keys=True, default=str)

    def _cache_file_path(self, namespace: str, cache_key: str) -> Path:
        """計算單一 cache entry 的檔案路徑。"""

        digest = hashlib.sha256(cache_key.encode("utf-8")).hexdigest()
        return self._cache_root / namespace / f"{digest}.json"

    def _load_cache_text(self, namespace: str, cache_key: str, max_age_seconds: int | None) -> str | None:
        """讀取仍在有效期限內的快取文字。"""

        if not self.cache_settings.enabled:
            return None
        cache_path = self._cache_file_path(namespace=namespace, cache_key=cache_key)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

        cached_at = payload.get("cached_at")
        if max_age_seconds is not None and isinstance(cached_at, (int, float)):
            if time.time() - float(cached_at) > max_age_seconds:
                return None
        return payload.get("text")

    def _save_cache_text(self, namespace: str, cache_key: str, text: str) -> None:
        """將文字內容寫入 cache。"""

        if not self.cache_settings.enabled:
            return
        cache_path = self._cache_file_path(namespace=namespace, cache_key=cache_key)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            cache_path.write_text(
                json.dumps({"cached_at": time.time(), "text": text}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            self.logger.debug("寫入快取失敗：%s", cache_path)

    def _respect_rate_limit(self) -> None:
        """確保（跨執行緒）兩次請求之間至少留出最小間隔。

        在鎖內僅計算等待時間並預約下一個時槽，sleep 留到鎖外執行，使併發抓價時各 thread
        能錯開送出、重疊網路等待，同時把單一 client 的吞吐限制在 1/min_interval 以內。
        """

        with self._rate_lock:
            now = time.monotonic()
            wait_seconds = self._next_allowed_at - now
            slot_start = now if wait_seconds <= 0 else self._next_allowed_at
            self._next_allowed_at = slot_start + self.http_settings.min_interval_seconds
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def _sleep_before_retry(self, attempt: int, message: str) -> None:
        """依 retry 次數進行指數退避。"""

        delay = self.http_settings.backoff_seconds * (2**attempt)
        self.logger.warning("%s，%.1f 秒後重試", message, delay)
        time.sleep(delay)


class TDCCOpenApiClient(BaseHttpClient):
    """負責抓取 TDCC 最新一週全市場股權分散快照。"""

    def fetch_latest_market_snapshots(self) -> tuple[dict[str, ShareholdingSnapshot], date | None]:
        """抓取並整理 TDCC 最新全市場快照。"""

        payload = self.get_json(
            TDCC_LATEST_SHAREHOLDING_URL,
            cache_namespace="tdcc_openapi",
            cache_key="latest_market_snapshots",
            cache_max_age_seconds=self.cache_settings.latest_snapshot_ttl_seconds,
        )
        if not isinstance(payload, list):
            raise ApiClientError("TDCC 最新快照格式異常")

        grouped_buckets: dict[tuple[str, date], list[ShareholdingBucketRecord]] = defaultdict(list)
        grouped_names: dict[str, str] = {}
        latest_date: date | None = None
        bucket_lookup = {item["bucket_id"]: item for item in TDCC_BUCKET_DEFINITIONS}

        for raw_row in payload:
            if not isinstance(raw_row, dict):
                continue
            row = sanitize_mapping_keys(raw_row)
            data_date = parse_tdcc_or_twse_date(pick_first_present(row, TDCC_OPENAPI_FIELD_ALIASES["data_date"]))
            stock_code = normalize_stock_code(pick_first_present(row, TDCC_OPENAPI_FIELD_ALIASES["stock_code"]))
            stock_name = normalize_text(pick_first_present(row, TDCC_OPENAPI_FIELD_ALIASES["stock_name"]))
            bucket_id = coerce_int(pick_first_present(row, TDCC_OPENAPI_FIELD_ALIASES["bucket_id"]))
            if data_date is None or not stock_code or bucket_id is None:
                continue

            definition = bucket_lookup.get(bucket_id, {})
            grouped_buckets[(stock_code, data_date)].append(
                ShareholdingBucketRecord(
                    bucket_id=bucket_id,
                    label=str(definition.get("label", bucket_id)),
                    holder_count=coerce_int(pick_first_present(row, TDCC_OPENAPI_FIELD_ALIASES["holder_count"])),
                    share_count=coerce_int(pick_first_present(row, TDCC_OPENAPI_FIELD_ALIASES["share_count"])),
                    ratio=(coerce_float(pick_first_present(row, TDCC_OPENAPI_FIELD_ALIASES["ratio"])) or 0.0) / 100,
                    is_adjustment=bool(definition.get("is_adjustment", False)),
                    is_total=bool(definition.get("is_total", False)),
                )
            )
            if stock_name:
                grouped_names.setdefault(stock_code, stock_name)
            if latest_date is None or data_date > latest_date:
                latest_date = data_date

        snapshots: dict[str, ShareholdingSnapshot] = {}
        if latest_date is None:
            return snapshots, None

        for (stock_code, data_date), buckets in grouped_buckets.items():
            if data_date != latest_date:
                continue
            buckets.sort(key=lambda bucket: bucket.bucket_id)
            snapshots[stock_code] = ShareholdingSnapshot(
                stock_code=stock_code,
                stock_name=grouped_names.get(stock_code, ""),
                data_date=data_date,
                buckets=buckets,
                source="tdcc_openapi_latest",
            )

        return snapshots, latest_date


class TDCCPortalHistoryClient(BaseHttpClient):
    """負責透過 TDCC 官網查詢頁補抓歷史週資料。"""

    def __init__(
        self,
        http_settings: HttpSettings,
        cache_settings: CacheSettings,
        logger: logging.Logger | None = None,
    ) -> None:
        """初始化查詢頁快取狀態。"""

        super().__init__(http_settings=http_settings, cache_settings=cache_settings, logger=logger)
        self._form_state: dict[str, str] | None = None
        self._available_dates: list[date] | None = None
        self._snapshot_cache: dict[tuple[str, date], ShareholdingSnapshot] = {}
        # 負快取：記住本次執行已確認「查無資料」的 (股票, 日期)，避免同一輪重複查詢。
        self._not_found_cache: set[tuple[str, date]] = set()

    _NOT_FOUND_NAMESPACE = "tdcc_history_notfound"
    _NOT_FOUND_MARKER = "__TDCC_NOT_FOUND__"

    def _load_not_found(self, stock_code: str, query_date: date, disk_cache_key: str) -> bool:
        """檢查記憶體與磁碟負快取是否已記錄此股票該週查無資料。"""

        if (stock_code, query_date) in self._not_found_cache:
            return True
        cached = self._load_cache_text(
            namespace=self._NOT_FOUND_NAMESPACE,
            cache_key=disk_cache_key,
            max_age_seconds=self.cache_settings.tdcc_not_found_ttl_seconds,
        )
        if cached == self._NOT_FOUND_MARKER:
            self._not_found_cache.add((stock_code, query_date))
            return True
        return False

    def _save_not_found(self, stock_code: str, query_date: date, disk_cache_key: str) -> None:
        """將「查無資料」寫入記憶體與磁碟負快取。"""

        self._not_found_cache.add((stock_code, query_date))
        self._save_cache_text(
            namespace=self._NOT_FOUND_NAMESPACE,
            cache_key=disk_cache_key,
            text=self._NOT_FOUND_MARKER,
        )

    def refresh_form_state(self) -> None:
        """重新抓取查詢頁 hidden token 與可選日期。"""

        html = self.get_text(TDCC_PORTAL_URL)
        self._update_form_state_from_html(html)

    def _update_form_state_from_html(self, html: str) -> None:
        """從查詢頁或結果頁 HTML 更新 hidden token 與可用日期。"""

        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")
        if form is None:
            raise ApiClientError("TDCC 查詢頁格式異常，找不到 form")

        form_state: dict[str, str] = {}
        for field_name in ("SYNCHRONIZER_TOKEN", "SYNCHRONIZER_URI", "method", "firDate"):
            field = form.find("input", {"name": field_name})
            form_state[field_name] = normalize_text(field.get("value") if field else "")
        if not form_state["method"]:
            form_state["method"] = "submit"

        available_dates: list[date] = []
        date_select = form.find("select", {"name": "scaDate"})
        if date_select is not None:
            for option in date_select.find_all("option"):
                parsed = parse_tdcc_or_twse_date(option.get("value"))
                if parsed is not None:
                    available_dates.append(parsed)

        previous_count = len(self._available_dates or [])
        self._form_state = form_state
        self._available_dates = sorted(set(available_dates), reverse=True)
        current_count = len(self._available_dates)
        if previous_count == 0 or previous_count != current_count:
            self.logger.info("TDCC 查詢頁可用日期數量：%s", current_count)

    def get_available_dates(self) -> list[date]:
        """回傳 TDCC 查詢頁提供的歷史日期列表。"""

        if self._available_dates is None:
            self.refresh_form_state()
        return list(self._available_dates or [])

    def fetch_snapshot(self, stock_code: str, query_date: date) -> ShareholdingSnapshot:
        """抓取單一股票在指定日期的 TDCC 歷史快照。"""

        cache_key = (stock_code, query_date)
        if cache_key in self._snapshot_cache:
            return self._snapshot_cache[cache_key]

        disk_cache_key = f"{stock_code}_{query_date:%Y%m%d}"
        cached_html = self._load_cache_text(
            namespace="tdcc_history",
            cache_key=disk_cache_key,
            max_age_seconds=self.cache_settings.historical_snapshot_ttl_seconds,
        )
        if cached_html is not None:
            snapshot = self._parse_snapshot_html(html=cached_html, stock_code=stock_code, query_date=query_date)
            self._snapshot_cache[cache_key] = snapshot
            return snapshot

        if self._load_not_found(stock_code=stock_code, query_date=query_date, disk_cache_key=disk_cache_key):
            raise DataNotFoundError(f"TDCC 查無資料（負快取）：{stock_code} {query_date.isoformat()}")

        available_dates = self.get_available_dates()
        if query_date not in available_dates:
            raise DataNotFoundError(f"TDCC 查詢頁沒有提供日期：{query_date.isoformat()}")

        for attempt in range(2):
            if self._form_state is None:
                self.refresh_form_state()
            assert self._form_state is not None

            payload = {
                "SYNCHRONIZER_TOKEN": self._form_state.get("SYNCHRONIZER_TOKEN", ""),
                "SYNCHRONIZER_URI": self._form_state.get("SYNCHRONIZER_URI", "/portal/zh/smWeb/qryStock"),
                "method": self._form_state.get("method", "submit"),
                "firDate": self._form_state.get("firDate", format_date_compact(query_date)),
                "scaDate": format_date_compact(query_date),
                "sqlMethod": "StockNo",
                "stockNo": stock_code,
                "stockName": "",
            }

            html = self.post_text(
                TDCC_PORTAL_URL,
                data=payload,
                headers={"Referer": TDCC_PORTAL_URL},
            )
            try:
                self._update_form_state_from_html(html)
                snapshot = self._parse_snapshot_html(html=html, stock_code=stock_code, query_date=query_date)
                self._save_cache_text(namespace="tdcc_history", cache_key=disk_cache_key, text=html)
                self._snapshot_cache[cache_key] = snapshot
                return snapshot
            except DataNotFoundError:
                # 確認查無資料：寫入負快取，避免之後（尤其回測）重複打官方查詢頁。
                self._save_not_found(stock_code=stock_code, query_date=query_date, disk_cache_key=disk_cache_key)
                raise
            except ApiClientError:
                self.refresh_form_state()
                if attempt == 1:
                    raise

        raise ApiClientError(f"TDCC 歷史資料抓取失敗：{stock_code} {query_date.isoformat()}")

    def _parse_snapshot_html(self, html: str, stock_code: str, query_date: date) -> ShareholdingSnapshot:
        """解析 TDCC 查詢頁回傳的 HTML 表格。"""

        soup = BeautifulSoup(html, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        if "查無資料" in page_text or "無符合條件" in page_text:
            raise DataNotFoundError(f"TDCC 查無資料：{stock_code} {query_date.isoformat()}")

        target_table = None
        for table in soup.find_all("table"):
            headers = [normalize_text(cell.get_text()) for cell in table.find_all("th")]
            if {"序", "持股/單位數分級", "人數", "股數/單位數", "占集保庫存數比例 (%)"}.issubset(set(headers)):
                target_table = table
                break
        if target_table is None:
            raise ApiClientError("TDCC 回應格式異常，找不到股權分散表")

        bucket_lookup = {item["bucket_id"]: item for item in TDCC_BUCKET_DEFINITIONS}
        buckets: list[ShareholdingBucketRecord] = []
        for row in target_table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            bucket_id = coerce_int(cells[0].get_text())
            label = normalize_text(cells[1].get_text())
            if bucket_id is None or not label:
                continue
            definition = bucket_lookup.get(bucket_id, {})
            compact_label = label.replace(" ", "")
            is_total = "合計" in compact_label
            is_adjustment = "差異數調整" in compact_label
            buckets.append(
                ShareholdingBucketRecord(
                    bucket_id=bucket_id,
                    label=label,
                    holder_count=coerce_int(cells[2].get_text()),
                    share_count=coerce_int(cells[3].get_text()),
                    ratio=(coerce_float(cells[4].get_text()) or 0.0) / 100,
                    is_adjustment=is_adjustment or bool(definition.get("is_adjustment", False)),
                    is_total=is_total or bool(definition.get("is_total", False)),
                )
            )

        if not buckets:
            raise ApiClientError(f"TDCC 回應格式異常，未解析到任何分級資料：{stock_code} {query_date.isoformat()}")

        buckets.sort(key=lambda bucket: bucket.bucket_id)
        return ShareholdingSnapshot(
            stock_code=stock_code,
            stock_name="",
            data_date=query_date,
            buckets=buckets,
            source="tdcc_portal_history",
        )


class TWSEApiClient(BaseHttpClient):
    """負責抓取 TWSE 上市清單與個股歷史日線。"""

    def fetch_listed_stocks(self) -> dict[str, StockInfo]:
        """抓取上市公司清單並濾出第一版要處理的股票 universe。"""

        payload = self.get_json(
            TWSE_LISTED_COMPANIES_URL,
            cache_namespace="twse_universe",
            cache_key="listed_companies",
            cache_max_age_seconds=self.cache_settings.universe_ttl_seconds,
        )
        if not isinstance(payload, list):
            raise ApiClientError("TWSE 上市公司清單格式異常")

        stocks: dict[str, StockInfo] = {}
        for raw_row in payload:
            if not isinstance(raw_row, dict):
                continue
            row = sanitize_mapping_keys(raw_row)
            stock_code = normalize_stock_code(pick_first_present(row, TWSE_COMPANY_FIELD_ALIASES["stock_code"]))
            if len(stock_code) != VALID_LISTED_STOCK_CODE_LENGTH:
                continue

            short_name = normalize_text(pick_first_present(row, TWSE_COMPANY_FIELD_ALIASES["short_name"]))
            if any(keyword in short_name for keyword in EXCLUDED_SHORT_NAME_KEYWORDS):
                continue

            # 與 TPEX 一致：上市日期無法解析時仍保留該股（非回測不會用到此欄位，
            # 回測則把 None 視為「日期未知、先保留」），避免默默縮小上市股票 universe。
            listed_date = parse_tdcc_or_twse_date(pick_first_present(row, TWSE_COMPANY_FIELD_ALIASES["listed_date"]))
            name = normalize_text(pick_first_present(row, TWSE_COMPANY_FIELD_ALIASES["stock_name"]))
            stocks[stock_code] = StockInfo(
                code=stock_code,
                name=name,
                short_name=short_name or name,
                market="上市",
                listed_date=listed_date,
                industry=normalize_text(pick_first_present(row, TWSE_COMPANY_FIELD_ALIASES["industry"])) or None,
                is_ky="KY" in (short_name or name),
                metadata={key: normalize_text(value) for key, value in row.items()},
            )

        return dict(sorted(stocks.items()))

    def fetch_stock_day_history(self, stock_code: str, months: int) -> list[PriceBar]:
        """抓取個股最近幾個月的 TWSE 官方日線資料。"""

        bars_by_date: dict[date, PriceBar] = {}
        month_starts = iter_recent_month_starts(anchor_date=date.today(), months=months)

        for month_start in month_starts:
            try:
                payload = self.get_json(
                    TWSE_STOCK_DAY_URL,
                    params={
                        "date": month_start.strftime("%Y%m01"),
                        "stockNo": stock_code,
                        "response": "json",
                    },
                    cache_namespace="twse_price",
                    cache_key=f"{stock_code}_{month_start:%Y%m}",
                    cache_max_age_seconds=self.cache_settings.price_ttl_seconds,
                )
            except ApiClientError as exc:
                self.logger.warning("TWSE 月資料抓取失敗：%s %s (%s)", stock_code, month_start.strftime("%Y-%m"), exc)
                continue
            if not isinstance(payload, dict):
                continue
            if normalize_text(payload.get("stat")) != "OK":
                self.logger.warning("TWSE 月資料無法取得：%s %s", stock_code, month_start.strftime("%Y-%m"))
                continue

            fields = [normalize_text(field) for field in payload.get("fields", [])]
            data_rows = payload.get("data", [])
            field_index = {field_name: index for index, field_name in enumerate(fields)}
            if not data_rows or "日期" not in field_index or "收盤價" not in field_index:
                continue

            for row_values in data_rows:
                if not isinstance(row_values, list):
                    continue
                trade_date = parse_tdcc_or_twse_date(row_values[field_index["日期"]])
                if trade_date is None:
                    continue
                bars_by_date[trade_date] = PriceBar(
                    trade_date=trade_date,
                    open_price=coerce_float(self._pick_field_value(row_values, field_index, ("開盤價",))),
                    high_price=coerce_float(self._pick_field_value(row_values, field_index, ("最高價",))),
                    low_price=coerce_float(self._pick_field_value(row_values, field_index, ("最低價",))),
                    close_price=coerce_float(self._pick_field_value(row_values, field_index, ("收盤價",))),
                    volume=coerce_int(self._pick_field_value(row_values, field_index, ("成交股數",))),
                    turnover=coerce_int(self._pick_field_value(row_values, field_index, ("成交金額",))),
                )

        return [bars_by_date[trade_date] for trade_date in sorted(bars_by_date)]

    def _pick_field_value(self, row_values: list[Any], field_index: dict[str, int], aliases: tuple[str, ...]) -> Any:
        """依欄位名稱安全取回單一欄位值。"""

        for alias in aliases:
            if alias in field_index and field_index[alias] < len(row_values):
                return row_values[field_index[alias]]
        return None


class TPEXApiClient(BaseHttpClient):
    """負責抓取 TPEX 上櫃清單與個股歷史日線。"""

    def fetch_otc_stocks(self) -> dict[str, StockInfo]:
        """抓取上櫃公司基本資料。"""

        payload = self.get_json(
            TPEX_OTC_COMPANIES_URL,
            cache_namespace="tpex_universe",
            cache_key="otc_companies",
            cache_max_age_seconds=self.cache_settings.universe_ttl_seconds,
        )
        if not isinstance(payload, list):
            raise ApiClientError("TPEX 上櫃公司清單格式異常")

        stocks: dict[str, StockInfo] = {}
        for raw_row in payload:
            if not isinstance(raw_row, dict):
                continue
            row = sanitize_mapping_keys(raw_row)
            stock_code = normalize_stock_code(pick_first_present(row, TPEX_COMPANY_FIELD_ALIASES["stock_code"]))
            if len(stock_code) != VALID_LISTED_STOCK_CODE_LENGTH:
                continue

            short_name = normalize_text(pick_first_present(row, TPEX_COMPANY_FIELD_ALIASES["short_name"]))
            if any(keyword in short_name for keyword in EXCLUDED_SHORT_NAME_KEYWORDS):
                continue

            listed_date = parse_tdcc_or_twse_date(pick_first_present(row, TPEX_COMPANY_FIELD_ALIASES["listed_date"]))
            name = normalize_text(pick_first_present(row, TPEX_COMPANY_FIELD_ALIASES["stock_name"]))
            stocks[stock_code] = StockInfo(
                code=stock_code,
                name=name,
                short_name=short_name or name,
                market="上櫃",
                listed_date=listed_date,
                industry=normalize_text(pick_first_present(row, TPEX_COMPANY_FIELD_ALIASES["industry"])) or None,
                is_ky="KY" in (short_name or name),
                metadata={key: normalize_text(value) for key, value in row.items()},
            )

        return dict(sorted(stocks.items()))

    def fetch_stock_day_history(self, stock_code: str, months: int) -> list[PriceBar]:
        """抓取上櫃個股最近幾個月的歷史日線資料。"""

        bars_by_date: dict[date, PriceBar] = {}
        month_starts = iter_recent_month_starts(anchor_date=date.today(), months=months)

        for month_start in month_starts:
            try:
                payload = self.get_json(
                    TPEX_OTC_STOCK_DAY_URL,
                    params={
                        "code": stock_code,
                        "date": month_start.strftime("%Y/%m/01"),
                    },
                    cache_namespace="tpex_price",
                    cache_key=f"{stock_code}_{month_start:%Y%m}",
                    cache_max_age_seconds=self.cache_settings.price_ttl_seconds,
                )
            except ApiClientError as exc:
                self.logger.warning("TPEX 月資料抓取失敗：%s %s (%s)", stock_code, month_start.strftime("%Y-%m"), exc)
                continue
            if not isinstance(payload, dict) or normalize_text(payload.get("stat")).lower() != "ok":
                self.logger.warning("TPEX 月資料無法取得：%s %s", stock_code, month_start.strftime("%Y-%m"))
                continue

            tables = payload.get("tables")
            if not isinstance(tables, list) or not tables:
                continue

            data_rows = tables[0].get("data", [])
            for row_values in data_rows:
                if not isinstance(row_values, list) or len(row_values) < 9:
                    continue
                trade_date = parse_tdcc_or_twse_date(row_values[0])
                if trade_date is None:
                    continue
                bars_by_date[trade_date] = PriceBar(
                    trade_date=trade_date,
                    volume=coerce_int(row_values[1]),
                    turnover=coerce_int(row_values[2]),
                    open_price=coerce_float(row_values[3]),
                    high_price=coerce_float(row_values[4]),
                    low_price=coerce_float(row_values[5]),
                    close_price=coerce_float(row_values[6]),
                )

        return [bars_by_date[trade_date] for trade_date in sorted(bars_by_date)]