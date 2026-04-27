# ==========================================
# data_loader.py - 資料讀取與 API 串接（CSV 匯入模式）
# ==========================================

from typing import Iterable

import numpy as np
import pandas as pd


def _validate_required_columns(
    df: pd.DataFrame,
    required_columns: Iterable[str],
    dataset_name: str
) -> None:
    """確認必要欄位齊全，缺漏時回傳清楚錯誤訊息。"""
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"{dataset_name} 缺少必要欄位：{missing_text}")


def _normalize_stock_ids(series: pd.Series) -> pd.Series:
    """將股票代號統一成不帶空白、不帶尾端 .0 的字串。"""
    def normalize(value) -> str | None:
        if pd.isna(value):
            return None
        if isinstance(value, (int, np.integer)):
            return str(int(value))
        if isinstance(value, (float, np.floating)) and float(value).is_integer():
            return str(int(value))

        text = str(value).strip()
        if text.endswith(".0"):
            text = text[:-2]
        return text or None

    return series.map(normalize)


def load_broker_trading(file) -> pd.DataFrame:
    """
    載入券商分點買賣超資料（CSV / Excel 匯入模式）

    必要欄位：date, stock_id, stock_name, broker, branch,
              buy_volume, sell_volume, net_buy
    選擇欄位：buy_avg_price（缺失時將由收盤價補充）

    回傳：清理後的 DataFrame
    """
    try:
        df = pd.read_csv(file)

        # 統一欄位名稱（去除前後空白）
        df.columns = df.columns.astype(str).str.strip()
        _validate_required_columns(
            df,
            ['date', 'stock_id', 'stock_name', 'broker', 'branch', 'buy_volume', 'sell_volume', 'net_buy'],
            '券商分點資料',
        )

        # 日期欄位轉換
        df['date'] = pd.to_datetime(df['date'], errors='coerce')

        # 數值欄位強制轉型，無效值填 0
        for col in ['buy_volume', 'sell_volume', 'net_buy']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

        # 買進均價可缺失，轉型後保留 NaN 以供後續補充
        if 'buy_avg_price' in df.columns:
            df['buy_avg_price'] = pd.to_numeric(df['buy_avg_price'], errors='coerce')
        else:
            df['buy_avg_price'] = np.nan

        # 移除日期或股票代號缺失的列
        df['stock_id'] = _normalize_stock_ids(df['stock_id'])
        df = df.dropna(subset=['date', 'stock_id'])

        # 股票代號統一為字串並去除空白
        df['stock_id'] = df['stock_id'].astype(str)

        # 確保文字欄位不含 NaN
        for col in ['stock_name', 'broker', 'branch']:
            if col in df.columns:
                df[col] = df[col].fillna('').astype(str).str.strip()

        return df.reset_index(drop=True)

    except Exception as e:
        raise ValueError(f"載入券商分點資料失敗：{e}")


def load_price_data(file) -> pd.DataFrame:
    """
    載入股價資料（CSV / Excel 匯入模式）

    必要欄位：date, stock_id, open, high, low, close, volume
    """
    try:
        df = pd.read_csv(file)
        df.columns = df.columns.astype(str).str.strip()
        _validate_required_columns(
            df,
            ['date', 'stock_id', 'open', 'high', 'low', 'close', 'volume'],
            '股價資料',
        )

        df['date'] = pd.to_datetime(df['date'], errors='coerce')

        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        df['stock_id'] = _normalize_stock_ids(df['stock_id'])
        df = df.dropna(subset=['date', 'stock_id', 'close'])
        df['stock_id'] = df['stock_id'].astype(str)

        if 'stock_name' in df.columns:
            df['stock_name'] = df['stock_name'].fillna('').astype(str).str.strip()

        return df.reset_index(drop=True)

    except Exception as e:
        raise ValueError(f"載入股價資料失敗：{e}")


