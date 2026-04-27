# 台股籌碼選股系統

依據「**券商分點連續買超 ＋ 主力平均成本 ＋ 集保戶數下降**」三關條件，自動篩選台股中可能有主力穩定吸籌的股票。

---

## 功能特色

- ✅ 三關嚴格篩選：連續買超、成本接近、籌碼集中
- 📊 100 分評級機制
- 📈 互動式圖表（K線、分點買超、集保戶數）
- 📥 一鍵匯出 Excel 報表（四個工作表）
- ⚙️ 參數可自由調整

---

## 安裝與執行

### 1. 安裝相依套件

```bash
cd stock_chip_selector
pip install -r requirements.txt
```

### 2. 產生測試範例資料（第一次使用請先執行）

```bash
python generate_sample_data.py
```

執行後將在 `sample_data/` 目錄產生三個 CSV 測試檔案。

### 3. 啟動 Streamlit 介面

```bash
streamlit run app.py
```

瀏覽器將自動開啟 `http://localhost:8501`

---

## CSV 檔案格式

### ① broker_trading.csv（券商分點買賣超）

| 欄位 | 說明 | 備註 |
|------|------|------|
| `date` | 交易日期 | 格式：`YYYY-MM-DD` |
| `stock_id` | 股票代號 | 字串，如 `2330` |
| `stock_name` | 股票名稱 | 如 `台積電` |
| `broker` | 券商名稱 | 如 `元大證券` |
| `branch` | 分點名稱 | 如 `元大台北` |
| `buy_volume` | 買進張數 | 整數 |
| `sell_volume` | 賣出張數 | 整數 |
| `net_buy` | 買超張數 | `buy_volume - sell_volume` |
| `buy_avg_price` | 買進均價 | **可留空**，系統自動以收盤價補充 |

範例：
```csv
date,stock_id,stock_name,broker,branch,buy_volume,sell_volume,net_buy,buy_avg_price
2026-04-21,2330,台積電,元大證券,元大台北,120,30,90,845.0
2026-04-22,2330,台積電,元大證券,元大台北,140,30,110,847.0
```

### ② price_data.csv（股價資料）

| 欄位 | 說明 | 備註 |
|------|------|------|
| `date` | 交易日期 | 格式：`YYYY-MM-DD` |
| `stock_id` | 股票代號 | |
| `stock_name` | 股票名稱 | |
| `open` | 開盤價 | |
| `high` | 最高價 | |
| `low` | 最低價 | |
| `close` | 收盤價 | |
| `volume` | 成交量（張） | |

### ③ holder_data.csv（集保戶數）

| 欄位 | 說明 | 備註 |
|------|------|------|
| `week_date` | 週別日期 | 通常為每週五，格式：`YYYY-MM-DD` |
| `stock_id` | 股票代號 | |
| `stock_name` | 股票名稱 | |
| `holder_count` | 集保戶數（戶） | |

---

## 選股邏輯說明

### 三關篩選條件

```
第一關：同一分點連續買超 ≥ N 天（預設 3 天）
         ↓ 通過
第二關：最新收盤價不超過主力平均成本 5% 以上
         ↓ 通過
第三關：近四週集保戶數下降（籌碼集中）
         ↓ 通過
        列入口袋名單
```

### 主力平均成本計算

$$\text{平均成本} = \frac{\sum(\text{買進均價} \times \text{買超張數})}{\sum(\text{買超張數})}$$

若無買進均價，以當日收盤價代入。

### 現價偏離率

$$\text{偏離率(\%)} = \frac{\text{最新收盤} - \text{平均成本}}{\text{平均成本}} \times 100$$

### 集保戶數變化率

$$\text{變化率(\%)} = \frac{\text{最新戶數} - \text{N週前戶數}}{\text{N週前戶數}} \times 100$$

---

## 評分機制（滿分 100 分）

| 項目 | 滿分 | 評分標準 |
|------|------|----------|
| 連續買超天數 | 30 | 3天=20分，4天=25分，5天以上=30分 |
| 買超趨勢 | 20 | 逐日增加=20分，大多增加=15分，僅連續=10分 |
| 現價接近成本 | 25 | 0–3%=25分，3–5%=20分，低於成本=15分 |
| 集保下降 | 25 | 降5%以上=25分，降3–5%=20分，降0–3%=15分 |

---

## 如何修改篩選參數

### 方式一：透過 Streamlit 介面調整

在側邊欄直接拖動或輸入數值，點擊「開始篩選」重新計算。

### 方式二：修改預設值

編輯 `config.py` 中的 `DEFAULT_CONFIG` 字典：

```python
DEFAULT_CONFIG = {
    "min_consecutive_days": 3,      # 最小連續買超天數
    "max_price_deviation_pct": 5.0, # 現價上限（%）
    "holder_observation_weeks": 4,  # 集保觀察週數
    "min_holder_decrease_pct": 0.0, # 集保最小下降（%）
    "strict_trend_mode": False,     # 嚴格模式
    "min_volume": 100,              # 最低成交量
    "market_scope": "all",          # 市場範圍
}
```

---

## 如何匯出 Excel

1. 執行篩選後，在結果頁面點擊「**⬇️ 下載 Excel 報表**」按鈕
2. Excel 包含四個工作表：
   - **選股結果總表**：所有符合條件的股票，依分數排序，高分列以黃色標示
   - **分點買超明細**：每筆 streak 的每日買超記錄
   - **集保戶數變化**：近 N 週集保戶數歷史
   - **參數設定紀錄**：本次使用的篩選參數（供回測比對）

---

## 程式架構

```
stock_chip_selector/
├── app.py                  # Streamlit 主程式（UI 與主流程）
├── config.py               # 預設參數與常數
├── data_loader.py          # CSV 資料載入、欄位清理、API TODO
├── broker_analyzer.py      # 連續買超 streak 分析
├── cost_calculator.py      # 主力平均成本、偏離率計算
├── holder_analyzer.py      # 集保戶數變化分析
├── scoring.py              # 100 分評級機制
├── report_exporter.py      # Excel 報表產生（openpyxl）
├── charts.py               # Plotly 互動圖表
├── generate_sample_data.py # 測試範例資料產生器
├── requirements.txt
├── README.md
└── sample_data/            # 執行 generate_sample_data.py 後產生
    ├── broker_trading.csv
    ├── price_data.csv
    └── holder_data.csv
```

---

## 未來擴充（API 模式）

`data_loader.py` 中已預留以下 TODO 介面：

- `fetch_broker_trading_from_api()` - 可串接富果 API、永豐金 API
- `fetch_price_from_twse()` - 可串接台灣證券交易所
- `fetch_holder_from_tdcc()` - 可串接台灣集保公司

待補充真實 API 後，取消 TODO 註解即可切換至線上資料來源。
