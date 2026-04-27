#!/usr/bin/env python3
# ==========================================
# generate_sample_data.py - 產生測試範例資料
# 執行此腳本將在 sample_data/ 目錄建立三個 CSV 測試檔案
# ==========================================
"""
範例資料設計說明：

股票清單：
- 2330  台積電  ← 應通過三關（5天買超，逐日增加，集保下降3%）
- 2317  鴻海    ← 應通過三關（4天買超，逐日增加，集保下降4%）
- 2454  聯發科  ← 應通過三關（4天買超，逐日增加，集保下降5%）
- 3711  日月光  ← 第三關失敗（集保戶數上升，應被過濾）

預期篩選結果（3標的通過）：
  排名 1：2330 台積電  95分
  排名 2：2454 聯發科  95分
  排名 3：2317 鴻海    90分
  3711 日月光 → 被集保戶數條件過濾

執行方式：
  python generate_sample_data.py
"""

import os
import pandas as pd
import numpy as np

# 輸出目錄
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sample_data')
os.makedirs(OUTPUT_DIR, exist_ok=True)


def generate_broker_trading():
    """
    產生券商分點買賣超範例資料。

    交易日：2026-04-17（週四）、2026-04-21（週一）～ 2026-04-25（週五）
    """
    rows = []

    # ── 2330 台積電 ／ 元大台北 ──────────────────────────────────────
    # 連續買超 5 天（Apr 21~25），買超張數逐日增加
    broker_2330 = [
        ('2026-04-17', '2330', '台積電', '元大證券', '元大台北', 80, 60, 20, 840.0),
        ('2026-04-21', '2330', '台積電', '元大證券', '元大台北', 120, 30, 90, 845.0),
        ('2026-04-22', '2330', '台積電', '元大證券', '元大台北', 140, 30, 110, 847.0),
        ('2026-04-23', '2330', '台積電', '元大證券', '元大台北', 160, 30, 130, 849.0),
        ('2026-04-24', '2330', '台積電', '元大證券', '元大台北', 180, 30, 150, 851.0),
        ('2026-04-25', '2330', '台積電', '元大證券', '元大台北', 200, 40, 160, 853.0),
    ]
    rows.extend(broker_2330)

    # 2330 另一個分點（國泰台北）只有 2 天，應被過濾（天數不足）
    broker_2330_b = [
        ('2026-04-24', '2330', '台積電', '國泰證券', '國泰台北', 50, 30, 20, 851.0),
        ('2026-04-25', '2330', '台積電', '國泰證券', '國泰台北', 60, 30, 30, 854.0),
    ]
    rows.extend(broker_2330_b)

    # ── 2317 鴻海 ／ 凱基信義 ────────────────────────────────────────
    # Apr 21 賣超（中斷），Apr 22~25 連續買超 4 天，買超張數逐日增加
    broker_2317 = [
        ('2026-04-21', '2317', '鴻海',   '凱基證券', '凱基信義', 50, 100, -50, 166.0),
        ('2026-04-22', '2317', '鴻海',   '凱基證券', '凱基信義', 180, 40, 140, 167.0),
        ('2026-04-23', '2317', '鴻海',   '凱基證券', '凱基信義', 200, 40, 160, 168.0),
        ('2026-04-24', '2317', '鴻海',   '凱基證券', '凱基信義', 220, 40, 180, 169.0),
        ('2026-04-25', '2317', '鴻海',   '凱基證券', '凱基信義', 250, 50, 200, 170.0),
    ]
    rows.extend(broker_2317)

    # ── 2454 聯發科 ／ 富邦南京 ──────────────────────────────────────
    # Apr 21 賣超（中斷），Apr 22~25 連續買超 4 天，買超張數逐日增加
    broker_2454 = [
        ('2026-04-21', '2454', '聯發科', '富邦證券', '富邦南京', 20,  50,  -30, 1085.0),
        ('2026-04-22', '2454', '聯發科', '富邦證券', '富邦南京', 70,  40,   30, 1090.0),
        ('2026-04-23', '2454', '聯發科', '富邦證券', '富邦南京', 80,  20,   60, 1098.0),
        ('2026-04-24', '2454', '聯發科', '富邦證券', '富邦南京', 90,  20,   70, 1105.0),
        ('2026-04-25', '2454', '聯發科', '富邦證券', '富邦南京', 100, 25,   75, 1110.0),
    ]
    rows.extend(broker_2454)

    # ── 3711 日月光 ／ 國泰敦南 ──────────────────────────────────────
    # Apr 21~23 連續買超 3 天，但集保戶數上升，第三關應被過濾
    broker_3711 = [
        ('2026-04-21', '3711', '日月光投控', '國泰證券', '國泰敦南', 150, 40,  110, 93.0),
        ('2026-04-22', '3711', '日月光投控', '國泰證券', '國泰敦南', 170, 40,  130, 95.0),
        ('2026-04-23', '3711', '日月光投控', '國泰證券', '國泰敦南', 200, 50,  150, 96.0),
        ('2026-04-24', '3711', '日月光投控', '國泰證券', '國泰敦南', 30,  60,  -30, 97.0),
        ('2026-04-25', '3711', '日月光投控', '國泰證券', '國泰敦南', 40,  70,  -30, 98.0),
    ]
    rows.extend(broker_3711)

    df = pd.DataFrame(rows, columns=[
        'date', 'stock_id', 'stock_name', 'broker', 'branch',
        'buy_volume', 'sell_volume', 'net_buy', 'buy_avg_price'
    ])

    return df


