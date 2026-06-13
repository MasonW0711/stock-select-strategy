from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from api_clients import AccessBlockedError, ApiClientError
from config import (
    DEFAULT_HTTP_SETTINGS,
    DEFAULT_MARKETS,
    DEFAULT_SCREEN_PARAMETERS,
    MARKET_LABELS,
    CacheSettings,
    ScreenParameters,
    make_cache_settings,
    make_screen_parameters,
)
from main import build_summary_text, execute_screening, frames_to_excel_bytes, resolve_output_path
from utils import setup_logging, format_ratio_as_pct


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
    require_holder_decrease: bool,
    holder_decrease_weeks: int,
    min_holder_decrease_ratio: float,
) -> ScreenParameters:
    """依 Streamlit 表單輸入建立篩選參數（建構與 clamp 委派給 config.make_screen_parameters）。"""

    return make_screen_parameters(
        consecutive_weeks=weeks,
        max_3m_return=max_3m_return,
        max_distance_to_ma=max_distance_to_ma,
        min_price_days=min_price_days,
        price_history_months=price_history_months,
        require_holder_decrease=require_holder_decrease,
        holder_decrease_weeks=holder_decrease_weeks,
        min_holder_decrease_ratio=min_holder_decrease_ratio,
    )


def _collect_pass_trends(results: list) -> dict:
    """從通過股票擷取籌碼趨勢（已在結果物件內，供線上圖表使用，不需回抓資料）。"""

    trends: dict[str, dict] = {}
    for result in results:
        if not getattr(result, "passed", False):
            continue
        label = f"[{result.score}分] {result.code} {result.name}"
        trends[label] = {
            "code": result.code,
            "name": result.name,
            "score": result.score,
            "score_label": result.score_label,
            "dates": [value.isoformat() for value in result.tdcc_dates],
            "small": list(result.small_holder_trend),
            "mid": list(result.mid_holder_trend),
            "large": list(result.large_holder_trend),
            "total": list(result.total_holder_trend),
        }
    return trends


def _trend_to_frame(dates: list[str], series_map: dict[str, list]) -> pd.DataFrame:
    """將新到舊的趨勢序列轉成可畫 line_chart 的 DataFrame（反轉為舊→新、以週別為索引）。"""

    lengths = [len(values) for values in series_map.values() if values]
    point_count = min(lengths) if lengths else 0
    if point_count == 0:
        return pd.DataFrame()

    index = dates[:point_count] if len(dates) >= point_count else [str(i) for i in range(point_count)]
    data: dict[str, list] = {}
    for name, values in series_map.items():
        column = values[:point_count]
        if len(column) == point_count and all(value is not None for value in column):
            data[name] = column
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data, index=index).iloc[::-1]


def _build_cache_settings(enabled: bool) -> CacheSettings:
    """建立 Streamlit 執行使用的快取設定（委派給 config.make_cache_settings）。"""

    return make_cache_settings(enabled=enabled)


