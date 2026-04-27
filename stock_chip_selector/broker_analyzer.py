# ==========================================
# broker_analyzer.py - 券商分點連續買超分析
# ==========================================

import pandas as pd
import numpy as np
from typing import List, Dict, Any, Tuple


def find_consecutive_buying_streaks(
    broker_df: pd.DataFrame,
    min_days: int = 3,
    strict_mode: bool = False
) -> Tuple[pd.DataFrame, List[Dict[str, Any]]]:
    """
    找出每一檔股票、每一券商分點的連續買超記錄。

    核心邏輯：
    - 只計算 net_buy > 0 的連續天數
    - 中間有一天 net_buy <= 0 即中斷，重新計算
    - 若同一 stock_id + branch 有多段連續買超，取最近一段

    參數:
        broker_df: 已合併 effective_buy_price 的券商分點資料
        min_days: 最小連續買超天數（預設 3）
        strict_mode: True=嚴格（買超張數逐日增加），False=寬鬆

    回傳:
        (summary_df, detailed_records)
        - summary_df: 每筆合格 streak 的摘要 DataFrame
        - detailed_records: list of dict（含 streak_data 欄，為 DataFrame）
    """
    all_records = []

    # 先建立每檔股票可觀察到的交易日序列，供後續判斷「中間缺一天」是否中斷。
    stock_trade_dates = {
        stock_id: sorted(group['date'].dropna().unique())
        for stock_id, group in broker_df.groupby('stock_id')
    }

    # 按股票代號與分點分組
    grouped = broker_df.groupby(['stock_id', 'branch'])

    for (stock_id, branch), group in grouped:
        try:
            # 依日期排序
            group = group.sort_values('date').reset_index(drop=True)

            # 找出所有連續買超區段
            streaks = _find_all_streaks(group, stock_trade_dates.get(stock_id, []))

            # 只保留最近一段有效的 streak（避免舊買超干擾判斷）
            valid_streaks = [s for s in streaks if len(s) >= min_days]
            if not valid_streaks:
                continue

            # 取最近的 streak（streak_end 最晚）
            latest_streak = max(
                valid_streaks,
                key=lambda s: pd.DataFrame(s)['date'].max()
            )
            streak_df = pd.DataFrame(latest_streak).reset_index(drop=True)

            # 判斷買超趨勢
            net_buy_list = streak_df['net_buy'].tolist()
            is_increasing = _check_strictly_increasing(net_buy_list)
            trend_type = _classify_trend(net_buy_list)

            # 嚴格模式下，非逐日增加則跳過
            if strict_mode and not is_increasing:
                continue

            record = {
                'stock_id': stock_id,
                'stock_name': streak_df['stock_name'].iloc[0] if 'stock_name' in streak_df.columns else '',
                'broker': streak_df['broker'].iloc[0] if 'broker' in streak_df.columns else '',
                'branch': branch,
                'streak_start': streak_df['date'].min(),
                'streak_end': streak_df['date'].max(),
                'streak_days': len(streak_df),
                'total_net_buy': int(streak_df['net_buy'].sum()),
                'is_increasing': is_increasing,
                'trend_type': trend_type,
                'streak_data': streak_df,  # 保留明細供成本計算使用
            }
            all_records.append(record)

        except Exception as e:
            # 單一 stock+branch 出錯不中斷整體分析
            print(f"[警告] 分析 {stock_id} / {branch} 時發生錯誤：{e}")
            continue

    if not all_records:
        return pd.DataFrame(), []

    # 組成摘要 DataFrame（排除 streak_data 欄）
    summary_df = pd.DataFrame([
        {k: v for k, v in r.items() if k != 'streak_data'}
        for r in all_records
    ])

    return summary_df, all_records


def _find_all_streaks(group: pd.DataFrame, stock_dates: List[pd.Timestamp]) -> List[List[dict]]:
    """
    在已排序的分組資料中，找出所有連續買超區段。
    規則：
    - net_buy <= 0 的記錄會中斷連續性，重新計算
    - 若該股票在某個交易日有資料，但此分點當天缺資料，也視為中斷
    """
    streaks: List[List[dict]] = []
    current: List[dict] = []

    if not stock_dates:
        return streaks

    branch_rows = group.drop_duplicates(subset=['date'], keep='last').set_index('date')

    for trade_date in stock_dates:
        if trade_date in branch_rows.index:
            row = branch_rows.loc[trade_date]
            if row['net_buy'] > 0:
                row_dict = row.to_dict()
                row_dict['date'] = trade_date
                current.append(row_dict)
                continue

        # 遇到缺資料或非正買超，儲存目前區段並重置
        if current:
            streaks.append(current)
            current = []

    # 別忘了最後一段未結束的區段
    if current:
        streaks.append(current)

    return streaks


def _check_strictly_increasing(net_buy_list: List[float]) -> bool:
    """
    判斷買超張數是否嚴格逐日遞增（每天都比前一天多）。
    """
    if len(net_buy_list) <= 1:
        return True

    for i in range(1, len(net_buy_list)):
        if net_buy_list[i] <= net_buy_list[i - 1]:
            return False
    return True


def _classify_trend(net_buy_list: List[float]) -> str:
    """
    分類買超趨勢。

    回傳值：
        '逐日增加'  - 每天都比前一天多
        '大多增加'  - 超過半數的轉換是增加的
        '僅連續買超' - 每天都有買超但沒有明顯增加趨勢
    """
    if len(net_buy_list) <= 1:
        return '逐日增加'

    total_transitions = len(net_buy_list) - 1
    increasing_count = sum(
        1 for i in range(1, len(net_buy_list))
        if net_buy_list[i] > net_buy_list[i - 1]
    )

    ratio = increasing_count / total_transitions

    if ratio == 1.0:
        return '逐日增加'
    elif ratio >= 0.5:
        return '大多增加'
    else:
        return '僅連續買超'
