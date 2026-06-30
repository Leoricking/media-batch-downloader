# 🚀 Media Batch Downloader

> Current documentation: v12.01 Instagram Profile Batch Resume & Checkpoint Fix

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
- Instagram 年齡／特定受眾限制貼文改用已登入 IG Parser Profile 的結構化 JSON，一次取得完整圖片／影片清單，不再依賴畫面翻頁
- 支援受限 Carousel 的 shortcode 鎖定、完整數量檢查、原始順序保留與防推薦貼文污染
- 支援下載前預取 Instagram Post Account 與 Post Title，先顯示於 GUI 再開始下載
- GUI 任務表格顯示 URL / Post Account / Post Title / 狀態 / Retry，欄寬會依視窗大小自動調整
- GUI 顯示 IG / FB Parser 登入狀態，可手動更新；Post Account 與 Post Title 標題及內容皆置中
- Instagram structured media 會寫入並驗證目標 shortcode ownership，避免跨貼文媒體污染
- Instagram 圖片會硬性拒絕 320 / 480 / 640 等低解析度縮圖、錯誤裁切與比例不符版本
- Instagram Reel / 影片必須通過真實 MP4、檔案大小與可播放完整性驗證，封面圖不得假裝影片成功
- 一般 headless 流程若只取得縮圖或品質檢查失敗，會改用已登入 IG Parser Profile 重抓；Profile 也無法確認時才回 BLOCKED
- Facebook Reel 保留既有可下載的 browser network / metadata 流程，輸出檔名優先使用 Reel caption / title
- `img_index=` 分享網址在受限貼文流程中只作為任務路由資訊；媒體清單改由結構化資料一次解析，不再逐張導航
- 結構化媒體清單中的 child 數量、實際寫入數與輸出檔案數必須一致，才會判定 SUCCESS
- 支援 IG 媒體真實檔頭判斷，WEBP 會轉成真正 JPEG，避免假 .jpg
- 支援自動簡體轉繁體
- 支援安全檔名與資料夾命名
- 支援 Instagram 主頁批次展開：背景蒐集該帳號全部 Post / Reel URL，依主頁順序插入目前任務後方逐一下載
- Instagram 主頁父任務不寫入永久 checkpoint；中途停止後重開會重新掃描主頁，只補下載尚未完成的子貼文
- 主頁展開任務依 shortcode 去重，避免 `?igsh=`、`/p/`、`/reel/` 不同格式造成重複下載
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
│  │  ├─ chrome_ig_parser/      # IG Parser 專用 Chrome Profile，用於登入、年齡／受眾限制與結構化媒體擷取
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

## Instagram 主頁批次下載與斷點續跑

支援直接貼上 Instagram 帳號主頁：

```text
https://www.instagram.com/<username>/
https://www.instagram.com/<username>/reels/
```

正式流程：

```text
主頁背景掃描
→ 攔截 GraphQL / private API / embedded JSON
→ 只接受 owner.username 等於目標帳號的 shortcode
→ 收齊 Header 宣告的 Post / Reel 數量
→ 將所有 child URL 插入主頁任務後方
→ Worker 依序下載每一篇 Post / Reel
```

安全規則：

- 主頁掃描優先使用已登入 IG Parser Profile 的 headless persistent context
- 只有登入、checkpoint、challenge、年齡或受眾確認時才開可見瀏覽器
- 不從整頁 HTML、performance resource 或推薦貼文補 URL
- 每個結構化節點都必須驗證 owner 等於目標帳號
- 主頁 Header 宣告數量與蒐集數不一致時回 `RETRY`，不假 `SUCCESS`
- 展開後的 child 任務依 shortcode 去重
- child 任務插入目前主頁任務正後方，不會排到整個佇列尾端
- 主頁批次子任務使用短冷卻；一般手動任務仍保留原本安全冷卻
- 主頁父任務只是展開器，不會寫入 `processed_links.log`

### 中途停止後重新開啟

Instagram 主頁下載到一半時，即使按「停止」並關閉程式：

```text
已完成的 Post / Reel
→ 保留在 processed_links.log
→ 下次重新掃描時自動略過

尚未完成的 Post / Reel
→ 重新插入佇列
→ 繼續下載
```

主頁 URL 本身不會永久標記為全部完成。新版啟動時也會自動清除舊版本錯誤寫入 checkpoint 的 Instagram 主頁 URL，但不會刪除任何已完成的單篇 Post / Reel 紀錄。

## Instagram 下載機制

Instagram 目前採多引擎與分流策略：

