from scoring import (
    calculate_score,
    get_score_label,
    score_holder_decline,
    score_large_holder_growth,
    score_ma_proximity,
    score_return_mildness,
)


def test_large_holder_growth_boundaries():
    assert score_large_holder_growth(0.12) == 30
    assert score_large_holder_growth(0.05) == 24
    assert score_large_holder_growth(0.02) == 18
    assert score_large_holder_growth(0.0) == 12
    assert score_large_holder_growth(-0.01) == 0
    assert score_large_holder_growth(None) == 0


def test_holder_decline_boundaries():
    assert score_holder_decline(0.06) == 25
    assert score_holder_decline(0.03) == 20
    assert score_holder_decline(0.01) == 15
    assert score_holder_decline(0.0) == 10
    assert score_holder_decline(None) == 0


def test_ma_proximity_boundaries():
    assert score_ma_proximity(0.01) == 25
    assert score_ma_proximity(0.03) == 20
    assert score_ma_proximity(0.05) == 15
    assert score_ma_proximity(0.08) == 10
    assert score_ma_proximity(0.09) == 0
    assert score_ma_proximity(None) == 0


def test_return_mildness_treats_negative_as_most_mild():
    assert score_return_mildness(-0.10) == 20
    assert score_return_mildness(0.05) == 20
    assert score_return_mildness(0.15) == 15
    assert score_return_mildness(0.25) == 10
    assert score_return_mildness(0.35) == 5
    assert score_return_mildness(0.45) == 0


def test_calculate_score_is_sum_capped_at_100():
    assert calculate_score(0.12, 0.06, 0.01, 0.05) == 100
    assert calculate_score(0.0, 0.0, 0.08, 0.40) == 12 + 10 + 10 + 5
    assert calculate_score(None, None, None, None) == 0


def test_score_equals_sum_of_components():
    components = score_large_holder_growth(0.04) + score_holder_decline(0.02) + score_ma_proximity(0.035) + score_return_mildness(0.22)
    assert calculate_score(0.04, 0.02, 0.035, 0.22) == min(components, 100)


def test_label_thresholds():
    assert get_score_label(90) == "⭐⭐⭐ 強力推薦"
    assert get_score_label(75) == "⭐⭐ 值得關注"
    assert get_score_label(60) == "⭐ 符合條件"
    assert get_score_label(40) == "基本符合"
    assert get_score_label(0) == "基本符合"
