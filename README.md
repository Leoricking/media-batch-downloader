# 🚀 Media Batch Downloader

> Current documentation: GUI v12.24 + queue_manager v12.12 + Instagram v12.40 + Facebook v15.4

Media Batch Downloader 是一套用於 Instagram / Facebook 連結預處理與批次下載的 Windows 桌面工具。

核心原則：

> **不假成功、不漏圖、不抓其他貼文、不把縮圖或封面誤判成正式媒體。**

目前版本支援大量連結匯入、URL 預處理、GUI 任務佇列、狀態分類、斷點續跑、登入 Profile、Post Account / Post Title、完整性驗證、失敗清單整理，以及 Instagram / Facebook 多引擎 recovery。

---

## 目前版本

| 元件 | 版本 | 重點 |
|---|---:|---|
| `downloader_GUI/main.py` | v12.24 | GUI、NO_ACCESS、篩選、右鍵複製、快捷開檔 |
| `downloader_GUI/queue_manager.py` | v12.12 | NO_ACCESS 狀態、任務紀錄、checkpoint、失敗分類 |
| `downloader_GUI/downloader/instagram.py` | v12.40 | authenticated transport recovery、listener cleanup、v12.39 transport recovery |
| `downloader_GUI/downloader/facebook.py` | v15.4 | NO_ACCESS detection、exact-post recovery、exact-fbid、strict completeness |
| `downloader_GUI/worker.py` | 現行穩定版 | 任務重試、冷卻、IG profile batch |

---

# 核心特色

## Instagram

- 支援一般 Post、Carousel、多圖、多影片與 Reels。
- 支援 `?img_index=N` 分享網址。
- 支援 Instagram 帳號主頁批次展開。
- 支援 IG Parser 專用 Chrome Persistent Profile。
- 支援登入、2FA、年齡限制、受眾限制與 authenticated fallback。
- 下載前預取 Post Account / Post Title。
- Structured media 必須驗證 target shortcode ownership。
- Carousel 必須驗證 expected / written / temp files 數量。
- Visual fallback 可偵測 pagination dot count。
- 缺少 slide 時可使用精準 `?img_index=N` 補抓。
- 不允許從推薦貼文或其他 shortcode 補數量。
- 圖片會拒絕 320 / 480 / 640 等低解析度或裁切縮圖。
- Reel / Video 必須通過真實 MP4 與完整性驗證。
- fragmented MP4 可使用 Range rebuild / fragment assembly recovery。
- Browser network cache 可直接寫入媒體。
- v12.40：exact media 已確認但 headless CDN 只剩 640px 或高解析 URL HTTP 403 時，改用已登入 IG Parser Profile recovery。
- v12.40：關閉 Playwright context 前會清理 response listener，降低 `CancelledError` / `TargetClosedError`。

## Facebook

- 支援 `facebook.com/share/...`、`share/p/...`、`share/r/...`、`share/v/...`、Reel、Watch、Photo、Story / post permalink、多圖 gallery。
- 使用 FB Parser 專用 Chrome Persistent Profile。
- Share URL 會解析成 canonical post identity。
- 支援 exact post / exact photo / exact fbid / PCB identity。
- 支援 exact-fbid serialized JSON recovery。
- 防止 Facebook SPA 停在上一張照片造成錯圖。
- 多圖張數必須完整，例如 `5/5 → SUCCESS`、`4/5 → RETRY`。
- 不允許以推薦貼文、背景圖片、鄰近 post 補數量。
- zero-evidence canonical post 使用 bounded recovery，避免單筆卡十幾分鐘。
- v15.3+ 支援 exact-post structured zero-evidence recovery。
- v15.4 新增 `NO_ACCESS`：內容存在，但目前 FB Parser 登入帳號沒有觀看權限。
- Reel 保留 video integrity gate，不會把封面圖當影片成功。

---

# 狀態分類

| 狀態 | 說明 | 是否適合自動重試 |
|---|---|---|
| `SUCCESS` | 已成功輸出並驗證媒體 | 否 |
| `FAILED` | 一般下載失敗 | 視情況 |
| `BLOCKED` | 登入、checkpoint、challenge、平台阻擋 | 通常先處理登入問題 |
| `NO_ACCESS` | 內容存在，但目前登入帳號沒有觀看權限 | **否，應改用有權限帳號** |
| `MISSING` | 頁面不存在、404、貼文已移除 | 否 |
| `UNAVAILABLE` | 內容不可用 | 否 |
| `RETRY` | timeout、429、rate limit、暫時性媒體取得失敗 | 是 |
| `DOWNLOADING` | 正在下載 | - |
| `PENDING` | 等待處理 | - |

## NO_ACCESS 判定

`NO_ACCESS` 代表：

> 貼文存在，但 FB Parser 目前登入的 Facebook 帳號不在可觀看受眾中。