```text
一般 Post / Reel
→ Instaloader / yt-dlp / headless Playwright
→ 媒體類型、解析度、比例、完整 MP4 與 shortcode ownership 驗證
→ 全部驗證通過才輸出 SUCCESS

一般流程遇到 GraphQL 403、empty media、年齡／特定受眾限制，
或 headless 只取得低解析度縮圖、裁切圖、無效影片
→ 先改用已登入 IG Parser Profile 的 headless persistent context
→ 若需要手動登入／年齡／受眾確認，才開啟可見 Profile
→ 擷取 GraphQL / API / 頁面 JSON
→ 依 shortcode 找到目標 Post
→ 一次取得完整 carousel_media / children
→ 依原始順序下載全部圖片與影片
→ 無法確認完整性時 FAILED / RETRY，不假 SUCCESS
```

### 圖文 / Carousel 貼文

對 `/p/` 圖文貼文，系統先嘗試既有快速路徑。若 Instaloader 因 GraphQL 403、一般 Playwright 回傳 0 媒體，或貼文需要登入／年齡／特定受眾驗證，會切換到已登入 IG Parser Profile。

受限貼文的主要流程不再依賴畫面上的 Carousel 箭頭，而是：

- 使用專案內已登入的 Chrome Persistent Profile 開啟目標 shortcode
- 攔截已登入瀏覽器的 GraphQL、`/api/v1/media/<media_id>/info/` 與頁面 JSON
- 從 `carousel_media`、`edge_sidecar_to_children` 或相容 children 結構一次取得全部元素
- 每個 child 分別選擇最高品質圖片或影片 URL
- 依 API / JSON 原始順序輸出，不以畫面位置或品質分數重新排序
- 使用同一個 Playwright Browser Context 下載，沿用登入 cookies 與 trust state
- 影片保留完整 MP4、Range rebuild 與 fragment 組合驗證
- 預期數、實際寫入數與暫存檔案數不一致時拒絕成功並回 `FAILED` / `RETRY`，不會假成功

這可避免「翻頁按錯、漏頁、重複上一張影片、推薦貼文污染」等 UI 自動化問題。

### Reel / 影片

對 `/reel/` 或 `/reels/`：

- 可使用 Instaloader、yt-dlp 與 Playwright fallback，但最終輸出必須是真正可播放的完整 MP4
- 若只取得封面圖、縮圖、破碎 fragment 或過小檔案，直接拒絕，不得判定 `SUCCESS`
- Playwright 可利用 browser network cache、Range rebuild 與 fragment assembly 重建完整影片
- Reel 任務只允許輸出影片；圖片封面不會被搬成正式結果

### IG Parser 專用 Chrome Profile 與受限貼文

當 Instagram 貼文出現年齡限制、特定對象限制，或 `Instaloader` / `yt-dlp` 回傳 `empty media response` 時，下載器會啟用專案內建的 IG Parser 專用 Chrome Profile：

```text
downloader_GUI/data/chrome_ig_parser
```

設計原則：

- 不使用日常 Chrome `Default` Profile，避免與平常瀏覽器搶鎖或造成空白視窗
- 保留登入狀態、年齡確認、2FA、裝置信任與受眾確認狀態
- 每筆任務鎖定原始 shortcode，只接受目標 Post 的結構化節點
- 受限 Post / Carousel 以 authenticated structured extraction 為主要流程
- 優先查詢 `/api/v1/media/<media_id>/info/`，並同時讀取 GraphQL / Polaris / hydration JSON
- 一次取得完整 `carousel_media` / `children`，不點 Next、不按 ArrowRight、不 Swipe、不使用 pagination dot
- `img_index=` 不再作為逐張定位 API，只保留為原始任務資訊
- 圖片與影片依 children 原始順序輸出
- 若結構化資料缺少 child、URL 重複或媒體數不完整，直接回 `RETRY`
- 不會退回視覺翻頁，以避免錯 post、錯 slide 或漏下載

若第一次使用 IG Parser，請先在 GUI 點「🌐 IG Parser」，登入 Instagram 並完成必要的年齡或帳號確認。完成後即可回到下載器批次執行，不需要手動匯出 cookies.txt。

### Instagram Authenticated Structured Extraction（不翻頁）

這是目前處理年齡驗證、特定受眾限制、GraphQL 403 與一般 headless 回傳 0 媒體時的正式解法。

流程：

```text
鎖定目標 shortcode
→ 開啟已登入 IG Parser Persistent Profile
→ 完成年齡 / 受眾 / 登入確認
→ 攔截 GraphQL / API / 頁面 JSON
→ 將 shortcode 轉換為 media ID
→ 查詢 /api/v1/media/<media_id>/info/
→ 找出目標 Post 的 carousel_media / children
→ 一次取得完整圖片與影片 URL
→ 依原始 child 順序下載
→ written == expected == temp_files 才回 SUCCESS
```

