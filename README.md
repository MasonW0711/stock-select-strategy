# 台股籌碼選股工具

這是一個可直接執行的台股自動選股專案，現在同時支援上市與上櫃，並提供兩個入口：

1. CLI：執行 python main.py
2. Streamlit：執行 streamlit run streamlit_app.py

資料來源全部來自公開 HTTP API 或官方查詢端點，不使用 CSV 或 Excel 當資料來源：

1. TWSE OpenAPI：上市股票清單與上市個股月日線
2. TPEX OpenAPI：上櫃股票基本資料
3. TPEX 官方 JSON 端點：上櫃個股歷史日成交資訊
4. TDCC OpenAPI：最新一週股權分散表
5. TDCC 官方查詢頁：歷史週別股權分散資料

## 專案檔案

1. config.py：集中管理 endpoint、欄位對映、預設市場與 cache 設定
2. models.py：股票、股權分散快照、價格日線與摘要 dataclass
3. api_clients.py：TWSE、TPEX、TDCC client、SSL fallback 與磁碟快取
4. screener.py：選股主流程、趨勢判斷、價格條件與 DataFrame 輸出
5. utils.py：日期、數字、股票代號與 logging 工具
6. main.py：CLI 入口、共用執行函式、Excel 匯出
7. streamlit_app.py：Streamlit 互動介面

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

1. 指定 Excel 輸出檔名

```bash
python3 main.py --output ./result.xlsx
```

## Streamlit 執行方式

1. 啟動介面

```bash
streamlit run streamlit_app.py
```

1. 在左側調整市場、週數、價格條件、是否啟用快取
2. 按下「開始篩選」
3. 畫面會顯示通過清單、失敗清單、raw_data_summary，並自動把 Excel 寫到專案資料夾

## 選股邏輯

1. 持股小於 10 張的人數，最近 N 週嚴格單調下降
2. 持股 400 到 800 張的人數，最近 N 週嚴格單調增加
3. 持股大於 1000 張的人數，最近 N 週嚴格單調增加
4. 近三個月漲幅不得超過 40%
5. 最新收盤價距離 20 日均線不得超過 8%

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
3. TDCC 歷史查詢會重用結果頁中的新 token，避免每一筆都先重新載入查詢首頁
4. 只有籌碼條件先通過的股票，才會往下抓價格資料

## 輸出內容

終端機會印出：

1. 共分析檔數
2. 共篩選出檔數
3. 最新 TDCC 日期
4. Excel 完整輸出路徑
5. pass / fail 表格

Excel 檔包含三個工作表：

1. pass：通過的股票
2. fail：未通過的股票
3. raw_data_summary：每檔股票的資料完整度、TDCC 週數、價格天數與失敗原因

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
