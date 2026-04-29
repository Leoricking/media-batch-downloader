# 🚀 Media Downloader Tool

Media Downloader Tool 是一套用於 Instagram / Facebook 連結預處理與批次下載的工具。

支援：

- Instagram 貼文 / Reels
- Facebook 貼文 / 分享連結 / 影片 / 多圖貼文
- 批次連結預處理
- 自動分類可下載與不可下載連結
- 自動簡體轉繁體
- 自動整理檔名與資料夾
- Facebook 多圖完整性檢查
- 安全延遲與重試機制，降低帳號風控風險

---

## 🛠️ 必要安裝套件

執行前請確保已安裝以下 Python 套件：

```bash
pip install instaloader yt-dlp opencc requests playwright
playwright install chromium
```
---

## 🧠 Facebook 多圖下載機制

新版本針對 Facebook 複雜的多圖結構導入了「防遺漏保護機制」：

精準鎖定：使用 `Post / PCB scope` 技術排除廣告、側邊欄與推薦貼文干擾。
完整度檢查：自動比對 FB 原生 Grid 偵測到的數量（如 `target=16`）。
防錯結案原則：
多圖缺圖 = RETRY：若預期 16 張但只抓到 15 張，系統回傳 `RETRY` 並重新排隊，不會視為下載成功。
拒絕退化：多圖貼文若抓取失敗，嚴禁 fallback 成下載單一影片檔結案。
暫存隔離：只有完整下載成功的內容才會搬移至 `downloads/` 資料夾。

---

## ⚙️ 進階配置

### 1. Cookies 設定
為避免帳號驗證阻攔，請使用瀏覽器外掛匯出 Netscape 格式的 `cookies.txt` 並置於專案根目錄。
同一份檔案可同時包含 `.facebook.com` 與 `.instagram.com` 的登入資訊。
安全提示：請勿將 `cookies.txt` 或 `accounts.json` 提交到 Git。

### 2. 下載延遲 (Safety Delay)
目前程式實作預設為下載成功後隨機延遲 20～40 秒。此設定用於模擬真人行為，避免短時間內大量請求。

---

## 🧪 Debug 指南

若發現下載不完全或 FB 偵測錯誤，請檢查：
1.  瀏覽器檢查：將 `FB_HEADLESS` 設為 `False` 觀察 Playwright 運作過程。
2.  Log 關鍵字：搜尋 `FB expected photo target=` 與 `FB unique output media count=`。
3.  重新整理：若發生連鎖 `RETRY`，請確認這是一份整理後的 `README.md`。我根據你提供的內容，強化了標題層級、修正了路徑層次感，並將核心邏輯與操作步驟進行了視覺化區隔，適合直接覆蓋。

---

📖 執行流程建議本工具採兩階段作業：先分類連結、後執行下載。
第一階段：預處理分類 (Pre-processing)
1. 將包含連結的原始文字檔（如：20260404.txt）放入 pre-processing/ 目錄。  
2. 執行分類腳本：
```
cd pre-processing
python link_sorter.py
```

邏輯與輸出：

邏輯：過濾個人主頁，保留具備 Media ID 的有效連結（如 /p/, /reel/, /watch/, /posts/ 等）。

輸出：

output/download_link.txt (可下載清單)

output/undownload_link.txt (無效或不支援清單)

第二階段：批次下載 (Downloader)
您可以根據偏好選擇 GUI 介面 或 CLI 命令列 模式。

🔹 模式 A：新版 GUI 介面 (推薦)適合需要視覺化監控下載進度的使用者。
```
cd downloader_GUI
python main.py
# 或直接執行目錄下的 run.bat
```
🔹 模式 B：舊版 CLI 下載器
適合伺服器端或自動化腳本調用。
```
cd downloder
python media_batch_downloader.py
```