媒體解析規則：

- 圖片會從 `image_versions2.candidates`、`display_resources` 等欄位選擇最高解析度版本
- 影片會從 `video_versions`、`video_resources` 等欄位選擇最高解析度／bitrate 版本
- 單張 Post 也使用同一套結構化節點解析
- 混合 Carousel 可正確保留 `image / video / video / ...` 的原始排列
- 不使用 Network Cache 的其他推薦貼文補數量
- 不使用 `img_index=N` 逐張載入
- 不進行畫面翻頁或滑鼠座標猜測
- 若無法取得完整結構化清單，回 `RETRY`，不以不可靠的視覺翻頁補救

命名規則：

```text
目標 Post 的真正 caption
→ Post Account
→ shortcode
→ Instagram_Post
```

並排除 Instagram 通用頁面文字，例如：

```text
建立帳號或登入 Instagram
Connect with friends, share what you're up to...
Share what you're into with the people who get you...
Instagram photos and videos
```

常用 Log：

```text
IG authenticated structured extraction complete:
IG structured caption resolved:
IG structured account resolved:
IG persistent profile structured media count=
carousel flipping skipped
IG Playwright 已成功寫入
TEMP 有效檔案=
IG headless 媒體未通過品質/完整性 gate
IG structured shortcode ownership
IG 第 N 個影片完整 MP4 重建完成
```

受限貼文流程中不應再出現：

