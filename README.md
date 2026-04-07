🛠️ 必要安裝套件
執行前請確保已安裝以下 Python 庫：
pip install instaloader yt-dlp opencc

第一階段：預處理分類 (Pre-processing)
1.準備清單：將包含大量連結的原始文字檔（例如 20260404.txt）丟入 pre-processing/ 目錄下。

2.執行分類：執行 link_sorter.py。
  i.腳本會自動過濾 Instagram/Facebook 個人主頁 等無效連結。
  ii.只保留具備特定媒體 ID（Post/Reel/Video）的有效連結。

3.輸出結果：分類後的清單會存放在 pre-processing/output/download_link.txt。

第二階段：批次下載 (Downloader)
1.啟動下載：進入 downloder/ 目錄並執行 omni_batch_downloader.py。

2.自動化處理：
  i.腳本會讀取上一步產生的有效清單。
  ii.檔名優化：自動執行簡轉繁，並將標題中的關鍵字進行轉換（「一二」→Bubu，「布布」→Dudu）。
  iii.智慧分類：單一檔案直接存於 downloads/；多圖/多片貼文則自動建立專屬資料夾。
  iv.安全機制：內建隨機延遲（20-35秒），保護帳號不被平台偵測封鎖。




  
