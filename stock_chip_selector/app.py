# ==========================================
# app.py - Streamlit 主程式
# 台股籌碼選股系統 v1.0
# ==========================================

import os
import sys
import traceback
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st

# 確保模組路徑正確（無論從哪個目錄執行）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from broker_analyzer import find_consecutive_buying_streaks
from charts import create_broker_volume_chart, create_holder_chart, create_price_chart
from config import DEFAULT_CONFIG
from cost_calculator import (
    build_latest_close_lookup,
    calculate_average_cost,
    calculate_price_deviation,
    get_latest_close,
    get_price_deviation_label,
)
from data_loader import (
    filter_by_volume,
    load_broker_trading,
    load_holder_data,
    load_price_data,
    merge_price_to_broker,
)
from holder_analyzer import analyze_holder_change, build_holder_lookup, get_holder_history
from report_exporter import export_to_excel
from scoring import calculate_score, get_score_label

# ── 頁面設定 ──────────────────────────────────────────────────────────
st.set_page_config(
    page_title='台股籌碼選股系統',
    page_icon='📈',
    layout='wide',
    initial_sidebar_state='expanded',
)

# ── 全域 CSS 微調 ────────────────────────────────────────────────────
st.markdown("""
<style>
    .metric-card {
        background: #f0f4f8;
        border-radius: 8px;
        padding: 12px 16px;
        text-align: center;
    }
    .stDataFrame { font-size: 13px; }
    [data-testid="stSidebar"] { min-width: 300px; }
</style>
""", unsafe_allow_html=True)

# ── 標題 ─────────────────────────────────────────────────────────────
st.title('📈 台股籌碼選股系統')
st.caption('依據「券商分點連續買超 ＋ 主力平均成本 ＋ 集保戶數下降」三關篩選優質標的')

# ── Session State 初始化 ─────────────────────────────────────────────
for key in ['results_df', 'broker_detail_df', 'holder_history_df',
            'broker_df', 'price_df', 'holder_df', 'excel_bytes', 'params_used']:
    if key not in st.session_state:
        st.session_state[key] = None

# ═══════════════════════════════════════════════════════════════════
# 側邊欄：資料上傳 + 篩選參數
# ═══════════════════════════════════════════════════════════════════
with st.sidebar:
    st.header('📂 上傳資料檔案')

    broker_file = st.file_uploader(
        '① 券商分點買賣超（broker_trading.csv）',
        type=['csv'],
        help='欄位：date, stock_id, stock_name, broker, branch, buy_volume, sell_volume, net_buy, buy_avg_price'
    )

    price_file = st.file_uploader(
        '② 股價資料（price_data.csv）',
        type=['csv'],
        help='欄位：date, stock_id, stock_name, open, high, low, close, volume'
    )

    holder_file = st.file_uploader(
        '③ 集保戶數（holder_data.csv）',
        type=['csv'],
        help='欄位：week_date, stock_id, stock_name, holder_count'
    )

    # 顯示上傳狀態
    upload_status = {
        '券商分點': '✅' if broker_file else '⬜',
        '股價資料': '✅' if price_file else '⬜',
        '集保戶數': '✅' if holder_file else '⬜',
    }
    for name, status in upload_status.items():
        st.write(f'{status} {name}')

    st.divider()
    st.header('⚙️ 篩選參數')

    min_consecutive_days = st.number_input(
        '最小連續買超天數',
        min_value=2, max_value=20,
        value=DEFAULT_CONFIG['min_consecutive_days'],
        help='同一分點至少連續幾個交易日正買超'
    )

    max_price_deviation_pct = st.number_input(
        '現價高於主力成本上限 (%)',
        min_value=0.0, max_value=30.0, step=0.5,
        value=float(DEFAULT_CONFIG['max_price_deviation_pct']),
        help='現價超過主力均攤成本此比例以上的股票將被過濾'
    )

    holder_observation_weeks = st.number_input(
        '集保戶數觀察週數',
        min_value=2, max_value=12,
        value=DEFAULT_CONFIG['holder_observation_weeks'],
        help='比較「最新集保戶數」與「N週前集保戶數」的變化'
    )

    min_holder_decrease_pct = st.number_input(
        '集保戶數最小下降比例 (%)',
        min_value=0.0, max_value=20.0, step=0.5,
        value=float(DEFAULT_CONFIG['min_holder_decrease_pct']),
        help='0=只要有下降即可；設3=下降須超過3%才符合條件'
    )

    strict_trend_mode = st.checkbox(
        '嚴格模式：要求買超張數逐日遞增',
        value=DEFAULT_CONFIG['strict_trend_mode'],
        help='勾選=每天買超張數必須比前一天多（嚴格）'
    )

    min_volume = st.number_input(
        '最低日均成交量 (張)',
        min_value=0, max_value=100000, step=100,
        value=int(DEFAULT_CONFIG['min_volume']),
        help='過濾流動性不足的低交量股票'
    )

    market_scope = st.selectbox(
        '股票市場範圍',
        options=['all', 'listed', 'otc'],
        format_func=lambda x: {'all': '🌐 全部', 'listed': '🔵 上市（TWSE）', 'otc': '🟢 上櫃（OTC）'}[x],
        index=0,
        help='目前版本（CSV 模式）此設定僅供紀錄，實際過濾依上傳的資料決定'
    )

    st.divider()
    run_button = st.button('🚀 開始篩選', use_container_width=True, type='primary')

