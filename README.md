# 台股籌碼選股工具

這是一個可直接執行的台股自動選股專案，現在同時支援上市與上櫃，並提供兩個入口：

1. CLI：執行 python main.py
2. Streamlit：執行 streamlit run streamlit_app.py

> 註：本版已將原本獨立的「stock_chip_selector（CSV 上傳籌碼選股）」**融合進單一線上 API 管線**。
> 由於券商分點（連續買超／主力成本）沒有可批次的免費線上 API，融合時改用線上可得訊號替代其意圖：
> 以 TDCC「大戶（>1000 張）人數連續增加」替代主力吸籌、以「距 20MA」替代主力成本接近、
> 並新增「集保總戶數下降」條件與 0–100 評分制。原 CSV 版模組已退役（仍保留在 git 歷史中）。

資料來源全部來自公開 HTTP API 或官方查詢端點，不使用 CSV 或 Excel 當資料來源：

1. TWSE OpenAPI：上市股票清單與上市個股月日線
2. TPEX OpenAPI：上櫃股票基本資料
3. TPEX 官方 JSON 端點：上櫃個股歷史日成交資訊
4. TDCC OpenAPI：最新一週股權分散表
5. TDCC 官方查詢頁：歷史週別股權分散資料

## 專案檔案

1. config.py：集中管理 endpoint、欄位對映、預設市場、cache 設定、抓價併發數與評分門檻；並提供 make_screen_parameters／make_cache_settings 工廠供 CLI 與 Streamlit 共用
2. models.py：股票、股權分散快照、價格日線與摘要 dataclass
3. api_clients.py：TWSE、TPEX、TDCC client、SSL fallback、磁碟快取與跨執行緒節流；TDCC 歷史含「查無資料」負快取
4. screener.py：選股主流程、趨勢判斷、集保總戶數下降、價格條件、兩階段管線（籌碼關序列＋抓價關併發）與評分
5. reporting.py：將篩選結果與摘要轉成 pass／fail／raw_data_summary／backtest 等輸出 DataFrame
6. scoring.py：0–100 綜合評分與評級（門檻全讀自 config.py，單一真實來源）
7. utils.py：日期、數字、股票代號與 logging 工具
8. main.py：CLI 入口、共用執行函式、Excel 匯出（含公式注入防護）
9. streamlit_app.py：Streamlit 互動介面（評分排序、線上趨勢圖、Excel 下載）

## 安裝步驟

1. 建立虛擬環境

```bash
python3 -m venv .venv
source .venv/bin/activate
```

1. 安裝依賴

```bash
pip install -r requirements.txt
```

## 開發、測試與 CI

### 本地開發（快速開始）

1. 建議在乾淨的虛擬環境中執行：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

1. 執行語法檢查與測試：

```bash
# 安裝測試工具
pip install -U pytest

# 只檢查語法（不執行外部 API）
python -m py_compile $(find . -name '*.py' -not -path './.venv/*' -not -path './venv/*' -not -path './.git/*' -not -path './.github/*')

# 執行 repository 的基本測試
pytest -q
```

### GitHub Actions CI

已新增 GitHub Actions 工作流程 `.github/workflows/ci.yml`，內容會在 push / pull_request 上執行：

- 以 Ubuntu 最新映像建立 Python (3.10、3.11) 環境
- 安裝 `requirements.txt`（若存在）與 `pytest`
- 執行語法檢查（`py_compile`）
- 執行 `pytest`

工作流程位置：`.github/workflows/ci.yml`

若要在 README 上顯示 badge，可使用：

```text
![CI](https://github.com/MasonW0711/stock-select-strategy/actions/workflows/ci.yml/badge.svg)
```

## CLI 執行方式

1. 直接跑全市場預設條件

```bash
python3 main.py
```

1. 先做小樣本 smoke test

```bash
python3 main.py --stock-limit 5
```

1. 只跑上市

```bash
python3 main.py --markets listed
```

1. 調整條件並限制處理檔數

```bash
python3 main.py --weeks 4 --max-3m-return 0.35 --max-distance-to-ma 0.06 --stock-limit 30
```

1. 停用磁碟快取

```bash
python3 main.py --disable-cache
```

1. 調整抓價併發數（1＝純序列；>1 會先序列跑籌碼關、再併發抓價，明顯加速全市場掃描）

```bash
python3 main.py --price-workers 8
```

1. 指定 Excel 輸出檔名

```bash
python3 main.py --output ./result.xlsx
```

1. 指定歷史日期做單日回測驗證

```bash
python3 main.py --start-date 2026-04-09 --stock-limit 30
```

回測模式說明：

1. 籌碼條件會使用小於等於指定日期的最近 TDCC 週資料
2. 價格條件與 1 個月、3 個月後報酬，會以你指定的日期作為 anchor 計算
3. 會先依上市/上櫃日期排除回測日尚未可交易的股票，讓歷史股票集合更接近當時市場
4. 目前仍以當前可取得的股票清單為基底，已下市或代號變更股票可能缺漏

## Streamlit 執行方式

1. 啟動介面

```bash
streamlit run streamlit_app.py
```

1. 在左側調整市場、週數、價格條件、集保總戶數下降條件、是否啟用快取
2. 若要驗證歷史日期，勾選「啟用回測驗證」並選擇回測日期
3. 按下「開始篩選」或「開始回測驗證」
4. 畫面上方顯示分析檔數、通過數、最高分、平均分；下方分頁顯示通過清單（依分數排序）、失敗清單、raw_data_summary；回測模式會額外顯示 backtest 工作表與績效統計
5. 通過清單下方可選個股，檢視其分級人數與集保總戶數的線上趨勢圖
6. 回測結果會同時標示使用者指定日期與實際對齊的 TDCC 週別；Excel 以下載按鈕提供（不自動寫檔到伺服器）

