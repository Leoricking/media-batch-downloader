# 🚀 Media Batch Downloader

> Current documentation: v11.54 Post Account + Dynamic Carousel Completeness

Media Batch Downloader 是一套用於 Instagram / Facebook 連結預處理與批次下載的 Windows 桌面工具。它支援大量連結匯入、URL 預處理、GUI 任務佇列、狀態分類、斷點續跑、失敗清單整理，以及 Instagram / Facebook 的多引擎下載 fallback。

---

## 核心特色


- 支援 Instagram 貼文、Carousel 多圖、Reels
- 支援 Facebook 貼文、分享連結、影片、Reels、多圖完整性檢查
- 支援 `facebook.com/share/...`、`facebook.com/share/r/...`、`fb.watch/...` 等分享格式
- 支援批次 URL 匯入與預處理分類
- 支援 GUI 拖放 `.txt` 檔案
- 支援 IG Parser / FB Parser 專用 Chrome Profile 登入模式，不再需要手動匯出 cookies.txt
- 保留 cookies.txt 作為 legacy / emergency fallback
- 支援 IG Parser 專用 Chrome Profile，處理年齡 / 特定對象限制貼文
- 支援 FB Parser 專用 Chrome Profile，處理 Facebook 登入、2FA、相簿與 Reel fallback
- 支援 Instagram 受限 Carousel 的 post 鎖定、完整數量檢查、順序保留與防推薦貼文污染
- 支援下載前預取 Instagram Post Account 與 Post Title，先顯示於 GUI 再開始下載
- GUI 任務表格顯示 URL / Post Account / Post Title / 狀態 / Retry，欄寬會依視窗大小自動調整
- 支援 `img_index=` 分享網址的 canonical first-slide navigation，避免從第 2 張開始而漏掉第一張
- Carousel 不再只相信分頁點數量，會動態走到真正最後一張後才判定 SUCCESS
- 支援 IG 媒體真實檔頭判斷，WEBP 會轉成真正 JPEG，避免假 .jpg
- 支援自動簡體轉繁體
- 支援安全檔名與資料夾命名
- 支援已下載 checkpoint，避免重複下載
- 安全延遲與重試機制，降低帳號風控風險
- 支援 FAILED / BLOCKED / MISSING / RETRY / UNAVAILABLE 狀態管理
- 支援暫停、繼續、停止與失敗重試
- 下載批次完成後會跳出結果通知視窗

---

## 專案結構

```text
media-batch-downloader/
├─ downloader_GUI/
│  ├─ main.py
│  ├─ worker.py
│  ├─ queue_manager.py
│  ├─ build.bat
│  ├─ run.bat
│  ├─ install.txt
│  ├─ README.md
│  ├─ cookies.txt
│  ├─ accounts.json
│  ├─ downloader/
│  │  ├─ instagram.py
│  │  └─ facebook.py
│  ├─ data/
│  │  ├─ chrome_ig_parser/      # IG Parser 專用 Chrome Profile，用於登入、受限貼文與 Carousel fallback
│  │  ├─ playwright_fb_profile/ # FB Parser 專用 Chrome Profile，用於登入、相簿與 Reel fallback
│  │  ├─ processed_links.log
│  │  ├─ failed_links.log
│  │  ├─ retry_needed.txt
│  │  └─ unavailable_links.txt
│  ├─ downloads/
│  └─ post/                 # 暫存資料夾，任務成功搬移或失敗結束後會自動清理
├─ pre-processing/
│  ├─ link_sorter.py
│  └─ output/
│     ├─ download_link.txt
│     └─ undownload_link.txt
```

---

## 安裝需求

建議環境：

- Windows 10 / 11
- Python 3.10 以上
- pip
- 可連線網路
- Chromium for Playwright

安裝套件：

```powershell
python -m pip install --upgrade pip
python -m pip install -U instaloader yt-dlp opencc-python-reimplemented requests playwright pycryptodome keyring
python -m pip install -U tkinterdnd2 pyinstaller
python -m playwright install chromium
```

若只想執行、不需要拖放功能，`tkinterdnd2` 可以不裝，但 GUI 拖放 `.txt` 會失效。

---

## 快速啟動

```powershell
cd downloader_GUI
python main.py
```

或直接執行：

```powershell
run.bat
```

---

## 建置 EXE

```powershell
cd downloader_GUI
build.bat
```

建置完成後會產生：

```text
release/MediaBatchDownloader.exe
release/data/
release/downloads/
```

