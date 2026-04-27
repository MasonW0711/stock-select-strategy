# ==========================================
# holder_analyzer.py - 集保戶數變化分析
# ==========================================

import pandas as pd
import numpy as np
from typing import Any, Dict, Mapping, Union

HolderSource = Union[pd.DataFrame, Mapping[str, pd.DataFrame]]


def analyze_holder_change(
    holder_df: HolderSource,
    stock_id: str,
    observation_weeks: int = 4,
    min_decrease_pct: float = 0.0
) -> Dict[str, Any]:
    """
    分析指定股票近 N 週的集保戶數變化。

    判斷邏輯：
        最新集保戶數 < N週前集保戶數 → 籌碼集中，符合條件

    參數:
        holder_df: 集保戶數資料 DataFrame
        stock_id: 欲查詢的股票代號
        observation_weeks: 觀察週數（預設 4 週）
        min_decrease_pct: 最小下降幅度（百分比），0 表示只要有下降即可

    回傳:
        dict 包含以下鍵值：
        - latest_holder: 最新集保戶數
        - early_holder: N週前集保戶數
        - latest_date: 最新資料日期
        - early_date: 基準比較日期
        - change_rate: 集保戶數變化率（百分比，負值代表下降）
        - is_decreasing: 是否下降（bool）
        - pass_filter: 是否通過篩選條件（bool）
    """
    # 篩選該股票的集保資料
    stock_holders = _get_stock_holders(holder_df, stock_id)
    if stock_holders.empty:
        return _empty_holder_result()

    latest_record = stock_holders.iloc[-1]
    latest_holder = latest_record['holder_count']
    latest_date = latest_record['week_date']

    # 取得 N 週前的基準資料。
    # 例如 observation_weeks=4 時，要比較「最新」與「4 週前」，
    # 因此至少需要 5 筆資料（最新 + 往前 4 週）。
    required_points = observation_weeks + 1
    if len(stock_holders) < required_points:
        # 資料不足完整區間時，退回以最早的一筆作為基準
        early_record = stock_holders.iloc[0]
    else:
        early_record = stock_holders.iloc[-required_points]

    early_holder = early_record['holder_count']
    early_date = early_record['week_date']

    # 基準戶數為 0 時無法計算
    if early_holder == 0 or pd.isna(early_holder):
        return {
            'latest_holder': int(latest_holder) if not pd.isna(latest_holder) else None,
            'early_holder': int(early_holder) if not pd.isna(early_holder) else None,
            'latest_date': latest_date,
            'early_date': early_date,
            'change_rate': None,
            'is_decreasing': False,
            'pass_filter': False,
        }

    # 計算變化率
    change_rate = (latest_holder - early_holder) / early_holder * 100.0
    is_decreasing = change_rate < 0

    # 通過篩選條件：必須下降，且下降幅度須達到指定門檻
    pass_filter = is_decreasing and abs(change_rate) >= min_decrease_pct

    return {
        'latest_holder': int(latest_holder),
        'early_holder': int(early_holder),
        'latest_date': latest_date,
        'early_date': early_date,
        'change_rate': round(change_rate, 4),
        'is_decreasing': is_decreasing,
        'pass_filter': pass_filter,
    }


def get_holder_history(
    holder_df: HolderSource,
    stock_id: str,
    observation_weeks: int = 4
) -> pd.DataFrame:
    """
    取得指定股票近 N 週的集保戶數歷史資料。

    回傳:
        依週別排序的 DataFrame（最多 observation_weeks + 1 筆，含比較基準）
    """
    stock_holders = _get_stock_holders(holder_df, stock_id)
    if stock_holders.empty:
        return pd.DataFrame()

    required_points = observation_weeks + 1
    return stock_holders.tail(required_points).reset_index(drop=True)


def build_holder_lookup(holder_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    依股票代號預先整理集保歷史，供篩選迴圈重複使用。
    """
    if holder_df.empty:
        return {}

    return {
        stock_id: _prepare_stock_holders(group)
        for stock_id, group in holder_df.groupby('stock_id', sort=False)
    }


def _empty_holder_result() -> Dict[str, Any]:
    """
    無集保資料時回傳安全的預設值（不通過篩選）。
    """
    return {
        'latest_holder': None,
        'early_holder': None,
        'latest_date': None,
        'early_date': None,
        'change_rate': None,
        'is_decreasing': False,
        'pass_filter': False,
    }


def _prepare_stock_holders(stock_holders: pd.DataFrame) -> pd.DataFrame:
    return (
        stock_holders
        .sort_values('week_date')
        .drop_duplicates(subset=['week_date'], keep='last')
        .reset_index(drop=True)
    )


def _get_stock_holders(holder_source: HolderSource, stock_id: str) -> pd.DataFrame:
    if isinstance(holder_source, Mapping):
        stock_holders = holder_source.get(stock_id)
        if stock_holders is None:
            return pd.DataFrame()
        return stock_holders.copy()

    stock_holders = holder_source[holder_source['stock_id'] == stock_id].copy()
    if stock_holders.empty:
        return stock_holders
    return _prepare_stock_holders(stock_holders)
