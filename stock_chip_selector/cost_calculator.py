# ==========================================
# cost_calculator.py - 主力平均成本計算
# ==========================================

import pandas as pd
import numpy as np
from typing import Dict


def calculate_average_cost(streak_data: pd.DataFrame) -> float:
    """
    計算連續買超期間的加權平均買進成本。

    計算邏輯（優先順序）：
    1. 若有 effective_buy_price（已補充缺失值的均價）→ 優先使用
    2. 若有 buy_avg_price → 次選
    3. 都沒有 → 回傳 NaN

    公式：
        平均成本 = Σ(買進均價 × 買超張數) / Σ(買超張數)

    參數:
        streak_data: 連續買超期間的 DataFrame（每日一列）

    回傳:
        加權平均成本（float），無法計算時回傳 NaN
    """
    # 選擇價格欄位（優先使用已補充的有效均價）
    if 'effective_buy_price' in streak_data.columns:
        price_col = 'effective_buy_price'
    elif 'buy_avg_price' in streak_data.columns:
        price_col = 'buy_avg_price'
    else:
        return np.nan

    # 只計算正買超的記錄
    valid = streak_data[streak_data['net_buy'] > 0].copy()
    valid = valid.dropna(subset=[price_col, 'net_buy'])

    if valid.empty or valid['net_buy'].sum() == 0:
        return np.nan

    # 加權平均計算
    weighted_sum = (valid[price_col] * valid['net_buy']).sum()
    total_volume = valid['net_buy'].sum()

    return float(weighted_sum / total_volume)


def calculate_price_deviation(avg_cost: float, latest_close: float) -> float:
    """
    計算最新收盤價相對於主力平均成本的偏離率。

    公式：
        偏離率(%) = (最新收盤價 - 平均成本) / 平均成本 × 100

    回傳值意義：
        正值 → 現價高於主力成本（主力可能仍在佈局或已獲利）
        負值 → 現價低於主力成本（主力可能被套，需謹慎）

    回傳:
        偏離率（百分比），無效時回傳 NaN
    """
    if pd.isna(avg_cost) or pd.isna(latest_close) or avg_cost == 0:
        return np.nan

    return (latest_close - avg_cost) / avg_cost * 100.0


def get_latest_close(price_df: pd.DataFrame, stock_id: str) -> float:
    """
    從股價資料中取得指定股票的最新收盤價。

    回傳:
        最新收盤價（float），無資料時回傳 NaN
    """
    stock_prices = price_df[price_df['stock_id'] == stock_id].copy()

    if stock_prices.empty:
        return np.nan

    latest = stock_prices.sort_values('date').iloc[-1]
    return float(latest['close'])


def build_latest_close_lookup(price_df: pd.DataFrame) -> Dict[str, float]:
    """
    預先建立每檔股票的最新收盤價查找表，避免重複篩選與排序。
    """
    if price_df.empty:
        return {}

    latest_prices = (
        price_df.sort_values(['stock_id', 'date'])
        .drop_duplicates(subset=['stock_id'], keep='last')
    )
    return latest_prices.set_index('stock_id')['close'].astype(float).to_dict()


def get_price_deviation_label(deviation_pct: float) -> str:
    """
    依據現價偏離率產生人類可讀的標籤說明。
    """
    if pd.isna(deviation_pct):
        return '無法計算'
    elif deviation_pct < -10:
        return '遠低於主力成本（留意風險）'
    elif deviation_pct < 0:
        return '低於主力估算成本'
    elif deviation_pct <= 3:
        return '非常接近主力成本 ✓'
    elif deviation_pct <= 5:
        return '略高於主力成本 ✓'
    else:
        return '高於主力成本過多'