正式使用時可直接執行：

```text
release/MediaBatchDownloader.exe
```

---

## IG Parser / FB Parser 專用登入模式（推薦）

新版推薦使用 GUI 內建的專用 Chrome Persistent Profile，不再需要手動匯出 cookies.txt。

### IG Parser 第一次登入

1. 在 GUI 點「🌐 IG Parser」。
2. 下載器會開啟專案專用 Chrome Profile：

```text
downloader_GUI/data/chrome_ig_parser
```

3. 在該 Chrome 視窗登入 Instagram。
4. 完成 2FA、裝置驗證、記住這台設備。
5. 若有年齡限制、特定對象限制貼文，請在該視窗手動打開並完成確認。
6. 完成後關閉 Chrome 視窗，回到下載器繼續下載。

之後 IG Playwright fallback 會優先使用這個 Profile 的登入 / 年齡確認 / trust state。

### FB Parser 第一次登入

1. 在 GUI 點「🌐 FB Parser」。
2. 下載器會開啟專案專用 Chrome Profile：

```text
downloader_GUI/data/playwright_fb_profile
```

3. 在該 Chrome 視窗登入 Facebook。
4. 完成 2FA、保持登入、信任此裝置。
5. 建議打開一個需要下載的貼文、相簿或 Reel，確認瀏覽器可以正常觀看。
6. 完成後關閉 Chrome 視窗，回到下載器繼續下載。

之後 Facebook Playwright fallback 會優先使用這個 Profile，不再依賴手動匯出的 FB cookies。

注意：

- 下載時請先關閉手動登入用的 IG Parser / FB Parser Chrome 視窗，避免 Chrome profile lock。
- IG Parser 與 FB Parser 是專案內的獨立 Profile，不會使用你日常 Chrome Default Profile。
- 不要把 `data/chrome_ig_parser/`、`data/playwright_fb_profile/`、`cookies.txt`、`accounts.json` 提交到 Git。

---

## cookies.txt 說明（Legacy / Emergency Fallback）

`cookies.txt` 仍支援，但新版不再把它當主要登入方式。它只保留給 Instaloader / yt-dlp 或特殊情況作為備援。

用途：

- 作為 IG / FB parser profile 失效時的 emergency fallback
- 讓 Instaloader / yt-dlp 在部分情境下可讀取舊式 Netscape cookie

注意：

- 同一份 `cookies.txt` 可同時包含 `.instagram.com` 與 `.facebook.com`
- 若瀏覽器看得到但程式抓不到，優先重新初始化 IG Parser / FB Parser Profile，而不是先更新 cookies.txt
- 請勿將 `cookies.txt`、`accounts.json` 或任何登入 Profile 目錄提交到 Git

---

## Instagram 下載機制

Instagram 目前採多引擎策略：

```text
Instaloader
→ Playwright DOM / Network Cache
→ yt-dlp fallback
```

### 圖文 / Carousel 貼文

對 `/p/` 圖文貼文，若 Instaloader 因 GraphQL 403 失敗，系統會優先使用 Playwright。Playwright 會：

- 開啟真實 Instagram 頁面
- 讀取主圖 / 影片 DOM
- 逐張點擊 Carousel 下一張
- 同時攔截瀏覽器已載入成功的 Network response
- 若 CDN 二次請求失敗，直接使用 Browser Network Cache 寫檔

這可避免「瀏覽器看得到，但程式二次抓 CDN 被拒絕」造成的 FAILED。

### Reel / 影片

對 `/reel/` 或 `/reels/`，仍優先使用 yt-dlp，失敗後再由 Playwright fallback。

### IG Parser 專用 Chrome Profile 與受限貼文

當 Instagram 貼文出現年齡限制、特定對象限制，或 `Instaloader` / `yt-dlp` 回傳 `empty media response` 時，下載器會啟用專案內建的 IG Parser 專用 Chrome Profile：

```text
downloader_GUI/data/chrome_ig_parser
```

設計原則：

- 不使用日常 Chrome `Default` Profile，避免與平常瀏覽器搶鎖或造成空白視窗
- 保留登入狀態、年齡確認與裝置信任狀態
- 每筆任務仍會鎖定原始 shortcode，避免跳到推薦貼文、帳號頁或其他 post
- Carousel 會偵測 `total_count`，缺圖不會假成功，會回 `RETRY`
- 若網址包含 `img_index=`，會優先走已驗證穩定的 clean persistent page 路徑
- 其他受限長 Carousel 會走 fresh tab 路徑，避免舊 DOM / 舊 dialog 污染
- 掃描前會清除 persistent profile 的預載 network cache，避免推薦貼文或上一筆任務圖片混入
- 下載時會保留 Carousel 翻頁順序，避免依圖片品質分數重新排序