程式不會只看到「目前無法查看此內容」就直接判定 `NO_ACCESS`。需具備足夠證據：

```text
canonical post identity 已確認
+
authenticated FB Parser session
+
明確受眾 / 分享對象限制提示
+
exact media evidence = 0
```

因此：

- 網路 / CDN 暫時失敗 → `RETRY`
- 登入失效 / checkpoint / challenge → `BLOCKED`
- 404 / 真正不存在 → `MISSING` / `UNAVAILABLE`
- 內容存在但目前帳號無權限 → `NO_ACCESS`

---

# GUI 功能

目前任務表格：

```text
URL | Post Account | Post Title | 狀態 | Retry
```

狀態篩選：

```text
全部
SUCCESS
FAILED
BLOCKED
NO ACCESS
MISSING
RETRY
DOWNLOADING
```

## 上方快捷按鈕

目前保留：

```text
🚀 開始下載
🧹 清空
📂 匯入txt
🧩 預處理
📥 載入清單
📁 下載資料夾
📂 預處理output
📝 link.txt
📄 download_link.txt
🧼 清除紀錄
🌐 IG Parser
🌐 FB Parser
```

### 📝 link.txt

直接開啟：

```text
pre-processing/link.txt
```

### 📄 download_link.txt

直接開啟：

```text
pre-processing/output/download_link.txt
```

與 `📥 載入清單` 不同：

- `📄 download_link.txt`：直接用文字編輯器開檔
- `📥 載入清單`：把 download_link.txt 載入 GUI Queue

---

# 右鍵選單

目前支援：

```text
複製連結
複製選取列狀態
查看完整連結
複製目前篩選 URL
複製目前篩選 Post Title

複製 FAILED URL
複製 BLOCKED URL
複製 NO_ACCESS URL
複製 MISSING URL
複製 RETRY URL
複製全部未成功 URL

複製 FAILED Post Title
複製 NO_ACCESS Post Title
複製 MISSING Post Title
複製 RETRY Post Title
複製全部未成功 Post Title
```

「全部未成功」包含：

```text
FAILED
BLOCKED
NO_ACCESS
MISSING
UNAVAILABLE
RETRY
```

---

# 專案結構

```text
media-batch-downloader/
├─ README.md
├─ downloader_GUI/
│  ├─ main.py
│  ├─ worker.py
│  ├─ queue_manager.py
│  ├─ build.bat
│  ├─ run.bat
│  ├─ install.txt
│  ├─ cookies.txt
│  ├─ accounts.json
│  ├─ downloader/
│  │  ├─ instagram.py
│  │  ├─ facebook.py
│  │  └─ fb_contracts.py
│  ├─ data/
│  │  ├─ chrome_ig_parser/
│  │  ├─ playwright_fb_profile/
│  │  ├─ processed_links.log
│  │  ├─ failed_links.log
│  │  ├─ retry_needed.txt
│  │  ├─ unavailable_links.txt
│  │  ├─ failed_history.log
│  │  └─ tasks_snapshot.tsv
│  ├─ downloads/
│  └─ post/
└─ pre-processing/
   ├─ link.txt
   ├─ link_sorter.py
   └─ output/
      ├─ download_link.txt
      └─ undownload_link.txt
```

---

# 安裝需求

建議：

- Windows 10 / 11
- Python 3.10+
- Chromium / Chrome
- Playwright

安裝：

```powershell
python -m pip install --upgrade pip
python -m pip install -U instaloader yt-dlp opencc-python-reimplemented requests playwright pycryptodome keyring
python -m pip install -U tkinterdnd2 pyinstaller pillow
python -m playwright install chromium
```

---

# 快速啟動

```powershell
cd downloader_GUI
python .\main.py
```

或：

```powershell
run.bat
```

---

# Parser 專用登入 Profile

## Instagram

GUI：`🌐 IG Parser`

Profile：

```text
downloader_GUI/data/chrome_ig_parser
```

第一次使用：登入 Instagram、完成 2FA / checkpoint / 年齡確認，確認目標貼文可以正常觀看後關閉 Parser Chrome，再回 GUI 執行下載。

## Facebook

GUI：`🌐 FB Parser`

Profile：

```text
downloader_GUI/data/playwright_fb_profile
```

第一次使用：登入 Facebook、完成 2FA、保持登入，手動確認目標貼文可觀看後關閉 Parser Chrome，再回 GUI 執行下載。

如果某篇貼文在 FB Parser 中就顯示受眾限制，程式可判定為 `NO_ACCESS`。

> 下載時建議先關閉手動開啟的 Parser Chrome 視窗，避免 Profile lock。

---

# cookies.txt

`cookies.txt` 目前是 Legacy / Emergency Fallback。主要登入方式為 IG / FB Parser Persistent Profile。

