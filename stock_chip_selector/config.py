# ==========================================
# config.py - 預設參數與常數設定
# ==========================================

# 預設篩選參數
DEFAULT_CONFIG = {
    # 最小連續買超天數（同一分點連續正買超）
    "min_consecutive_days": 3,
    # 現價高於主力平均成本的上限（百分比），超過此值則過濾
    "max_price_deviation_pct": 5.0,
    # 集保戶數觀察週數（最新一週 vs. N週前）
    "holder_observation_weeks": 4,
    # 集保戶數最小下降比例（百分比），0=只要有下降即可
    "min_holder_decrease_pct": 0.0,
    # 買超趨勢模式：True=嚴格（逐日增加），False=寬鬆（連續買超即可）
    "strict_trend_mode": False,
    # 最低平均成交量（張），避免選到流動性不足的股票
    "min_volume": 100,
    # 股票市場範圍：all=全部，listed=上市，otc=上櫃
    "market_scope": "all",
}

# ── 資料欄位名稱定義（統一規範） ──

# 券商分點買賣超資料欄位
BROKER_COLUMNS = [
    "date",          # 日期
    "stock_id",      # 股票代號
    "stock_name",    # 股票名稱
    "broker",        # 券商名稱
    "branch",        # 分點名稱
    "buy_volume",    # 買進張數
    "sell_volume",   # 賣出張數
    "net_buy",       # 買超張數（buy_volume - sell_volume）
    "buy_avg_price", # 買進均價（可缺失，將由收盤價補充）
]

# 股價資料欄位
PRICE_COLUMNS = [
    "date",       # 日期
    "stock_id",   # 股票代號
    "stock_name", # 股票名稱
    "open",       # 開盤價
    "high",       # 最高價
    "low",        # 最低價
    "close",      # 收盤價
    "volume",     # 成交量（張）
]

# 集保戶數資料欄位
HOLDER_COLUMNS = [
    "week_date",    # 週別日期
    "stock_id",     # 股票代號
    "stock_name",   # 股票名稱
    "holder_count", # 集保戶數
]

# ── 評分門檻常數 ──
SCORE_STREAK_DAYS = {
    "min3": 20,   # 3天：20分
    "min4": 25,   # 4天：25分
    "min5": 30,   # 5天以上：30分
}

SCORE_TREND = {
    "逐日增加": 20,
    "大多增加": 15,
    "僅連續買超": 10,
}

SCORE_PRICE_DEV = {
    "low3": 25,    # 0%~3%：25分
    "low5": 20,    # 3%~5%：20分
    "below": 15,   # 低於成本：15分
}

SCORE_HOLDER = {
    "drop5": 25,   # 下降5%以上：25分
    "drop3": 20,   # 下降3%~5%：20分
    "drop0": 15,   # 下降0%~3%：15分
}
