from datetime import date

from config import DEFAULT_SCREEN_PARAMETERS
from screener import StockScreener


def _make_screener():
    # Provide minimal constructor args; clients are not used by _resolve_target_tdcc_dates
    return StockScreener(
        tdcc_latest_client=None,
        tdcc_history_client=None,
        twse_client=None,
        tpex_client=None,
        screen_params=DEFAULT_SCREEN_PARAMETERS,
        logger=None,
    )


def test_resolve_target_dates_with_start_date():
    screener = _make_screener()
    available = [
        date(2026, 4, 10),
        date(2026, 4, 3),
        date(2026, 3, 27),
        date(2026, 3, 20),
        date(2026, 3, 13),
    ]
    latest = date(2026, 4, 10)
    start = date(2026, 4, 3)
    resolved = screener._resolve_target_tdcc_dates(available, latest, start_date=start, warnings=[])
    assert resolved[0] == start
    assert len(resolved) == max(DEFAULT_SCREEN_PARAMETERS.consecutive_weeks, DEFAULT_SCREEN_PARAMETERS.min_history_weeks)


def test_resolve_target_dates_without_start_date_includes_latest():
    screener = _make_screener()
    available = [date(2026, 4, 3), date(2026, 3, 27), date(2026, 3, 20)]
    latest = date(2026, 4, 10)
    resolved = screener._resolve_target_tdcc_dates(available, latest, start_date=None, warnings=[])
    # latest should be inserted when missing
    assert resolved[0] == latest
    assert len(resolved) == max(DEFAULT_SCREEN_PARAMETERS.consecutive_weeks, DEFAULT_SCREEN_PARAMETERS.min_history_weeks)