def generate_price_data():
    """
    產生股價範例資料（10 個交易日）。

    最新收盤價設計（讓現價偏離率都在 5% 以內）：
    - 2330：最新收盤 855，主力成本 849.56 → 偏離 +0.64%
    - 2317：最新收盤 172，主力成本 168.65 → 偏離 +1.99%
    - 2454：最新收盤 1110，主力成本 1102.89 → 偏離 +0.64%
    - 3711：最新收盤 99，主力成本 94.82 → 偏離 +4.41%（集保關會先過濾）
    """

    def make_prices(stock_id, stock_name, base, vol_base, dates):
        """以隨機漫步模擬價格，確保最後一天收盤為指定值"""
        rng = np.random.default_rng(int(stock_id))
        prices = []
        close = base * 0.97  # 從稍低處開始

        for i, date in enumerate(dates):
            if i == len(dates) - 1:
                close = base  # 最後一天固定為目標收盤價
            else:
                change = rng.uniform(-0.005, 0.007)
                close = round(close * (1 + change), 1)

            high = round(close * rng.uniform(1.001, 1.015), 1)
            low = round(close * rng.uniform(0.985, 0.999), 1)
            open_p = round(rng.uniform(low, high), 1)
            volume = int(vol_base * rng.uniform(0.6, 1.5))

            prices.append((date, stock_id, stock_name, open_p, high, low, close, volume))
        return prices

    trading_dates = [
        '2026-04-14', '2026-04-15', '2026-04-16', '2026-04-17',
        '2026-04-21', '2026-04-22', '2026-04-23', '2026-04-24', '2026-04-25',
    ]

    rows = []
    rows.extend(make_prices('2330', '台積電',    855.0,  25000, trading_dates))
    rows.extend(make_prices('2317', '鴻海',       172.0, 100000, trading_dates))
    rows.extend(make_prices('2454', '聯發科',    1110.0,   8000, trading_dates))
    rows.extend(make_prices('3711', '日月光投控',  99.0,  30000, trading_dates))

    df = pd.DataFrame(rows, columns=[
        'date', 'stock_id', 'stock_name', 'open', 'high', 'low', 'close', 'volume'
    ])

    return df


def generate_holder_data():
    """
    產生集保戶數範例資料（5 個週別，以便有 4 週的比較區間）。

    集保戶數設計（越新越少代表籌碼集中）：
    - 2330：200000 → 194000（-3.0%）✓ 符合條件
    - 2317：350000 → 336000（-4.0%）✓ 符合條件
    - 2454：150000 → 142500（-5.0%）✓ 符合條件
    - 3711：80000  → 83000（+3.75%）✗ 集保上升，第三關過濾
    """
    week_dates = [
        '2026-03-28',  # 5 週前（index 0）
        '2026-04-04',  # 4 週前（index 1）← observation_weeks=4 時作為基準
        '2026-04-11',  # 3 週前
        '2026-04-18',  # 2 週前
        '2026-04-25',  # 最新（index 4）
    ]

    # 每週集保戶數（格式：stock_id, [w0, w1, w2, w3, w4]）
    holder_config = {
        ('2330', '台積電'):       [200000, 199200, 197800, 196000, 194000],
        ('2317', '鴻海'):         [350000, 347000, 343500, 339000, 336000],
        ('2454', '聯發科'):       [150000, 148500, 146000, 144000, 142500],
        ('3711', '日月光投控'):   [ 80000,  80500,  81200,  82000,  83000],
    }

    rows = []
    for (stock_id, stock_name), counts in holder_config.items():
        for date, count in zip(week_dates, counts):
            rows.append((date, stock_id, stock_name, count))

    df = pd.DataFrame(rows, columns=['week_date', 'stock_id', 'stock_name', 'holder_count'])

    return df


if __name__ == '__main__':
    print('正在產生範例資料...')

    # ── 券商分點買賣超 ──
    broker_df = generate_broker_trading()
    broker_path = os.path.join(OUTPUT_DIR, 'broker_trading.csv')
    broker_df.to_csv(broker_path, index=False, encoding='utf-8-sig')
    print(f'✅ 已產生：{broker_path}（{len(broker_df)} 筆）')

    # ── 股價資料 ──
    price_df = generate_price_data()
    price_path = os.path.join(OUTPUT_DIR, 'price_data.csv')
    price_df.to_csv(price_path, index=False, encoding='utf-8-sig')
    print(f'✅ 已產生：{price_path}（{len(price_df)} 筆）')

    # ── 集保戶數 ──
    holder_df = generate_holder_data()
    holder_path = os.path.join(OUTPUT_DIR, 'holder_data.csv')
    holder_df.to_csv(holder_path, index=False, encoding='utf-8-sig')
    print(f'✅ 已產生：{holder_path}（{len(holder_df)} 筆）')

    print('\n📁 所有範例檔案已儲存至 sample_data/ 目錄')
    print('\n預期篩選結果（使用預設參數執行後）：')
    print('  ✅ 2330 台積電  ～ 95 分（5天買超，逐日增加，集保 -3%）')
    print('  ✅ 2454 聯發科  ～ 95 分（4天買超，逐日增加，集保 -5%）')
    print('  ✅ 2317 鴻海    ～ 90 分（4天買超，逐日增加，集保 -4%）')
    print('  ❌ 3711 日月光  → 集保戶數上升 +3.75%，第三關過濾')
    print('  ❌ 2330 國泰台北 → 連續天數僅 2 天，第一關過濾')
