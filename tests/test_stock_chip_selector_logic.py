from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "stock_chip_selector"))

from broker_analyzer import find_consecutive_buying_streaks
from holder_analyzer import analyze_holder_change, get_holder_history


def test_holder_change_uses_true_four_weeks_ago_baseline():
    holder_df = pd.DataFrame(
        [
            ("2026-03-28", "2454", "聯發科", 150000),
            ("2026-04-04", "2454", "聯發科", 148500),
            ("2026-04-11", "2454", "聯發科", 146000),
            ("2026-04-18", "2454", "聯發科", 144000),
            ("2026-04-25", "2454", "聯發科", 142500),
        ],
        columns=["week_date", "stock_id", "stock_name", "holder_count"],
    )
    holder_df["week_date"] = pd.to_datetime(holder_df["week_date"])

    result = analyze_holder_change(holder_df, "2454", observation_weeks=4, min_decrease_pct=0.0)

    assert result["early_holder"] == 150000
    assert result["latest_holder"] == 142500
    assert result["change_rate"] == -5.0


def test_get_holder_history_includes_baseline_point():
    holder_df = pd.DataFrame(
        [
            ("2026-03-28", "2330", "台積電", 200000),
            ("2026-04-04", "2330", "台積電", 199200),
            ("2026-04-11", "2330", "台積電", 197800),
            ("2026-04-18", "2330", "台積電", 196000),
            ("2026-04-25", "2330", "台積電", 194000),
        ],
        columns=["week_date", "stock_id", "stock_name", "holder_count"],
    )
    holder_df["week_date"] = pd.to_datetime(holder_df["week_date"])

    history = get_holder_history(holder_df, "2330", observation_weeks=4)

    assert len(history) == 5
    assert history.iloc[0]["holder_count"] == 200000
    assert history.iloc[-1]["holder_count"] == 194000


def test_missing_branch_trading_day_breaks_streak():
    broker_df = pd.DataFrame(
        [
            ("2026-04-21", "2330", "台積電", "元大證券", "元大台北", 50, 10, 40),
            ("2026-04-21", "2330", "台積電", "凱基證券", "凱基台北", 30, 5, 25),
            ("2026-04-22", "2330", "台積電", "凱基證券", "凱基台北", 20, 5, 15),
            ("2026-04-23", "2330", "台積電", "元大證券", "元大台北", 60, 10, 50),
            ("2026-04-23", "2330", "台積電", "凱基證券", "凱基台北", 20, 5, 15),
        ],
        columns=[
            "date",
            "stock_id",
            "stock_name",
            "broker",
            "branch",
            "buy_volume",
            "sell_volume",
            "net_buy",
        ],
    )
    broker_df["date"] = pd.to_datetime(broker_df["date"])

    summary_df, records = find_consecutive_buying_streaks(broker_df, min_days=2, strict_mode=False)

    assert summary_df["branch"].tolist() == ["凱基台北"]
    assert records[0]["streak_days"] == 3
