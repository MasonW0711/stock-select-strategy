# ==========================================
# scoring.py - 選股分數計算（滿分 100 分）
# ==========================================

import numpy as np
from typing import Optional


def calculate_score(
    streak_days: int,
    trend_type: str,
    price_deviation_pct: float,
    holder_change_rate: float
) -> int:
    """
    依據四個維度計算選股綜合分數（滿分 100 分）。

    評分項目與權重：
    ┌─────────────────────────────┬─────┐
    │ 項目                        │ 滿分 │
    ├─────────────────────────────┼─────┤
    │ 1. 連續買超天數              │ 30  │
    │ 2. 買超張數趨勢              │ 20  │
    │ 3. 現價接近主力成本          │ 25  │
    │ 4. 集保戶數下降              │ 25  │
    └─────────────────────────────┴─────┘

    此函數在篩選後才被呼叫，因此 price_deviation_pct <= max_threshold
    且 holder_change_rate < 0 皆已確保。

    回傳:
        整數分數（0-100）
    """
    score = 0

    # ── 1. 連續買超天數（最高 30 分） ──
    score += _score_streak_days(streak_days)

    # ── 2. 買超張數趨勢（最高 20 分） ──
    score += _score_trend(trend_type)

    # ── 3. 現價接近主力成本（最高 25 分） ──
    score += _score_price_deviation(price_deviation_pct)

    # ── 4. 集保戶數下降（最高 25 分） ──
    score += _score_holder_change(holder_change_rate)

    return min(score, 100)


def _score_streak_days(streak_days: int) -> int:
    """
    連續買超天數評分：
    - 3 天 → 20 分
    - 4 天 → 25 分
    - 5 天以上 → 30 分
    """
    if streak_days >= 5:
        return 30
    elif streak_days == 4:
        return 25
    elif streak_days == 3:
        return 20
    else:
        return 0


def _score_trend(trend_type: str) -> int:
    """
    買超張數趨勢評分：
    - 逐日增加 → 20 分（最理想，代表主力加速佈局）
    - 大多增加 → 15 分（多數日子有增加）
    - 僅連續買超 → 10 分（維持買超但無增加趨勢）
    """
    mapping = {
        '逐日增加': 20,
        '大多增加': 15,
        '僅連續買超': 10,
    }
    return mapping.get(trend_type, 0)


def _score_price_deviation(deviation_pct: float) -> int:
    """
    現價接近主力成本評分：
    - 低於主力成本（deviation < 0）   → 15 分
    - 0% ~ 3%（非常接近）              → 25 分
    - 3% ~ 5%（略高）                  → 20 分
    - 超過 5%（已超過篩選門檻，不應出現）→  0 分
    """
    if np.isnan(deviation_pct):
        return 0
    elif deviation_pct < 0:
        return 15
    elif deviation_pct <= 3:
        return 25
    elif deviation_pct <= 5:
        return 20
    else:
        # 此情況理應已在篩選階段排除，防禦性處理
        return 0


def _score_holder_change(change_rate: Optional[float]) -> int:
    """
    集保戶數下降評分：
    - 下降 5% 以上   → 25 分（強烈籌碼集中）
    - 下降 3% ~ 5%  → 20 分（明顯籌碼集中）
    - 下降 0% ~ 3%  → 15 分（輕微籌碼集中）
    - 沒有下降       →  0 分（已在篩選階段排除）
    """
    if change_rate is None or np.isnan(change_rate):
        return 0
    elif change_rate >= 0:
        return 0
    elif change_rate <= -5:
        return 25
    elif change_rate <= -3:
        return 20
    else:
        # -3% ~ 0%
        return 15


def get_score_label(score: int) -> str:
    """
    依據分數產生評級標籤（含 emoji 方便視覺辨識）。

    - 85分以上 → ⭐⭐⭐ 強力推薦
    - 70~84分  → ⭐⭐ 值得關注
    - 55~69分  → ⭐ 符合條件
    - 55分以下 → 基本符合
    """
    if score >= 85:
        return '⭐⭐⭐ 強力推薦'
    elif score >= 70:
        return '⭐⭐ 值得關注'
    elif score >= 55:
        return '⭐ 符合條件'
    else:
        return '基本符合'