若第一次使用 IG Parser，請先在 GUI 點「🌐 IG Parser」，登入 Instagram 並完成必要的年齡或帳號確認。完成後即可回到下載器批次執行，不需要手動匯出 cookies.txt。

### Instagram Carousel 動態完整遍歷

新版 Carousel 不再把初始分頁點（dots）數量直接當成真實總張數。部分贊助貼文、廣告型 Carousel 或 `img_index=` 分享網址，初始 DOM 可能只顯示兩個導覽節點，但實際貼文有更多頁。

目前流程：

```text
鎖定目標 shortcode
→ 必要時使用 IG Parser Persistent Profile
→ 將瀏覽器導航網址中的 img_index 移除
→ 從第一張開始
→ 逐張點擊 Next
→ 每次確認主媒體 key 確實改變
→ Next 真正消失或停用後才確認到達最後一張
→ 實際走過的張數作為 true_total
→ true_total 全部成功寫入才回 SUCCESS
```

保護規則：

- 原始任務 URL 與 `img_index=` 仍會保留，用於路由與 GUI 顯示
- 只有瀏覽器實際導航時暫時移除 `img_index=`，確保從第一張開始
- 若回到第一張後仍存在可操作的 Previous，會拒絕 false SUCCESS
- 若 Next 還存在但下一張沒有完成載入，會回 `RETRY`
- 不會因初始 dots 顯示 `2` 就只下載兩張
- 不會因 URL 從 `img_index=2` 開啟而漏掉第一張
- 每次翻頁都會檢查目標 shortcode，避免跳到推薦貼文
- 正常單張 Post 與 Reel 不會啟動 Carousel 翻頁流程

常用 Log：

```text
IG canonical first-slide navigation lock
IG carousel first-slide lock
IG dynamic carousel walk start
IG dynamic carousel walk: slide=
IG dynamic carousel walk complete: true_total=
IG dynamic carousel traversal incomplete
```

### IG 媒體格式與 WEBP 處理

Instagram 可能回傳 WEBP bytes，但 URL 或暫存檔名看起來像 `.jpg`。新版會以檔頭 magic bytes 判斷真實格式：

| 格式 | 判斷方式 |
|---|---|
| JPEG | `FF D8 FF` |
| WEBP | `RIFF ... WEBP` |
| PNG | PNG header |
| MP4 | `ftyp` |

處理規則：

- 若回傳 WEBP 且已安裝 Pillow，會轉成真正 JPEG
- 若未安裝 Pillow，會保留 `.webp` 真實副檔名
- 不再把 WEBP bytes 假裝命名成 `.jpg`
- `move_files()` 搬移前會再次檢查真實檔案格式
- 多檔搬移會使用自然排序，避免 `ig_10` 排在 `ig_2` 前面

### Instagram 狀態分類

| 狀態 | 說明 |
|---|---|
| `SUCCESS` | 已成功輸出有效媒體檔案 |
| `MISSING` | 貼文不存在、頁面已移除、連結失效 |
| `BLOCKED` | 需要登入、checkpoint、challenge、私人帳號或權限限制 |
| `RETRY` | 暫時性錯誤，例如 timeout、429、rate limit |
| `FAILED` | 下載流程失敗，但不屬於以上分類 |

### IG 暫存清理規則

`post/` 是暫存資料夾，不是正式輸出資料夾。

目前規則：

- 每筆 IG 任務開始前，會先清理上一筆失敗或中斷留下的 `post/` 殘留
- IG 任務若以 `FAILED` / `BLOCKED` / `MISSING` / `RETRY` 結束，會自動清空 `post/`
- `SUCCESS` 不會在最外層亂清，仍由 `move_files()` 在成功搬移到 `downloads/` 後清理
- 這樣可以避免誤刪已成功下載但正在搬移的媒體

---

## Facebook 下載機制

Facebook 下載器針對多圖、Reel、Viewer 模式做了防污染保護。

### 支援連結

- `facebook.com/share/...`
- `facebook.com/share/r/...`
- `facebook.com/share/v/...`
- `facebook.com/watch/...`
- `facebook.com/reel/...`
- `facebook.com/.../videos/...`
- `facebook.com/.../posts/...`
- `facebook.com/story.php...`
- `fb.watch/...`