# ── 整合篩選參數 ──────────────────────────────────────────────────────
params = {
    'min_consecutive_days': min_consecutive_days,
    'max_price_deviation_pct': max_price_deviation_pct,
    'holder_observation_weeks': holder_observation_weeks,
    'min_holder_decrease_pct': min_holder_decrease_pct,
    'strict_trend_mode': strict_trend_mode,
    'min_volume': min_volume,
    'market_scope': market_scope,
}

# ═══════════════════════════════════════════════════════════════════
# 執行篩選邏輯
# ═══════════════════════════════════════════════════════════════════
if run_button:
    if not (broker_file and price_file and holder_file):
        st.error('⚠️ 請先上傳三個 CSV 檔案（券商分點、股價、集保戶數）再執行篩選！')
        st.stop()

    progress_bar = st.progress(0, text='載入資料中...')

    try:
        # ── 步驟 1：載入三份資料 ──────────────────────────────────────
        progress_bar.progress(10, text='載入券商分點資料...')
        broker_df = load_broker_trading(broker_file)

        progress_bar.progress(25, text='載入股價資料...')
        price_df = load_price_data(price_file)

        progress_bar.progress(40, text='載入集保戶數資料...')
        holder_df = load_holder_data(holder_file)

        # 儲存原始資料到 Session State（圖表使用）
        st.session_state['broker_df'] = broker_df
        st.session_state['price_df'] = price_df
        st.session_state['holder_df'] = holder_df

        latest_close_lookup = build_latest_close_lookup(price_df)
        holder_lookup = build_holder_lookup(holder_df)

        # ── 步驟 2：合併股價補充缺失均價 ─────────────────────────────
        progress_bar.progress(50, text='合併股價資料...')
        merged_broker_df = merge_price_to_broker(broker_df, price_df)

        # ── 步驟 3：成交量過濾 ────────────────────────────────────────
        progress_bar.progress(55, text='過濾低流動性股票...')
        if min_volume > 0:
            merged_broker_df = filter_by_volume(merged_broker_df, price_df, min_volume)

        if merged_broker_df.empty:
            st.warning('套用成交量過濾後無剩餘股票，請降低最低成交量門檻。')
            progress_bar.empty()
            st.stop()

        # ── 步驟 4：找出連續買超 streak ──────────────────────────────
        progress_bar.progress(65, text='分析連續買超...')
        summary_df, all_results = find_consecutive_buying_streaks(
            merged_broker_df,
            min_days=min_consecutive_days,
            strict_mode=strict_trend_mode,
        )

        if summary_df.empty:
            st.warning(
                f'未找到符合「連續買超 {min_consecutive_days} 天以上」條件的股票。\n\n'
                '建議：降低最小連續買超天數，或擴大篩選日期範圍。'
            )
            progress_bar.empty()
            st.stop()

        # ── 步驟 5：三關篩選與指標計算 ───────────────────────────────
        progress_bar.progress(75, text='計算主力成本與集保變化...')

        final_records = []
        broker_detail_rows = []
        holder_histories = {}

        for result in all_results:
            stock_id = result['stock_id']
            branch = result['branch']
            streak_df = result['streak_data']

            try:
                # ── 關卡 2：主力平均成本與現價偏離 ──────────────────
                avg_cost = calculate_average_cost(streak_df)
                latest_close = get_latest_close(price_df, stock_id) if stock_id not in latest_close_lookup else latest_close_lookup[stock_id]

                if not np.isnan(avg_cost) and not np.isnan(latest_close):
                    deviation_pct = calculate_price_deviation(avg_cost, latest_close)
                else:
                    deviation_pct = np.nan

                # 現價高於成本超過上限 → 過濾
                if not np.isnan(deviation_pct) and deviation_pct > max_price_deviation_pct:
                    continue

                # ── 關卡 3：集保戶數下降 ─────────────────────────────
                holder_info = analyze_holder_change(
                    holder_lookup,
                    stock_id,
                    observation_weeks=holder_observation_weeks,
                    min_decrease_pct=min_holder_decrease_pct,
                )

                if not holder_info['pass_filter']:
                    continue

                # ── 計算選股分數 ─────────────────────────────────────
                dev_for_score = deviation_pct if not np.isnan(deviation_pct) else 0.0
                chg_for_score = holder_info['change_rate'] if holder_info['change_rate'] is not None else 0.0

                score = calculate_score(
                    streak_days=result['streak_days'],
                    trend_type=result['trend_type'],
                    price_deviation_pct=dev_for_score,
                    holder_change_rate=chg_for_score,
                )

                # ── 彙總結果 ─────────────────────────────────────────
                record = {
                    'stock_id': stock_id,
                    'stock_name': result['stock_name'],
                    'broker': result['broker'],
                    'branch': branch,
                    'streak_days': result['streak_days'],
                    'streak_period': (
                        f"{result['streak_start'].strftime('%Y/%m/%d')}"
                        f"～{result['streak_end'].strftime('%Y/%m/%d')}"
                    ),
                    'total_net_buy': result['total_net_buy'],
                    'avg_cost': round(avg_cost, 2) if not np.isnan(avg_cost) else None,
                    'latest_close': latest_close if not np.isnan(latest_close) else None,
                    'price_deviation_pct': round(deviation_pct, 2) if not np.isnan(deviation_pct) else None,
                    'early_holder': holder_info.get('early_holder'),
                    'latest_holder': holder_info.get('latest_holder'),
                    'holder_change_rate': (
                        round(holder_info['change_rate'], 2)
                        if holder_info['change_rate'] is not None else None
                    ),
                    'is_increasing': '是' if result['is_increasing'] else '否',
                    'trend_type': result['trend_type'],
                    'score': score,
                    'label': get_score_label(score),
                    'price_label': get_price_deviation_label(deviation_pct),
                }
                final_records.append(record)

                # 收集買超明細（用於 Excel Sheet 2）
                broker_detail_rows.extend(streak_df.to_dict('records'))

                # 收集集保歷史（用於 Excel Sheet 3）
                if stock_id not in holder_histories:
                    history = get_holder_history(holder_lookup, stock_id, holder_observation_weeks)
                    if not history.empty:
                        holder_histories[stock_id] = history

            except Exception as e:
                st.warning(f'[{stock_id}/{branch}] 分析時發生錯誤，跳過：{e}')
                continue

        progress_bar.progress(90, text='整理結果中...')

        if not final_records:
            st.warning(
                '所有候選標的均未通過完整三關篩選。\n\n'
                '建議調整：提高現價上限 (%)、降低集保下降要求、或減少連續買超天數。'
            )
            progress_bar.empty()
            st.stop()

        # ── 組成結果 DataFrame ────────────────────────────────────────
        results_df = (
            pd.DataFrame(final_records)
            .sort_values('score', ascending=False)
            .reset_index(drop=True)
        )
        broker_detail_df = pd.DataFrame(broker_detail_rows) if broker_detail_rows else pd.DataFrame()
        holder_history_df = (
            pd.concat(holder_histories.values(), ignore_index=True)
            if holder_histories else pd.DataFrame()
        )

        # 預先產生 Excel（存入 Session State 供下載）
        excel_bytes = export_to_excel(results_df, broker_detail_df, holder_history_df, params)

        # 寫入 Session State
        st.session_state['results_df'] = results_df
        st.session_state['broker_detail_df'] = broker_detail_df
        st.session_state['holder_history_df'] = holder_history_df
        st.session_state['excel_bytes'] = excel_bytes
        st.session_state['params_used'] = params.copy()

        progress_bar.progress(100, text='篩選完成！')
        st.success(f'✅ 篩選完成！共找到 **{len(results_df)}** 個符合三關條件的標的。')

    except Exception as e:
        progress_bar.empty()
        st.error(f'❌ 執行過程發生嚴重錯誤：{e}')
        with st.expander('查看詳細錯誤訊息'):
            st.code(traceback.format_exc())
        st.stop()