不要把以下內容 commit：

```text
downloader_GUI/data/chrome_ig_parser/
downloader_GUI/data/playwright_fb_profile/
downloader_GUI/cookies.txt
downloader_GUI/accounts.json
```

---

# Instagram 下載流程

```text
URL
→ shortcode identity
→ Post Account / Post Title prefetch
→ Instaloader
→ headless Playwright
→ exact visual / structured candidates
→ quality gate
→ authenticated IG Parser Profile fallback
→ media validation
→ move_files()
→ SUCCESS
```

如果 Instaloader 出現：

```text
401 Unauthorized
Please wait a few minutes before you try again
```

不代表一定失敗。v12.40 仍會繼續 Browser / Persistent Profile recovery。

例如：

```text
Instaloader 401
→ headless 找到 640x640
→ quality gate 拒絕
→ 高解析 CDN HTTP 403
→ authenticated IG Parser Profile
→ browser session/network cache 取得圖片
→ SUCCESS
```

## Carousel

- shortcode 必須完全一致。
- 不可抓推薦貼文。
- 不可漏第一張或忽略 `img_index`。
- dot count 可作 expected count。
- `expected=5, output=4` 不能 SUCCESS。
- 不使用低解析度或重複內容補張數。

## Reel

必須是真實、完整 MP4。封面 JPG、fragment-only MP4、推薦 Reel 都不得假 SUCCESS。

---

# Facebook 下載流程

## Share URL

```text
share URL
→ authenticated redirect
→ canonical post
→ post identity
→ metadata
→ exact scope
→ structured payload
→ exact photo / video identity
→ download
→ completeness audit
```

## 多圖 Gallery

核心規則：

```text
expected == unique verified output
```

例如：

```text
target=5
output=5 → SUCCESS
output=4 → RETRY
```

不接受推薦貼文、背景圖片、avatar、comment 圖片、鄰近貼文、重複內容或不屬於 exact post identity 的 media。

## Exact fbid

v15.2+ 不只相信 SPA 畫面目前 slide，而是從 exact fbid + Facebook serialized structured payload 找該 photo identity 自己的媒體 URL。

## Zero-Evidence Recovery

某些 share URL 會出現：

```text
canonical post 成功
scoped_media=0
grid=0
links=0
plus=0
```

v15.1+ 改成 bounded recovery，v15.3+ 再加入 exact-post structured recovery。

若最後頁面明確證明是受眾權限限制，v15.4 會標記 `NO_ACCESS`，而不是 `RETRY`。

---

# 完整性與防誤判原則

1. **不假 SUCCESS**：expected 無法完整證明就 RETRY。
2. **不抓其他貼文**：recovery 必須綁 target shortcode / canonical post id / exact fbid / PCB / media identity。
3. **不拿縮圖充正式圖片**。
4. **Reel 不拿封面充影片**。
5. **不因效能優化降低完整性**：只能減少重複 scan / bounded retry / cache identity / exact target recovery。

---

# Queue / Checkpoint

成功任務寫入：

```text
data/processed_links.log
```

Instagram Profile URL 本身是展開器，不會永久 checkpoint；只有真正完成的 child Post / Reel 會寫入 checkpoint。

---

# Instagram 主頁批次下載

支援：

```text
https://www.instagram.com/<username>/
https://www.instagram.com/<username>/reels/
```

流程：掃描主頁 → 鎖定 owner.username → 收集 shortcode → 去重 → 插入目前任務後方 → 逐篇下載。

中途停止後，已完成的保留在 processed_links.log，未完成的下次掃描後補下載。

---

# 失敗紀錄

```text
data/failed_links.log
data/retry_needed.txt
data/unavailable_links.txt
data/failed_history.log
data/tasks_snapshot.tsv
```

| 檔案 | 用途 |
|---|---|
| `failed_links.log` | FAILED / BLOCKED / NO_ACCESS / MISSING / RETRY 等 |
| `retry_needed.txt` | 暫時性失敗，之後可重新嘗試 |
| `unavailable_links.txt` | MISSING / UNAVAILABLE |
| `failed_history.log` | 歷史失敗事件 |
| `tasks_snapshot.tsv` | Queue 快照 |

---

# Debug Log

## Instagram

```text
IG v12.40 pipeline active
IG structured caption resolved
IG structured account resolved
IG authenticated structured extraction complete
IG persistent profile media count=
IG filtered media count=
network harvest=
IG headless 媒體未通過品質/完整性 gate
IG 啟用已登入 Chrome Profile fallback
IG Playwright 已成功寫入
TEMP 有效檔案=
IG 第 N 個影片完整 MP4 重建完成
```

## Facebook

```text
FB v15.4 integrated pipeline active
authenticated share resolved
effective target identity
canonical post exact scope
exact-fbid serialized recovery
zero-evidence gate
zero-evidence exact-post structured recovery
NO_ACCESS
strict completeness guard
unique output media count
ordered move exact-count completion
```