### FB Parser 專用 Chrome Profile

Facebook Playwright fallback 會優先使用：

```text
downloader_GUI/data/playwright_fb_profile
```

這個 Profile 會保留 Facebook 登入、雙重驗證、保持登入與信任裝置狀態。第一次使用請先在 GUI 點「🌐 FB Parser」完成登入。完成後，FB 一般貼文、多圖相簿、大相簿、share/r 與 Reel fallback 都會使用此 Profile。

`cookies.txt` 只保留為 legacy / emergency fallback，不再是主要推薦流程。

### 多圖貼文保護

Facebook 多圖貼文會使用 Playwright viewer-intercept 收集候選媒體，並比對原生 Grid / Photo link 數量。

保護原則：

- 多圖缺圖會回傳 `RETRY`，不會假裝成功
- 多圖貼文若抓取失敗，禁止退化成單一影片檔結案
- 若圖片與影片候選混在一起，圖片貼文優先保留圖片，避免推薦影片污染
- 若多檔輸出仍是 fallback title `Facebook_Post`，會阻擋高風險輸出，避免錯誤候選檔搬成正式結果

---

## GUI 任務表格

新版任務表格欄位：

```text
URL | Post Account | Post Title | 狀態 | Retry
```

欄位行為：

- `URL`：保留原始任務網址，靠左顯示
- `Post Account`：下載前先預取發文帳號，例如 `successful101_official`
- `Post Title`：下載前先預取完整 caption / 標題
- `Post Account`、`Post Title`、`狀態`、`Retry` 皆置中顯示
- 視窗放大時，URL / Account / Title 會自動加寬
- 視窗縮小時會保留最小欄寬，超出部分交由水平捲軸，不會黏在一起
- 帳號或標題預取失敗時，不會阻止正常下載，下載階段仍會再次補抓

Instagram 任務的預設流程：

```text
取得任務
→ 預取 Post Account
→ 預取 Post Title
→ 更新 GUI
→ 開始正式下載
```

## GUI 使用方式

1. 開啟 GUI：

```powershell
cd downloader_GUI
python main.py
```

2. 在輸入框貼上 URL，每行一個。
3. 按「🚀 開始下載」。
4. 可使用：
   - 「⏸ 暫停」
   - 「▶ 繼續」
   - 「⏹ 停止」
   - 「🔁 重試失敗」
   - 「📄 查看失敗」
   - 「🚫 複製 BLOCKED」
   - 「🌐 IG Parser」
   - 「🌐 FB Parser」
5. 任務完成後會跳出下載結果摘要視窗。

### 下載完成通知

批次任務完成後，GUI 會顯示非阻塞通知視窗，包含：

- 總任務數
- SUCCESS 數量
- FAILED 數量
- BLOCKED 數量
- MISSING 數量
- RETRY 數量
- UNAVAILABLE 數量
- 耗時

視窗不會 `grab_set()`，因此不會阻塞 worker 或造成 GUI 看似 hang 住。

---

## 預處理分類

若你有大量原始文字或混合連結，可以先放進：

```text
EX: pre-processing/xxx.txt
```

然後在 GUI 按：

```text
📂 匯入 txt（自動預處理）
```

或手動執行：

```powershell
cd pre-processing
python link_sorter.py
```

輸出：

```text
pre-processing/output/download_link.txt
pre-processing/output/undownload_link.txt
```

`download_link.txt` 會自動載入 GUI 下載器。

---

## 已下載跳過 / 斷點續跑

程式使用：

```text
data/processed_links.log
```

記錄已成功下載的 URL。

若同一個連結之後再次加入任務：

- 不會重複下載
- 會直接略過或標記為已處理

若想重新下載已完成連結，可在 GUI 使用「🧼 清除已下載紀錄」。

---

## 失敗與重試紀錄

程式會寫出：

```text
data/failed_links.log
data/retry_needed.txt
data/unavailable_links.txt
data/failed_history.log
data/tasks_snapshot.tsv
```

用途：

| 檔案 | 說明 |
|---|---|
| `failed_links.log` | 目前失敗 / 受限 / 不存在任務 |
| `retry_needed.txt` | 建議之後可重試的 URL |
| `unavailable_links.txt` | 已失效、MISSING 或不可用 URL |
| `failed_history.log` | 歷史失敗事件流水帳 |
| `tasks_snapshot.tsv` | 關閉時輸出的任務快照 |