st.markdown(
    """
    <div class="hero-card">
      <div class="hero-title">台股自動選股儀表板</div>
      <p class="hero-subtitle">
        單一線上 API 管線：分級趨勢、集保總戶數下降、價格條件一次篩選，並輸出 0–100 評分、排序、Excel 與回測驗證。
      </p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.info("回測功能在左側欄。先勾選「啟用回測驗證」，再選日期，最後按「開始回測驗證」。")


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
        st.markdown("**集保總戶數下降（線上 TDCC 合計列）**")
        require_holder_decrease = st.checkbox(
            "要求集保總戶數下降",
            value=DEFAULT_SCREEN_PARAMETERS.require_holder_decrease,
            help="勾選＝硬性過濾；取消＝不過濾但仍計入評分",
        )
        holder_decrease_weeks = st.slider(
            "集保戶數比較週距", min_value=1, max_value=8, value=DEFAULT_SCREEN_PARAMETERS.holder_decrease_weeks,
            help="比較最新一週與 N 週前的集保總戶數",
        )
        min_holder_decrease_pct = st.slider(
            "集保戶數最小下降比例 (%)", min_value=0.0, max_value=15.0,
            value=DEFAULT_SCREEN_PARAMETERS.min_holder_decrease_ratio * 100, step=0.5,
            help="0＝只要下降即可；3＝需下降逾 3%",
        )
        st.markdown("---")
        enable_backtest = st.checkbox("啟用回測驗證", value=False, help="用歷史日期驗證當天選出的股票，觀察 1 個月與 3 個月後報酬")
        backtest_date = None
        if enable_backtest:
            st.markdown("**回測驗證**")
            st.caption("選擇一個過去的日期，以該日的籌碼資料篩選股票，並計算之後 1 個月、3 個月的漲跌幅。")
            backtest_date = st.date_input(
                "回測日期",
                value=date.today() - timedelta(days=90),
                min_value=date.today() - timedelta(days=365 * 3),
                max_value=date.today() - timedelta(days=30),
                format="YYYY-MM-DD",
                help="以該日歷史資料執行篩選，並自動計算後續 30/90 天報酬率",
            )
        else:
            st.caption("若要看策略回測，請先勾選上方的「啟用回測驗證」。")
        enable_cache = st.checkbox("啟用磁碟快取", value=True)
        price_workers = st.slider(
            "抓價併發數", min_value=1, max_value=12, value=DEFAULT_HTTP_SETTINGS.price_fetch_workers,
            help="1＝純序列；>1 會先序列跑籌碼關、再併發抓價，明顯加速全市場掃描",
        )
        log_level = st.selectbox("Log 等級", options=["INFO", "DEBUG"], index=0)
        submitted = st.form_submit_button("開始回測驗證" if enable_backtest else "開始篩選", use_container_width=True)


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
            require_holder_decrease=require_holder_decrease,
            holder_decrease_weeks=int(holder_decrease_weeks),
            min_holder_decrease_ratio=min_holder_decrease_pct / 100.0,
        )
        cache_settings = _build_cache_settings(enable_cache)
        actual_stock_limit = int(stock_limit) if stock_limit_enabled else None

        try:
            with st.spinner("正在抓取資料並執行篩選，這可能需要一些時間..."):
                results, summary, frames = execute_screening(
                    screen_parameters=screen_parameters,
                    logger=logger,
                    stock_limit=actual_stock_limit,
                    markets=tuple(selected_markets),
                    cache_settings=cache_settings,
                    start_date=backtest_date,
                    price_fetch_workers=int(price_workers),
                )
                excel_bytes = frames_to_excel_bytes(frames)
                download_name = resolve_output_path("").name
        except AccessBlockedError:
            st.session_state.pop("screening_run", None)
            st.error("官方資料站台拒絕了這台伺服器的請求（很可能是雲端／資料中心 IP 被 TWSE／TPEX／TDCC 阻擋）。")
            st.info(
                "這不是程式錯誤，而是來源端的 IP 封鎖。可行做法：\n"
                "1. 在本機執行（家用／公司 IP 通常正常）\n"
                "2. 於 Streamlit Cloud 設定 `HTTPS_PROXY`／`HTTP_PROXY` 環境變數，改由未被封鎖的出口 IP 連線\n"
                "3. 或自行部署到出口 IP 未被封鎖的環境"
            )
        except ApiClientError as exc:
            st.session_state.pop("screening_run", None)
            st.error(f"官方資料來源暫時回應異常：{exc}")
            st.info("系統已避免整頁直接崩潰。你可以稍後重試，或先限制股票數量縮小範圍。")
        else:
            st.session_state["screening_run"] = {
                "summary": summary,
                "frames": frames,
                "excel_bytes": excel_bytes,
                "download_name": download_name,
                "pass_trends": _collect_pass_trends(results),
            }


run_result = st.session_state.get("screening_run")
if run_result:
    summary = run_result["summary"]
    frames = run_result["frames"]
    excel_bytes = run_result["excel_bytes"]
    pass_trends = run_result.get("pass_trends", {})

    pass_frame = frames["pass"]
    pass_scores = (
        pd.to_numeric(pass_frame["選股分數"], errors="coerce").dropna()
        if (not pass_frame.empty and "選股分數" in pass_frame.columns)
        else pd.Series(dtype=float)
    )
    top_score = int(pass_scores.max()) if not pass_scores.empty else 0
    avg_score = pass_scores.mean() if not pass_scores.empty else 0

    metric_columns = st.columns(5)
    metric_columns[0].metric("分析檔數", summary.processed_count)
    metric_columns[1].metric("篩選通過", summary.passed_count)
    metric_columns[2].metric("最高分", top_score)
    metric_columns[3].metric("平均分", f"{avg_score:.1f}" if summary.passed_count else "—")
    metric_columns[4].metric("耗時秒數", f"{summary.elapsed_seconds:.2f}")
    st.markdown('<div class="metric-note">結果可直接下載 Excel；本版不再自動寫檔到伺服器磁碟。</div>', unsafe_allow_html=True)

    st.download_button(
        label="下載 Excel 報表",
        data=excel_bytes,
        file_name=run_result["download_name"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    summary_lines = "".join(f"<li>{line}</li>" for line in build_summary_text(summary))
    st.markdown(f'<div class="summary-panel"><strong>執行摘要</strong><ul>{summary_lines}</ul></div>', unsafe_allow_html=True)

    has_backtest = summary.backtest_anchor_date is not None
    tab_labels = ["通過清單", "未通過清單", "原始摘要"]
    if has_backtest:
        tab_labels.append("回測績效驗證")
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        st.dataframe(frames["pass"], use_container_width=True, height=420)
    with tabs[1]:
        st.dataframe(frames["fail"], use_container_width=True, height=420)
    with tabs[2]:
        st.dataframe(frames["raw_data_summary"], use_container_width=True, height=520)

    if has_backtest:
        with tabs[3]:
            bt_frame = frames["backtest"]
            anchor_date_str = summary.backtest_anchor_date.isoformat() if summary.backtest_anchor_date else "?"
            tdcc_anchor_date = summary.backtest_tdcc_anchor_date
            tdcc_anchor_str = tdcc_anchor_date.isoformat() if tdcc_anchor_date else "?"
            st.markdown(f"**篩選日期：{anchor_date_str}**　**TDCC 對齊週別：{tdcc_anchor_str}**　共 {len(bt_frame)} 檔通過篩選")
            if tdcc_anchor_date is not None and tdcc_anchor_date != summary.backtest_anchor_date:
                st.caption("已使用小於等於指定日期的最近 TDCC 週資料做籌碼篩選；價格條件與後續報酬仍以你選的日期為準。")
            st.caption("⚠️ 本回測會先依上市/上櫃日期排除當時尚未可交易股票，但仍以今日可取得的公司清單為基底；已下市或代號變更股票可能無法抓到歷史資料。")

            if bt_frame.empty:
                st.info("這個回測日期目前沒有符合條件的股票，或價格資料不足，因此暫無可計算的後續報酬。")
            else:
                col1m, col3m, col1m_wr, col3m_wr = st.columns(4)
                returns_1m = bt_frame["1個月後報酬率"].dropna()
                returns_3m = bt_frame["3個月後報酬率"].dropna()
                avg_1m = returns_1m.mean() if len(returns_1m) > 0 else None
                avg_3m = returns_3m.mean() if len(returns_3m) > 0 else None
                wr_1m = (returns_1m > 0).sum() / len(returns_1m) if len(returns_1m) > 0 else None
                wr_3m = (returns_3m > 0).sum() / len(returns_3m) if len(returns_3m) > 0 else None

                col1m.metric("1個月後平均報酬", format_ratio_as_pct(avg_1m) if avg_1m is not None else "N/A")
                col3m.metric("3個月後平均報酬", format_ratio_as_pct(avg_3m) if avg_3m is not None else "N/A")
                col1m_wr.metric("1個月後勝率", f"{wr_1m:.0%}" if wr_1m is not None else "N/A")
                col3m_wr.metric("3個月後勝率", f"{wr_3m:.0%}" if wr_3m is not None else "N/A")

                display_bt = bt_frame.copy()
                display_bt["1個月後報酬"] = display_bt["1個月後報酬率"].apply(format_ratio_as_pct)
                display_bt["3個月後報酬"] = display_bt["3個月後報酬率"].apply(format_ratio_as_pct)
                display_bt = display_bt.drop(columns=["1個月後報酬率", "3個月後報酬率"])
                st.dataframe(display_bt, use_container_width=True, height=420)

    if pass_trends:
        st.divider()
        st.subheader("📉 個股籌碼趨勢（線上 TDCC 資料，新到舊）")
        selected_label = st.selectbox("選擇通過的股票查看趨勢", options=list(pass_trends.keys()))
        trend = pass_trends[selected_label]
        st.caption(f"{trend['score_label']}　評級分數 {trend['score']}")
        dates = trend["dates"]
        small_df = _trend_to_frame(dates, {"小於10張人數": trend["small"], "400~800張人數": trend["mid"], "大於1000張人數": trend["large"]})
        total_df = _trend_to_frame(dates, {"集保總戶數": trend["total"]})
        chart_cols = st.columns(2)
        with chart_cols[0]:
            st.markdown("**分級人數趨勢**")
            if small_df.empty:
                st.info("無分級趨勢資料")
            else:
                st.line_chart(small_df)
        with chart_cols[1]:
            st.markdown("**集保總戶數趨勢**")
            if total_df.empty:
                st.info("無集保總戶數資料")
            else:
                st.line_chart(total_df)
else:
    st.info("在左側設定條件後按下「開始篩選」，即可產生 CLI 同步邏輯的結果與 Excel。")