def load_holder_data(file) -> pd.DataFrame:
    """
    載入集保戶數資料（CSV / Excel 匯入模式）

    必要欄位：week_date, stock_id, holder_count
    """
    try:
        df = pd.read_csv(file)
        df.columns = df.columns.astype(str).str.strip()
        _validate_required_columns(
            df,
            ['week_date', 'stock_id', 'holder_count'],
            '集保戶數資料',
        )

        df['week_date'] = pd.to_datetime(df['week_date'], errors='coerce')
        df['holder_count'] = pd.to_numeric(df['holder_count'], errors='coerce')

        df['stock_id'] = _normalize_stock_ids(df['stock_id'])
        df = df.dropna(subset=['week_date', 'stock_id', 'holder_count'])
        df['stock_id'] = df['stock_id'].astype(str)

        if 'stock_name' in df.columns:
            df['stock_name'] = df['stock_name'].fillna('').astype(str).str.strip()

        return df.reset_index(drop=True)

    except Exception as e:
        raise ValueError(f"載入集保戶數資料失敗：{e}")


def merge_price_to_broker(
    broker_df: pd.DataFrame,
    price_df: pd.DataFrame
) -> pd.DataFrame:
    """
    將每日收盤價合併至券商分點資料，用於補充缺失的買進均價。

    新增欄位 effective_buy_price：
    - 若 buy_avg_price 有值 → 使用 buy_avg_price
    - 若 buy_avg_price 缺失 → 使用當日收盤價近似
    """
    price_simple = (
        price_df[['date', 'stock_id', 'close']]
        .sort_values(['stock_id', 'date'])
        .drop_duplicates(subset=['date', 'stock_id'], keep='last')
        .copy()
    )

    merged = broker_df.merge(
        price_simple,
        on=['date', 'stock_id'],
        how='left'
    )

    # 買進均價缺失時以收盤價代替
    merged['effective_buy_price'] = merged['buy_avg_price'].where(
        merged['buy_avg_price'].notna(),
        merged['close']
    )

    return merged


def filter_by_volume(
    broker_df: pd.DataFrame,
    price_df: pd.DataFrame,
    min_volume: int
) -> pd.DataFrame:
    """
    依據最低平均成交量過濾低流動性股票。

    回傳：過濾後的 broker_df（僅保留成交量達標的股票）
    """
    if min_volume <= 0:
        return broker_df

    avg_vol = price_df.groupby('stock_id')['volume'].mean()
    valid_ids = avg_vol[avg_vol >= min_volume].index.tolist()

    return broker_df[broker_df['stock_id'].isin(valid_ids)].copy()


# ──────────────────────────────────────────────
# TODO：API / 爬蟲模式（未來可補充下列函數）
# ──────────────────────────────────────────────

# def fetch_broker_trading_from_api(
#     stock_id: str, start_date: str, end_date: str
# ) -> pd.DataFrame:
#     """
#     TODO: 從 API 取得券商分點買賣超資料
#     可考慮串接：
#     - 富果 API (Fugle MarketData)：https://developer.fugle.tw/
#     - 永豐金 SinoPac API：https://sinopacapi.github.io/
#     - 台灣證券交易所公開資料：https://www.twse.com.tw/
#     """
#     raise NotImplementedError("API 模式尚未實作，請使用 CSV 匯入模式")


# def fetch_price_from_twse(stock_id: str, date: str) -> pd.DataFrame:
#     """
#     TODO: 從台灣證券交易所取得個股日線資料
#     參考：https://www.twse.com.tw/exchangeReport/STOCK_DAY
#     """
#     raise NotImplementedError("API 模式尚未實作，請使用 CSV 匯入模式")


# def fetch_holder_from_tdcc(stock_id: str) -> pd.DataFrame:
#     """
#     TODO: 從台灣集中保管結算所取得集保戶數統計
#     參考：https://www.tdcc.com.tw/portal/zh/smoffshore/stat
#     """
#     raise NotImplementedError("API 模式尚未實作，請使用 CSV 匯入模式")