---

## 暫停 / 繼續 / 停止

### 暫停

暫停新的下載流程；若正在冷卻，會停在冷卻流程中。

### 繼續

從暫停狀態恢復。若之前按過停止，GUI 會嘗試重新喚醒 worker，並恢復被中斷的任務。

### 停止

停止整體下載流程。正在下載中的任務會嘗試安全收尾，若中斷會標記為可恢復狀態，之後可按「▶ 繼續」或「🔁 重試失敗」。

---

## Debug 指南

### Instagram

常用 Log 關鍵字：

```text
IG filtered media count=
network harvest=
IG 使用 browser network cache 寫入
IG Playwright 已成功寫入
IG 清理暫存 post/
IG strategy pre-route: img_index URL detected
IG 清除 persistent profile 預載 network cache
IG canonical first-slide navigation lock
IG carousel first-slide lock
IG dynamic carousel walk start
IG dynamic carousel walk: slide=
IG dynamic carousel walk complete: true_total=
IG dynamic carousel traversal incomplete
IG carousel network fill:
```

若看到：

```text
No video formats found
```

這通常代表 yt-dlp 對圖片貼文找不到影片格式，不一定是錯誤。圖片貼文應以 Playwright 結果為準。

### Facebook

常用 Log 關鍵字：

```text
FB expected photo target=
FB viewer sequence count=
FB merged candidate media count=
FB filtered media count=
FB unique output media count=
FB move_files blocked
```

若 Facebook Reel 被抓到大量 mp4，可能是推薦影片或預載片段污染，需以 Reel 主影片篩選邏輯處理，不應直接搬成 `Facebook_Post` 多檔資料夾。

---

## 常見問題

### 1. 為什麼會有 post/ 目錄？

`post/` 是暫存資料夾。媒體會先寫入 `post/`，成功後再搬到 `downloads/`。若任務失敗或中斷，系統會自動清理。若你手動中斷程式導致殘留，可以關閉 GUI 後刪除：

```powershell
rmdir /s /q post
```

### 2. 顯示 BLOCKED 是不是程式錯？

不一定。`BLOCKED` 通常代表登入、checkpoint、challenge、私人帳號、特定受眾或權限限制。

### 3. 顯示 MISSING 是什麼？

`MISSING` 代表頁面不存在、連結故障、貼文已移除，或 Instagram / Facebook 顯示內容不可用。

### 4. 瀏覽器看得到，但程式抓不到？

請依序確認：

1. GUI 的 `🌐 IG Parser` 或 `🌐 FB Parser` 是否已完成登入
2. 專用 Chrome 視窗是否仍開著，造成 Profile lock
3. 是否遇到 checkpoint、challenge、2FA、年齡或特定受眾限制
4. 是否遭遇平台 rate limit，需稍後重試
5. Reel 是否需要先在 Parser 視窗播放數秒
6. `cookies.txt` 僅作 legacy fallback，不應優先依賴

### 5. build.bat 失敗？

可能原因：

- Python 沒加入 PATH
- pip 套件安裝不完整
- Playwright Chromium 沒安裝
- 防毒軟體阻擋 PyInstaller

先回原始碼模式確認：

```powershell
python main.py
```

---

## 測試指令

```powershell
cd downloader_GUI
python -m py_compile main.py queue_manager.py worker.py downloader\instagram.py downloader\facebook.py
python main.py
```

---

## 版本紀錄

### v11.54 Post Account + Dynamic Carousel Completeness

- GUI 新增 `Post Account` 欄位，與 `Post Title` 一樣在正式下載前預取
- 任務表格改為 `URL / Post Account / Post Title / 狀態 / Retry`
- `Post Account` 與 `Post Title` 的標題和內容置中顯示
- URL 保持靠左，狀態與 Retry 維持置中
- 新增響應式欄寬，視窗縮放時會自動重新分配 URL / Account / Title 寬度
- 視窗太窄時保留可讀最小寬度，使用水平捲軸避免欄位黏在一起
- queue task 新增 `account` 欄位與 `update_task_account()`
- worker 會先執行 `prefetch_post_info()`，取得帳號與標題後才開始下載
- 保留舊版 `prefetch_post_title()` API，避免舊 worker 或其他呼叫端失效

### v11.52 Canonical First-Slide Navigation Lock