# ═══════════════════════════════════════════════════════════════════
# 結果顯示區
# ═══════════════════════════════════════════════════════════════════
if st.session_state['results_df'] is not None:
    results_df = st.session_state['results_df']

    st.divider()

    # ── 統計摘要卡片 ─────────────────────────────────────────────────
    st.subheader('📊 篩選結果摘要')
    col1, col2, col3, col4, col5 = st.columns(5)

    col1.metric('✅ 符合標的數', len(results_df))
    col2.metric('🏆 最高分數', results_df['score'].max())
    col3.metric('📊 平均分數', f"{results_df['score'].mean():.1f}")

    avg_streak = results_df['streak_days'].mean()
    col4.metric('📅 平均連買天數', f"{avg_streak:.1f} 天")

    avg_holder_chg = results_df['holder_change_rate'].dropna().mean()
    col5.metric('👥 平均集保變化', f"{avg_holder_chg:.1f}%")

    # ── 結果表格 ─────────────────────────────────────────────────────
    st.subheader('📋 選股結果一覽（依分數排序）')

    display_col_map = {
        'stock_id': '股票代號',
        'stock_name': '股票名稱',
        'branch': '券商分點',
        'streak_days': '連買天數',
        'streak_period': '連買期間',
        'total_net_buy': '總買超(張)',
        'avg_cost': '主力成本',
        'latest_close': '最新收盤',
        'price_deviation_pct': '偏離率%',
        'early_holder': '早期戶數',
        'latest_holder': '最新戶數',
        'holder_change_rate': '戶數變化%',
        'is_increasing': '逐日增加',
        'trend_type': '買超趨勢',
        'score': '選股分數',
        'label': '評級',
    }

    available_cols = [c for c in display_col_map if c in results_df.columns]
    show_df = results_df[available_cols].rename(columns=display_col_map)

    st.dataframe(
        show_df,
        use_container_width=True,
        hide_index=True,
        height=400,
        column_config={
            '選股分數': st.column_config.ProgressColumn(
                '選股分數',
                min_value=0,
                max_value=100,
                format='%d 分',
                width='medium',
            ),
            '偏離率%': st.column_config.NumberColumn('偏離率%', format='%.2f%%'),
            '戶數變化%': st.column_config.NumberColumn('戶數變化%', format='%.2f%%'),
            '主力成本': st.column_config.NumberColumn('主力成本', format='%.2f'),
            '最新收盤': st.column_config.NumberColumn('最新收盤', format='%.2f'),
        }
    )

    # ── Excel 下載 ───────────────────────────────────────────────────
    st.subheader('📥 匯出 Excel 報表')

    dl_col1, dl_col2 = st.columns([1, 3])

    with dl_col1:
        if st.session_state['excel_bytes']:
            filename = f'籌碼選股_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            st.download_button(
                label='⬇️ 下載 Excel 報表',
                data=st.session_state['excel_bytes'],
                file_name=filename,
                mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                use_container_width=True,
            )

    with dl_col2:
        st.caption(
            '📌 Excel 包含四個工作表：① 選股結果總表 ② 分點買超明細 ③ 集保戶數變化 ④ 參數設定紀錄'
        )

    # ═══════════════════════════════════════════════════════════════
    # 圖表區：點選個股查看詳細圖表
    # ═══════════════════════════════════════════════════════════════
    st.divider()
    st.subheader('📉 個股圖表分析')

    # 建立選股選單
    if len(results_df) > 0:
        option_labels = results_df.apply(
            lambda r: f"[{r['score']}分] {r['stock_id']} {r['stock_name']} ／ {r['branch']}",
            axis=1
        ).tolist()

        selected_label = st.selectbox(
            '選擇股票 + 分點查看圖表',
            options=option_labels,
            help='選擇後可查看股價走勢、分點買超記錄、集保戶數變化三張圖表'
        )

        if selected_label:
            sel_idx = option_labels.index(selected_label)
            sel_row = results_df.iloc[sel_idx]
            sel_stock_id = sel_row['stock_id']
            sel_branch = sel_row['branch']
            sel_stock_name = sel_row.get('stock_name', '')

            price_df = st.session_state['price_df']
            broker_df = st.session_state['broker_df']
            holder_df = st.session_state['holder_df']

            if price_df is not None and broker_df is not None and holder_df is not None:
                tab1, tab2, tab3 = st.tabs(['📈 股價走勢', '📊 分點每日買超', '👥 集保戶數變化'])

                with tab1:
                    fig_price = create_price_chart(price_df, sel_stock_id, sel_stock_name)
                    st.plotly_chart(fig_price, use_container_width=True)

                with tab2:
                    fig_broker = create_broker_volume_chart(
                        broker_df, sel_stock_id, sel_branch, sel_stock_name
                    )
                    st.plotly_chart(fig_broker, use_container_width=True)

                    # 顯示選取分點的買超明細表
                    detail_data = broker_df[
                        (broker_df['stock_id'] == sel_stock_id) &
                        (broker_df['branch'] == sel_branch)
                    ].sort_values('date')[['date', 'buy_volume', 'sell_volume', 'net_buy', 'buy_avg_price']]
                    if not detail_data.empty:
                        st.dataframe(detail_data, use_container_width=True, hide_index=True)

                with tab3:
                    fig_holder = create_holder_chart(holder_df, sel_stock_id, sel_stock_name)
                    st.plotly_chart(fig_holder, use_container_width=True)

                # 選股分數明細
                with st.expander(f'📊 {sel_stock_id} {sel_stock_name} 評分明細'):
                    score_cols = st.columns(4)
                    streak_score = 30 if sel_row['streak_days'] >= 5 else (25 if sel_row['streak_days'] == 4 else 20)
                    trend_score = {'逐日增加': 20, '大多增加': 15, '僅連續買超': 10}.get(sel_row.get('trend_type', ''), 0)
                    dev_pct = sel_row.get('price_deviation_pct') or 0
                    price_score = 25 if dev_pct <= 3 else (20 if dev_pct <= 5 else (15 if dev_pct < 0 else 0))
                    chg = sel_row.get('holder_change_rate') or 0
                    holder_score = 25 if chg <= -5 else (20 if chg <= -3 else 15)

                    score_cols[0].metric('連續買超天數分', f'{streak_score}/30', f'{sel_row["streak_days"]}天')
                    score_cols[1].metric('買超趨勢分', f'{trend_score}/20', sel_row.get('trend_type', '-'))
                    score_cols[2].metric('成本接近分', f'{price_score}/25', f'{dev_pct:+.2f}%')
                    score_cols[3].metric('集保下降分', f'{holder_score}/25', f'{chg:.2f}%')

