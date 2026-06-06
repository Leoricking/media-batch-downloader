# 🚀 Media Batch Downloader

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
│  │  ├─ chrome_ig_parser/      # IG Parser 專用 Chrome Profile，用於 IG 登入 / 受限貼文 fallback
│  │  ├─ playwright_fb_profile/ # FB Parser 專用 Chrome Profile，用於 FB 登入 / 相簿 / Reel fallback
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

1. 在 GUI 點「🌐 IG Parser 登入/初始化」。
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

1. 在 GUI 點「🌐 FB Parser 登入/初始化」。
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


### Instagram 主頁全自動展開下載

可直接貼 Instagram 帳號主頁網址，也支援 Reels 分頁網址，例如：

```text
https://www.instagram.com/bubu.dudu_hk/
https://www.instagram.com/duolastudy/reels/
```

系統會自動判斷這是 IG 主頁 / Reels 分頁，而不是單篇貼文。`/<username>/reels/` 會先展開成真正的 `/reel/<shortcode>/` 單篇任務，不會直接下載主頁網格縮圖。處理流程如下：

```text
IG 主頁 URL / IG Reels 分頁 URL
→ 使用 IG Parser 專用 Chrome Profile 掃描主頁與 Reels 分頁
→ 自動收集 /p/ 與 /reel/ 貼文連結
→ 展開成多筆單篇任務加入 GUI 佇列
→ 每篇仍走原本穩定的單篇 IG 下載流程
→ 輸出仍依原本 post title / caption 規則建立資料夾
```

設計原則：

- 主頁 / Reels 分頁任務只負責「掃描與展開」，不直接在同一個任務內下載全部媒體。
- 每一篇子任務仍使用原本 v11.26 已驗證的單篇下載流程；Reels 會進入單篇 Reel 下載，不會只抓預覽縮圖。
- 保留 `img_index=` 預分流、restricted Carousel post lock、`total_count` 檢查、scoped network fill、WEBP/JPEG magic bytes 檢查。
- 已在 `processed_links.log` 內的貼文會自動略過。
- 佇列中已存在的貼文不會重複加入。
- 不抓 Stories；Stories 需未來另外做獨立模組。
- 私人帳號、checkpoint、challenge、權限不足會回 `BLOCKED`。
- 若主頁貼文很多，掃描時間會較久，建議先完成 IG Parser 登入與信任裝置初始化。

使用方式：

1. 先點「🌐 IG Parser」完成 Instagram 登入、2FA、信任裝置。
2. 關閉手動登入用 Chrome 視窗，避免 profile lock。
3. 在 GUI 輸入框貼上 IG 主頁網址或 Reels 分頁網址，例如 `https://www.instagram.com/duolastudy/reels/`。
4. 按「🚀 開始下載」。
5. GUI 會先顯示主頁掃描狀態，展開完成後會自動新增多筆 `/p/` 或 `/reel/` 任務。

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

若第一次使用 IG Parser，請先在 GUI 點「🌐 IG Parser 登入/初始化」，登入 Instagram 並完成必要的年齡或帳號確認。完成後即可回到下載器批次執行，不需要手動匯出 cookies.txt。

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

這個 Profile 會保留 Facebook 登入、雙重驗證、保持登入與信任裝置狀態。第一次使用請先在 GUI 點「🌐 FB Parser 登入/初始化」完成登入。完成後，FB 一般貼文、多圖相簿、大相簿、share/r 與 Reel fallback 都會使用此 Profile。

`cookies.txt` 只保留為 legacy / emergency fallback，不再是主要推薦流程。

### 多圖貼文保護

Facebook 多圖貼文會使用 Playwright viewer-intercept 收集候選媒體，並比對原生 Grid / Photo link 數量。

保護原則：

- 多圖缺圖會回傳 `RETRY`，不會假裝成功
- 多圖貼文若抓取失敗，禁止退化成單一影片檔結案
- 若圖片與影片候選混在一起，圖片貼文優先保留圖片，避免推薦影片污染
- 若多檔輸出仍是 fallback title `Facebook_Post`，會阻擋高風險輸出，避免錯誤候選檔搬成正式結果

---

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
   - 「🌐 IG Parser 登入/初始化」
   - 「🌐 FB Parser 登入/初始化」
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
IG carousel detected total_count=
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

請先確認：

1. cookies.txt 是否最新
2. cookies.txt 是否同一個帳號
3. cookies 是否包含 Instagram / Facebook 完整 domain
4. 是否遇到平台風控或 rate limit
5. 是否需要重新播放影片 3～5 秒後再抓

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

### v11.33 IG Profile/Reels Auto Expand Fix

- 修正 `https://www.instagram.com/<username>/reels/` 會被誤當成單一 Reel 任務的問題
- IG 主頁掃描會同時掃描主頁與 Reels 分頁，避免只抓網格預覽縮圖
- Reels 分頁會展開成真正的 `/reel/<shortcode>/` 子任務，再沿用原本單篇 Reel 下載與 title/caption 分類規則
- 保留 v11.32 的 queue 展開架構，不把整個帳號下載塞進單一任務

### v11.32 IG Profile Auto Expand

- 新增 Instagram 主頁 URL 偵測，例如 `https://www.instagram.com/bubu.dudu_hk/`
- 使用 IG Parser persistent Chrome Profile 掃描主頁貼文
- 自動收集 `/p/` 與 `/reel/` shortcode
- 自動展開成單篇 post / Reel 任務加入 GUI queue
- 每篇子任務仍使用原本穩定的單篇 IG 下載流程
- 保留原本 post title / caption 輸出分類規則
- 保留 processed-link 去重與 queue 去重
- 保留 v11.26 restricted Carousel routing、shortcode lock、total_count validation、scoped network fill、媒體順序與 WEBP/JPEG 檔頭處理
- 不抓 Stories，避免與貼文下載流程混線
- 不影響 Facebook 下載流程


### v11.27 Parser Login Profiles

- 新增 IG Parser / FB Parser 專用 Chrome Persistent Profile 登入模式
- GUI 新增「🌐 FB Parser 登入/初始化」
- GUI 將 IG Parser 改為「🌐 IG Parser 登入/初始化」
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


### v11.34 IG Reels Click-Scan + Profile Folder Output

- 修正 `https://www.instagram.com/<username>/reels/` 在部分 IG 版面中掃描不到 `a[href]` 的問題。
- 主頁 / Reels 頁掃描會先嘗試 DOM / HTML / performance URL 擷取；若仍為 0，會用 visible tile click-probe 點開格子，只收集真正的 `/p/<shortcode>/` 與 `/reel/<shortcode>/`。
- 不再把 Reels grid 預覽縮圖當成正式下載結果。
- 主頁展開後的每一篇仍走原本穩定單篇下載流程，所以 Reel 會下載真正影片 `.mp4`，圖文 / Carousel 仍保留原本完整數量與順序保護。
- 從主頁 / Reels 頁展開出的子任務會輸出到 `downloads/<username>/`，例如 `downloads/duolastudy/`。
- 子任務檔名 / 資料夾名稱仍沿用單篇貼文 title / caption，例如 `找工作，什麼樣的公司不能去？` 或長文案標題。
- 保留 `processed_links.log` 的 canonical URL 去重，不為了 profile folder 改寫 post URL。

