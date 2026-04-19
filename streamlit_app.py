from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import DEFAULT_CACHE_SETTINGS, DEFAULT_MARKETS, DEFAULT_SCREEN_PARAMETERS, MARKET_LABELS, CacheSettings, ScreenParameters
from main import build_summary_text, execute_screening, frames_to_excel_bytes, resolve_output_path
from utils import setup_logging


st.set_page_config(page_title="台股自動選股儀表板", page_icon="📈", layout="wide", initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700;900&family=IBM+Plex+Mono:wght@400;500&display=swap');
    .stApp {
        background:
            radial-gradient(circle at top left, rgba(235, 163, 52, 0.18), transparent 28%),
            radial-gradient(circle at top right, rgba(38, 112, 255, 0.12), transparent 22%),
            linear-gradient(180deg, #f7f1e8 0%, #f5f7fb 48%, #eef4f8 100%);
        color: #1f2937;
        font-family: 'Noto Sans TC', sans-serif;
    }
    .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    .hero-card {
        background: rgba(255, 255, 255, 0.78);
        border: 1px solid rgba(15, 23, 42, 0.08);
        border-radius: 24px;
        padding: 1.4rem 1.5rem;
        box-shadow: 0 24px 80px rgba(15, 23, 42, 0.08);
        backdrop-filter: blur(12px);
        margin-bottom: 1rem;
    }
    .hero-title {
        font-size: 2rem;
        font-weight: 900;
        letter-spacing: -0.03em;
        margin-bottom: 0.35rem;
    }
    .hero-subtitle {
        color: #475569;
        line-height: 1.6;
        margin-bottom: 0;
    }
    .metric-note {
        color: #64748b;
        font-size: 0.92rem;
        margin-top: -0.25rem;
    }
    div[data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.82);
        border: 1px solid rgba(15, 23, 42, 0.08);
        border-radius: 18px;
        padding: 0.9rem 1rem;
        box-shadow: 0 16px 48px rgba(15, 23, 42, 0.05);
    }
    div[data-testid="stMetric"] label {
        color: #64748b;
    }
    .stButton button, .stDownloadButton button {
        border-radius: 999px;
        font-weight: 700;
        border: none;
        padding: 0.65rem 1.1rem;
    }
    .stButton button {
        background: linear-gradient(135deg, #0f766e, #2563eb);
        color: white;
    }
    .stDownloadButton button {
        background: #ffffff;
        color: #0f172a;
        border: 1px solid rgba(15, 23, 42, 0.12);
    }
    .summary-panel {
        background: rgba(255, 255, 255, 0.78);
        border: 1px solid rgba(15, 23, 42, 0.08);
        border-radius: 20px;
        padding: 1rem 1.2rem;
        margin-top: 0.8rem;
    }
    .summary-panel ul {
        margin: 0;
        padding-left: 1.1rem;
    }
    .summary-panel li {
        margin: 0.2rem 0;
        color: #334155;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _build_screen_parameters(
    weeks: int,
    max_3m_return: float,
    max_distance_to_ma: float,
    min_price_days: int,
    price_history_months: int,
) -> ScreenParameters:
    """依 Streamlit 表單輸入建立篩選參數。"""

    return ScreenParameters(
        consecutive_weeks=weeks,
        max_3m_return=max_3m_return,
        ma_window=DEFAULT_SCREEN_PARAMETERS.ma_window,
        max_distance_to_ma=max_distance_to_ma,
        min_history_weeks=max(weeks, DEFAULT_SCREEN_PARAMETERS.min_history_weeks),
        min_price_days=min_price_days,
        price_history_months=max(price_history_months, 1),
        tdcc_date_buffer_weeks=DEFAULT_SCREEN_PARAMETERS.tdcc_date_buffer_weeks,
        trend_mode=DEFAULT_SCREEN_PARAMETERS.trend_mode,
    )


def _build_cache_settings(enabled: bool) -> CacheSettings:
    """建立 Streamlit 執行使用的快取設定。"""

    return CacheSettings(
        enabled=enabled,
        cache_dir=DEFAULT_CACHE_SETTINGS.cache_dir,
        universe_ttl_seconds=DEFAULT_CACHE_SETTINGS.universe_ttl_seconds,
        latest_snapshot_ttl_seconds=DEFAULT_CACHE_SETTINGS.latest_snapshot_ttl_seconds,
        tdcc_dates_ttl_seconds=DEFAULT_CACHE_SETTINGS.tdcc_dates_ttl_seconds,
        price_ttl_seconds=DEFAULT_CACHE_SETTINGS.price_ttl_seconds,
        historical_snapshot_ttl_seconds=DEFAULT_CACHE_SETTINGS.historical_snapshot_ttl_seconds,
    )


st.markdown(
    """
    <div class="hero-card">
      <div class="hero-title">台股自動選股儀表板</div>
      <p class="hero-subtitle">
        同一套 CLI 篩選核心，直接在瀏覽器執行上市 / 上櫃籌碼條件、價格條件、Excel 匯出與資料表檢視。
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)


with st.sidebar:
    st.subheader("執行設定")
    with st.form("screen_form"):
        selected_markets = st.multiselect(
            "市場",
            options=list(MARKET_LABELS.keys()),
            default=list(DEFAULT_MARKETS),
            format_func=lambda value: MARKET_LABELS[value],
        )
        stock_limit_enabled = st.checkbox("限制股票數量", value=False)
        stock_limit = st.number_input("股票數量上限", min_value=1, max_value=5000, value=50, step=1, disabled=not stock_limit_enabled)
        weeks = st.slider("連續觀察週數", min_value=1, max_value=8, value=DEFAULT_SCREEN_PARAMETERS.consecutive_weeks)
        max_3m_return = st.slider("近三個月最大漲幅", min_value=0.05, max_value=1.0, value=DEFAULT_SCREEN_PARAMETERS.max_3m_return, step=0.01)
        max_distance_to_ma = st.slider("距離 20MA 上限", min_value=0.01, max_value=0.2, value=DEFAULT_SCREEN_PARAMETERS.max_distance_to_ma, step=0.01)
        min_price_days = st.slider("最低價格日數", min_value=20, max_value=120, value=DEFAULT_SCREEN_PARAMETERS.min_price_days, step=5)
        price_history_months = st.slider("回抓價格月份數", min_value=2, max_value=12, value=DEFAULT_SCREEN_PARAMETERS.price_history_months)
        enable_cache = st.checkbox("啟用磁碟快取", value=True)
        log_level = st.selectbox("Log 等級", options=["INFO", "DEBUG"], index=0)
        submitted = st.form_submit_button("開始篩選", use_container_width=True)


if submitted:
    if not selected_markets:
        st.error("至少要選一個市場。")
    else:
        logger = setup_logging(log_level)
        screen_parameters = _build_screen_parameters(
            weeks=weeks,
            max_3m_return=max_3m_return,
            max_distance_to_ma=max_distance_to_ma,
            min_price_days=min_price_days,
            price_history_months=price_history_months,
        )
        cache_settings = _build_cache_settings(enable_cache)
        actual_stock_limit = int(stock_limit) if stock_limit_enabled else None

        with st.spinner("正在抓取資料並執行篩選，這可能需要一些時間..."):
            _, summary, frames = execute_screening(
                screen_parameters=screen_parameters,
                logger=logger,
                stock_limit=actual_stock_limit,
                markets=tuple(selected_markets),
                cache_settings=cache_settings,
            )
            excel_bytes = frames_to_excel_bytes(frames)
            output_path = resolve_output_path("")
            output_path.write_bytes(excel_bytes)

        st.session_state["screening_run"] = {
            "summary": summary,
            "frames": frames,
            "excel_bytes": excel_bytes,
            "output_path": str(output_path),
            "download_name": output_path.name,
        }


run_result = st.session_state.get("screening_run")
if run_result:
    summary = run_result["summary"]
    frames = run_result["frames"]
    excel_bytes = run_result["excel_bytes"]
    output_path = run_result["output_path"]

    metric_columns = st.columns(4)
    metric_columns[0].metric("分析檔數", summary.processed_count)
    metric_columns[1].metric("篩選通過", summary.passed_count)
    metric_columns[2].metric("價格檢查", summary.price_checked_count)
    metric_columns[3].metric("耗時秒數", f"{summary.elapsed_seconds:.2f}")
    st.markdown('<div class="metric-note">每次執行都會自動將 Excel 輸出到專案資料夾，同時保留下載按鈕。</div>', unsafe_allow_html=True)

    action_columns = st.columns([2, 1])
    action_columns[0].success(f"Excel 已輸出到：{output_path}")
    action_columns[1].download_button(
        label="下載 Excel",
        data=excel_bytes,
        file_name=run_result["download_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    summary_lines = "".join(f"<li>{line}</li>" for line in build_summary_text(summary))
    st.markdown(f'<div class="summary-panel"><strong>執行摘要</strong><ul>{summary_lines}</ul></div>', unsafe_allow_html=True)

    pass_tab, fail_tab, raw_tab = st.tabs(["通過清單", "未通過清單", "原始摘要"])
    with pass_tab:
        st.dataframe(frames["pass"], use_container_width=True, height=420)
    with fail_tab:
        st.dataframe(frames["fail"], use_container_width=True, height=420)
    with raw_tab:
        st.dataframe(frames["raw_data_summary"], use_container_width=True, height=520)
else:
    st.info("在左側設定條件後按下「開始篩選」，即可產生 CLI 同步邏輯的結果與 Excel。")