- 修正 `img_index=2` 分享網址從第 2 張開始，造成 Carousel 第一張漏下載
- 保留原始任務 URL 與 v7 / v8 routing，只在瀏覽器導航時移除 `img_index`
- 新增 first-slide lock，確認已回到第一張後才開始收集
- 若仍存在可操作的 Previous，拒絕 false SUCCESS 並轉為重試
- 加強不同語系 Previous 控制項辨識

### v11.50 Dynamic Carousel End-Walk Lock

- 初始 dots 數量改為提示值，不再當成硬性總張數
- Carousel 會持續點擊 Next，直到真正無法再前進
- 每次翻頁都驗證主媒體 key 是否改變
- Next 尚存在但新媒體未完成載入時回 `RETRY`
- 動態遍歷完成後，以實際收集張數作為 `true_total`
- 避免廣告型、贊助型 Carousel 只抓 2 張卻誤判 SUCCESS

### v11.48 Caption Lock + Full-Frame Validation

- 標題優先使用目標貼文 caption metadata，排除 Instagram UI 提示與留言雜訊
- 清除 likes / comments / 日期等 metadata 前綴
- 保留長篇中文、英文與贊助貼文 caption
- 新增下載圖片尺寸、比例與可視畫面幾何驗證
- 拒絕過小、極端比例、正方形裁切或只剩局部畫面的候選
- 不完整或裁切媒體不再 false SUCCESS

### v11.27 Parser Login Profiles

- 新增 IG Parser / FB Parser 專用 Chrome Persistent Profile 登入模式
- GUI 新增「🌐 FB Parser」
- GUI 將 IG Parser 改為「🌐 IG Parser」
- FB Playwright fallback 優先使用 `data/playwright_fb_profile`
- IG Playwright fallback 繼續使用 `data/chrome_ig_parser`
- `cookies.txt` 改為 legacy / emergency fallback，不再是主要推薦流程
- 保留 v11.26 IG restricted Carousel routing、total_count、scoped network fill、WEBP/JPEG 檔頭檢查
- 保留 Facebook 貼文、多圖相簿、大相簿、share/r、Reel fallback 與完整性檢查

### v11.26 IG Restricted Carousel Lock

- 修正 Instagram 年齡 / 特定對象限制貼文的 Carousel fallback
- 新增 IG Parser 專用 Chrome Profile 路徑策略，避免與日常 Chrome profile 搶鎖
- 保留 v7 / v8 A/B 測試後的穩定路徑：
  - `img_index=` 圖文貼文預先走 v7 clean persistent page
  - 其他受限長 Carousel 走 v8 fresh tab
- 修正受限 Carousel 第一張被上一筆任務、推薦貼文或舊 dialog 污染
- 修正 3 張 Carousel 頭尾漏檔問題
- 修正 10 張 Carousel 只抓 4 張卻誤判 SUCCESS 問題
- 修正 Carousel 順序被圖片品質分數排序打亂問題
- 新增 `total_count` 檢查，若 expected / got 不一致則回 `RETRY`，不假成功
- 新增 scoped network fill，只用目標 post 翻頁期間的新鮮快取補圖
- 新增 WEBP / JPEG / PNG / MP4 檔頭判斷
- WEBP 內容預設轉成真正 JPEG；沒有 Pillow 時保留 `.webp`
- `move_files()` 搬移前再次檢查真實副檔名
- 多檔搬移改自然排序，避免 `ig_10` 排在 `ig_2` 前面

### v11.25 Stable Cleanup

- IG 任務開始前清理上一筆殘留 `post/`
- IG 任務若以 `FAILED` / `BLOCKED` / `MISSING` / `RETRY` 結束，自動清空 `post/`
- `SUCCESS` 不在最外層亂清，仍由 `move_files()` 成功搬移後清理
- 新增下載完成後 GUI 結果通知視窗
- 保留 Instagram Browser Network Cache fallback
- 保留 Instagram Playwright 成功後防止 yt-dlp 覆蓋狀態的保護
- 保留 Windows 安全檔名與路徑縮短處理

### v11.24 Stable Fix

- 修正 Instagram `empty media response` 誤判為 `BLOCKED`
- 修正失效 IG 頁面判定為 `MISSING`
- 修正 `KeyError: skipped_processed`
- 修正停止後再繼續可能卡住的 worker lifecycle 問題
- 強化 Facebook 多圖完整性檢查與 fallback 保護

---

## 建議日常使用方式

開發 / 測試：

```powershell
run.bat
```

正式打包：

```powershell
build.bat
```

正式使用：

```text
release/MediaBatchDownloader.exe
```