# ═══════════════════════════════════════════════════════════════════
# 歡迎畫面（尚未上傳資料時）
# ═══════════════════════════════════════════════════════════════════
else:
    st.info('''
### 🚀 使用步驟

1. 在左側側邊欄**上傳三個 CSV 資料檔案**
2. 依需求**調整篩選參數**（可使用預設值快速上手）
3. 點擊「**開始篩選**」執行三關選股
4. 查看結果表格，點選個股查看圖表
5. 點擊「**下載 Excel 報表**」匯出完整報告
''')

    st.subheader('📄 CSV 檔案格式說明')

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown('**① broker_trading.csv**')
        st.code(
            'date\nstock_id\nstock_name\nbroker\nbranch\n'
            'buy_volume\nsell_volume\nnet_buy\nbuy_avg_price',
            language='text'
        )
        st.caption('buy_avg_price 可留空，系統將自動以收盤價代替')

    with col2:
        st.markdown('**② price_data.csv**')
        st.code(
            'date\nstock_id\nstock_name\nopen\nhigh\nlow\nclose\nvolume',
            language='text'
        )
        st.caption('volume 單位為張（千股）')

    with col3:
        st.markdown('**③ holder_data.csv**')
        st.code(
            'week_date\nstock_id\nstock_name\nholder_count',
            language='text'
        )
        st.caption('week_date 為每週統計日期（通常為週五）')

    st.subheader('🔍 三關選股邏輯')
    st.markdown('''
| 關卡 | 條件 | 說明 |
|------|------|------|
| 第一關 | 同一分點連續買超 ≥ N 天 | 確認主力持續吸籌，非單日大買 |
| 第二關 | 現價不超過主力成本 5% | 確保追價空間充足 |
| 第三關 | 近四週集保戶數下降 | 籌碼集中，散戶出場 |
''')

    st.subheader('📊 評分說明（滿分 100 分）')
    st.markdown('''
| 項目 | 最高分 | 說明 |
|------|--------|------|
| 連續買超天數 | 30 | 5天以上30分，4天25分，3天20分 |
| 買超趨勢 | 20 | 逐日增加20分，大多增加15分，僅買超10分 |
| 現價接近主力成本 | 25 | 偏離 0–3% 得 25 分，3–5% 得 20 分，低於成本得 15 分 |
| 集保戶數下降 | 25 | 降 5% 以上得 25 分，降 3–5% 得 20 分，降 0–3% 得 15 分 |
''')