```text
IG dynamic carousel walk
Next click
ArrowRight fallback
media-edge Next click
img_index recovery
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

### Instagram 媒體硬驗證與受限貼文 fallback

正式輸出前會執行下列 hard gate：

- 每個 structured media item 的 `_target_shortcode` 必須與任務 shortcode 完全一致
- 圖片長邊低於 720px、短邊過小、比例異常、與可視畫面比例明顯不符時拒絕
- 來源宣告為高解析度、但實際下載尺寸明顯縮水時拒絕
- 影片必須是真實 MP4，檔案需包含可播放所需的初始化與媒體資料
- Carousel 的 expected / written / temp_files 必須一致
- 任一元素失敗時不會用其他貼文、推薦內容或低品質候選補數量

若一般 headless 頁面能看見貼文，卻只提供 640px 裁切縮圖，下載器不會直接採用匿名 yt-dlp 的「特定受眾」訊息判定 `BLOCKED`。它會先切換到已登入 IG Parser Profile 重新取得目標 shortcode 的完整結構化媒體；只有 Profile 本身也需要登入、年齡或受眾確認且無法完成時，才回 `BLOCKED`。

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

### Facebook Reel / 分享影片

支援：

- `facebook.com/reel/...`
- `facebook.com/share/r/...`
- `facebook.com/share/v/...`
- `facebook.com/watch/...`
- `facebook.com/.../videos/...`
- `fb.watch/...`

Facebook Reel 頁面可能使用 `blob:` 作為可見 `<video>` 的播放來源，因此下載器會保留已驗證可用的 browser network / metadata / HTML 候選流程，不會因可見 `<video>` 沒有直接 MP4 URL 就錯誤回 `RETRY`。

命名規則：

```text
Reel caption / title
→ canonical Reel ID
→ Facebook_Reel
```

下載完成後會優先使用 Reel 內文標題命名，不再直接以 `/share/v/<短碼>/` 作為檔名。若沒有可信 caption，才退回 `Facebook_Reel_<實際 Reel ID>.mp4`。


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
- `Post Title`：下載前先預取 caption；若一般 headless 只能取得 Instagram 通用頁面文字，會在已登入結構化擷取後更新為真正 caption、帳號或 shortcode
- `Post Account`、`Post Title`、`狀態`、`Retry` 皆置中顯示
- GUI 會顯示 `IG：已登入 / 未登入 / Profile 使用中` 與 `FB：已登入 / 未登入 / Profile 使用中`
- 可按「更新登入狀態」重新檢查；Facebook 會優先從 Chrome cookie database 判斷登入狀態
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

## Instagram 主頁 checkpoint 規則

Instagram 主頁 URL 是批次展開器，不是實際媒體下載任務，因此不會寫入：

```text
data/processed_links.log
```

只有真正完成輸出的單篇 `/p/<shortcode>/` 或 `/reel/<shortcode>/` 會寫入 checkpoint。

這可避免：

- 主頁只下載一部分便中途停止
- 關閉程式後再次啟動
- 主頁被錯誤標示為 `SUCCESS`
- 剩餘子貼文永遠不再加入佇列

新版會在 `load_checkpoint()` 時自動移除舊版本留下的主頁 checkpoint，同時保留所有已完成 child URL。

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
IG authenticated structured extraction complete:
IG structured caption resolved:
IG structured account resolved:
IG persistent profile structured media count=
carousel flipping skipped
IG Playwright 已成功寫入
TEMP 有效檔案=
IG headless 媒體未通過品質/完整性 gate
IG structured shortcode ownership
IG 第 N 個影片完整 MP4 重建完成
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
FB Reel video candidate count=
FB Reel 主影片已下載
FB 單檔完成:
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

`BLOCKED` 通常代表登入、checkpoint、challenge、私人帳號、年齡／特定受眾或權限限制。新版在 headless 只取得低解析度縮圖時，會先改用已登入 IG Parser Profile 重抓，不會直接把匿名 yt-dlp 的限制訊息當成最終 `BLOCKED`。

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

### v12.01 Instagram Profile Batch Resume & Checkpoint Fix

- Instagram 主頁改為背景 GraphQL / API / embedded JSON URL harvest
- 只接受 `owner.username == 目標帳號` 的 Post / Reel shortcode
- 收齊主頁 Header 宣告數量後才展開任務
- 主頁 child tasks 插入目前父任務正後方，立即依序下載
- Instagram 任務依 shortcode 去重，避免 `?igsh=`、`/p/`、`/reel/` 變體重複
- 主頁批次子任務使用短冷卻，一般任務安全冷卻保持不變
- Instagram 主頁父任務不再寫入永久 checkpoint
- 啟動時自動移除舊版錯誤保存的主頁 checkpoint
- 中途停止後重開會重新掃描主頁，只補下載尚未完成的 child tasks
- 保留已完成 Post / Reel checkpoint、Facebook、Retry、Blocked、Missing 與 GUI 正常功能


### v11.93 Persistent Profile After Quality-Reject Fix

- 修正瀏覽器可查看貼文，但一般 headless 只取得 640px 裁切縮圖後被誤判 `BLOCKED`
- headless 媒體若全部因解析度、裁切、比例或完整性 hard gate 被拒絕，改用已登入 IG Parser Profile 重抓
- 先嘗試隱藏的 persistent context；只有需要登入／年齡／受眾確認時才開啟可見 Profile
- 保留低解析度縮圖拒絕、完整 Carousel、影片 MP4 驗證與跨貼文防污染
- Profile 本身也無法確認時才回 `BLOCKED`

### v11.92 Facebook Reel Title-Only Fix

- 恢復既有可正常下載 Facebook Reel 的 browser network / metadata 候選流程
- 修正 visible `<video>` 使用 `blob:` 時錯誤回 `RETRY`
- Reel 輸出檔名優先使用 caption / title
- 抓不到 caption 時才使用 canonical Reel ID
- 不再直接使用 `/share/v/<短碼>/` 作為正式檔名
- 保留 Facebook 一般貼文、多圖、相簿與完整性檢查

### v11.90 Instagram Structured Shortcode Ownership Fix

- 修正 authenticated structured extraction 已取得完整媒體，但 child item 缺少 `_target_shortcode` 而被最終 hard gate 全部拒絕
- structured media 只在精確匹配目標 shortcode 後寫入 ownership 標記
- 每個輸出元素下載前再次驗證 ownership
- 保留防抓其他貼文、完整數量與原始順序檢查

### v11.89 GUI Post Account / Post Title Restore

- 恢復 `Post Account` 與 `Post Title` 兩個欄位
- 兩欄標題及內容皆置中
- 新增 IG / FB Parser 登入狀態顯示與手動更新按鈕
- Facebook 登入狀態可讀取 Chrome cookie database，避免 Profile 使用中時誤判未登入

### v11.78.2 Authenticated Structured Extraction + Git-OK Naming Rule

- 年齡／特定受眾限制 Post 改用已登入 IG Parser Profile 的結構化資料擷取
- 新增 GraphQL、Polaris、hydration JSON 與 `/api/v1/media/<media_id>/info/` 解析
- 一次取得完整 `carousel_media` / `children`，不再依賴 Carousel 畫面翻頁
- 圖片與影片依原始 child 順序下載，支援混合 Carousel
- 保留完整 MP4、Range rebuild、fragment assembly 與媒體完整性檢查
- expected / written / temp_files 必須一致才判定 `SUCCESS`
- 結構化清單無法取得時回 `RETRY`，不退回容易誤點的視覺翻頁
- Post Title 只接受目標 shortcode 節點的真正 caption
- 命名規則恢復為：caption → Post Account → shortcode → `Instagram_Post`
- 排除 Instagram 通用頁面標題，例如登入提示與 `Connect with friends...`
- 保留普通 Post、Reels、帳號批次、Facebook 與既有 GUI 功能

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