---

# 常見問題

## 為什麼 Facebook 顯示 NO_ACCESS？

FB Parser 已登入，目標 canonical post 也已辨識，但目前帳號沒有觀看該貼文的受眾權限。

處理方式：

1. 點 `🌐 FB Parser`
2. 確認登入的是有權限的 Facebook 帳號
3. 手動開啟目標 URL
4. 若仍顯示「目前無法查看此內容」，代表帳號本身沒有權限
5. 換有權限帳號再下載

## NO_ACCESS 和 BLOCKED？

- `NO_ACCESS`：內容存在，但目前帳號不在可觀看受眾。
- `BLOCKED`：登入 / checkpoint / challenge / 平台阻擋。

## NO_ACCESS 和 MISSING？

- `NO_ACCESS`：內容存在，只是目前帳號看不到。
- `MISSING`：內容已刪除 / 404 / 找不到。

## Instagram 401 一定是失敗嗎？

不一定。v12.40 還會嘗試 Playwright → authenticated IG Parser Profile → browser response recovery。

## 為什麼 640x640 不直接下載？

因為 Instagram 常把 640x640 當預覽或裁切版本，程式會繼續找 structured high-res candidate / authenticated response / browser cache。

## 為什麼 Facebook 4/5 不算成功？

因為少一張就是不完整。本專案選擇 `4/5 = RETRY`，而不是假成功。

---

# 測試

```powershell
cd downloader_GUI
python -m py_compile main.py queue_manager.py worker.py downloader\instagram.py downloader\facebook.py downloader\fb_contracts.py
python .\main.py
```

---

# 版本紀錄

## 2026-11 Current Integrated Release

### GUI v12.24

- 新增 `NO ACCESS` 篩選。
- 新增 NO_ACCESS URL / Post Title 右鍵複製。
- 「全部未成功」包含 NO_ACCESS。
- 保留 `📝 link.txt`、`📄 download_link.txt`、Post Account、Post Title、FAILED / MISSING / RETRY copy、IG Parser、FB Parser。
- downloader 修正不再任意更動 UI 配置。

### queue_manager v12.12

- 新增 `NO_ACCESS` failure / terminal status。
- `NO_ACCESS` 可寫入 failed log、篩選、複製與統計。
- 保留 checkpoint、RETRY、MISSING、BLOCKED、UNAVAILABLE、Profile batch queue。

### Facebook v15.4

- 新增 authenticated audience restriction / permission detection。
- 明確受眾限制改判 `NO_ACCESS`，不再一律 `RETRY`。
- 判定要求 canonical post + authenticated session + permission evidence + zero exact media。
- 保留 v15.3 exact-post structured recovery、v15.2 exact-fbid serialized recovery、strict completeness guard 與 Reel integrity。

### Facebook v15.3

- zero-evidence canonical post 新增 exact-post structured recovery。
- listener cleanup，降低 TargetClosedError / CancelledError。

### Facebook v15.2

- exact fbid 使用 serialized identity object recovery，避免 SPA URL 切換後仍取到上一張圖片。

### Facebook v15.1

- Zero-Evidence Fast Exit，避免單一 share URL 卡十幾分鐘。

### Facebook v15.0

- Exact fbid persist + bounded exact-PCB recovery。

### Instagram v12.40

- authenticated transport recovery。
- headless exact media 全被 quality / HTTP 403 擋下時改走已登入 IG Parser Profile。
- Playwright response listener cleanup。
- 保留 v12.39 transport recovery、v12.38 no-orphan synchronous task、v12.37 exact visual high-res recovery。

### GUI v12.23

- 恢復 FAILED / MISSING / RETRY / 未成功右鍵複製。
- 恢復 Post Title 名單複製。
- 修正五欄表格 status / retry index。

### GUI v12.22

- 恢復 `📝 link.txt` 與 `📄 download_link.txt`。

---

# 開發原則

1. **最新完整版本是唯一基準。**
2. 不用舊檔覆蓋最新版。
3. 正式交付不提供 patch，提供完整可覆蓋檔。
4. 修 bug 採最小必要修改。
5. 已正常功能不得因新功能消失。
6. downloader 修改不應任意改 GUI。
7. 每版至少執行 syntax compile。
8. 核心 UI / filters / shortcuts / integrity gate 做回歸檢查。
9. Facebook / Instagram 都以「不假成功」高於「強行下載成功」。

---

# Git 建議

```powershell
git status
git add -u
git add README.md
git status
git commit -m "feat: add NO_ACCESS handling and improve downloader recovery"
git push origin main
```

若 repo 根目錄還有不需要的未追蹤 JPG，`git add -u` 不會把它們自動加入 commit。
