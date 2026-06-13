from __future__ import annotations

from config import (
    SCORE_HOLDER_DECLINE,
    SCORE_LABEL_THRESHOLDS,
    SCORE_LARGE_HOLDER_GROWTH,
    SCORE_MA_PROXIMITY,
    SCORE_RETURN_DEEP_DROP,
    SCORE_RETURN_MILDNESS,
)

ScoreTable = tuple[tuple[float, int], ...]


def _score_at_least(value: float | None, table: ScoreTable) -> int:
    """越大越好且必須為正：回傳第一個 value >= 門檻 的分數，table 由高門檻往低排列。

    value <= 0 一律 0 分：這些維度（大戶增幅、集保降幅）代表「方向訊號」，
    零成長或反向（例如關閉硬性過濾時集保戶數其實在增加）不應領到地板分。
    """

    if value is None or value <= 0:
        return 0
    for threshold, points in table:
        if value >= threshold:
            return points
    return 0


def _score_at_most(value: float | None, table: ScoreTable) -> int:
    """越小越好：回傳第一個 value <= 門檻 的分數，table 由低門檻往高排列。"""

    if value is None:
        return 0
    for threshold, points in table:
        if value <= threshold:
            return points
    return 0


def score_large_holder_growth(growth_ratio: float | None) -> int:
    """大戶人數增幅評分（取代原『連續買超強度／主力吸籌』）。"""

    return _score_at_least(growth_ratio, SCORE_LARGE_HOLDER_GROWTH)


def score_holder_decline(decline_ratio: float | None) -> int:
    """集保總戶數降幅評分；decline_ratio 為下降的正比例（0.05 = 降 5%）。"""

    return _score_at_least(decline_ratio, SCORE_HOLDER_DECLINE)


def score_ma_proximity(distance_to_ma: float | None) -> int:
    """距 20MA 評分（取代原『現價接近主力成本』）。"""

    return _score_at_most(distance_to_ma, SCORE_MA_PROXIMITY)


def score_return_mildness(three_month_return: float | None) -> int:
    """近三月漲幅溫和度評分；小幅或溫和負報酬視為溫和、得高分。

    但深跌（跌幅超過 SCORE_RETURN_DEEP_DROP）屬於弱勢/可能基本面有事，
    並非「仍有上行空間」，故直接 0 分，避免崩跌股反而拿滿分。
    """

    if three_month_return is not None and three_month_return < SCORE_RETURN_DEEP_DROP:
        return 0
    return _score_at_most(three_month_return, SCORE_RETURN_MILDNESS)


def calculate_score(
    large_holder_growth: float | None,
    holder_decline: float | None,
    distance_to_ma: float | None,
    three_month_return: float | None,
) -> int:
    """彙整四個線上維度為 0-100 綜合分數。

    各維度的分項分數可分別由上面的 score_* 函式取得，UI 評分明細與此處共用同一邏輯，
    確保「明細加總 == 綜合分數」。
    """

    score = (
        score_large_holder_growth(large_holder_growth)
        + score_holder_decline(holder_decline)
        + score_ma_proximity(distance_to_ma)
        + score_return_mildness(three_month_return)
    )
    return min(score, 100)


def get_score_label(score: int) -> str:
    """依綜合分數回傳評級標籤。"""

    for threshold, label in SCORE_LABEL_THRESHOLDS:
        if score >= threshold:
            return label
    return SCORE_LABEL_THRESHOLDS[-1][1]