## 選股邏輯（硬性條件，全部通過才入選）

1. 持股小於 10 張的人數，最近 N 週嚴格單調下降
2. 持股 400 到 800 張的人數，最近 N 週嚴格單調增加
3. 持股大於 1000 張的人數，最近 N 週嚴格單調增加
4. 集保總戶數（TDCC 合計列）最新一週低於 N 週前，且下降幅度達門檻（預設 0＝只要下降即可；可用 `--no-holder-decrease` 關閉硬性過濾）
5. 近三個月漲幅不得超過 40%
6. 最新收盤價距離 20 日均線不得超過 8%

集保總戶數資料週數不足以做 N 週比較時，直接判定不通過（不會退回較短窗，避免短窗訊號被誤標成長窗）。

## 評分制（0–100，僅對通過股票計算，門檻定義於 config.py）

| 維度 | 滿分 | 線上訊號 | 對應原 CSV 版概念 |
|---|---|---|---|
| 大戶人數增幅 | 30 | 大戶（>1000 張）人數自最舊到最新週的增幅 | 連續買超強度／主力吸籌 |
| 集保總戶數降幅 | 25 | 集保總戶數下降比例 | 集保戶數下降 |
| 距 20MA | 25 | 收盤價距 20MA 越近越高分 | 現價接近主力成本 |
| 近三月漲幅溫和 | 20 | 漲幅越溫和（含負報酬）越高分 | 價格仍有上行空間 |

評分結果同時提供「選股分數」與「評級」欄位，pass 清單預設依分數由高到低排序。

評分語意補充：

1. 大戶人數增幅、集保總戶數降幅屬「方向訊號」，數值 ≤ 0（零成長或反向，例如關閉硬性過濾時集保戶數其實在增加）一律 0 分，不給地板分
2. 近三月漲幅維度將溫和或小幅負報酬視為高分，但跌幅深於門檻（預設 −30%）視為弱勢、直接 0 分，避免崩跌股反而拿滿分

## TDCC 分級換算說明

TDCC 官方分級不是每一張都切得很細，因此程式採用 metadata 聚合方式：

1. 小於 10 張：加總 1-999、1,000-5,000、5,001-10,000 股三個桶
2. 400 到 800 張：加總 400,001-600,000、600,001-800,000 股兩個桶
3. 大於 1000 張：使用 1,000,001 股以上桶

注意：

1. 小於 10 張會把剛好 10,000 股一併納入，這是官方分級粒度造成的量化近似
2. 400 到 800 張會排除剛好 400,000 股邊界，因為官方把 400,000 股放在前一桶

## 快取與效能優化

目前版本已加入以下優化：

1. 公司清單、最新 TDCC 快照、TWSE 月資料、TPEX 月資料會寫入 .cache 目錄
2. TDCC 歷史週資料會依 股票代號 + 日期 做磁碟快取，重跑時不必再次查詢官方頁面
3. TDCC 歷史「查無資料」會寫入負快取（預設 1 天），避免每次（尤其回測）對下市／當期無資料股票重打查詢頁
4. TDCC 歷史查詢會重用結果頁中的新 token，避免每一筆都先重新載入查詢首頁
5. 只有籌碼條件先通過的股票，才會往下抓價格資料
6. 抓價階段可併發（--price-workers / Streamlit「抓價併發數」）；跨執行緒仍以最低請求間隔節流，兼顧速度與禮貌

## 輸出內容

終端機會印出：

1. 共分析檔數
2. 共篩選出檔數
3. 最新 TDCC 日期
4. Excel 完整輸出路徑
5. pass / fail 表格

Excel 檔預設包含三個工作表（皆含「選股分數」「評級」「集保總戶數變化」欄位）：

1. pass：通過的股票（依選股分數由高到低排序）
2. fail：未通過的股票
3. raw_data_summary：每檔股票的資料完整度、TDCC 週數、價格天數、各關卡是否通過與失敗原因；價格區間若含疑似除權息／減資的大跳空（單日逾約 11%），會在「備註」標註，提醒此處價格未還原、均線與報酬可能失真

若使用回測模式，會額外加入：

1. backtest：通過篩選股票的選股分數、評級、篩選日收盤價、篩選日期、1 個月後報酬率、3 個月後報酬率

匯出時會對字串前導的 `=`、`@`、`+`/`-`（後接非數字）加單引號轉義，避免 Excel 公式注入。
Streamlit 介面不再自動把 Excel 寫到伺服器磁碟，改以下載按鈕提供。

預設檔名格式為：

1. stock_screen_result_YYYYMMDD.xlsx

## 常見錯誤排查

1. TDCC 歷史查詢失敗

- 可能原因：TDCC 查詢頁 token 或欄位格式變更
- 建議做法：先執行 python3 main.py --stock-limit 1 --log-level DEBUG 觀察 log，再檢查 api_clients.py 內 TDCCPortalHistoryClient 的欄位解析

1. TWSE 或 TPEX 價格資料不足

- 可能原因：股票新掛牌、暫停交易、該月份資料尚未提供
- 程式行為：會標示為價格資料不足，不會直接崩潰

1. Python 3.14 憑證驗證失敗

- 可能原因：TWSE、TPEX、TDCC 官方站台憑證鏈在部分 Python / OpenSSL 組合下驗證失敗
- 程式行為：只對已知官方資料主機做受控 verify=False fallback，並在 log 中提示一次

1. 第一次跑全市場太久

- 可能原因：TDCC 歷史資料仍需逐檔逐週補抓
- 建議做法：第一次先讓快取建立完成，之後重跑會明顯變快

1. Excel 無法覆寫

- 可能原因：既有檔案仍被 Excel 開啟
- 建議做法：先關閉檔案後再重跑
