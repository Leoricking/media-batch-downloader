import re
import os
import glob

def get_input_file():
    """自動偵測目錄下的 .txt 檔案並讓使用者選擇"""
    txt_files = glob.glob("*.txt")
    exclude_files = ["download_link.txt", "undownload_link.txt", "ig_links.txt", "fb_links.txt"]
    available_files = [f for f in txt_files if f not in exclude_files]

    if not available_files:
        print("目錄下找不到任何 .txt 檔案。")
        return None
    
    if len(available_files) == 1:
        print(f"自動偵測到檔案: {available_files[0]}")
        return available_files[0]

    print("請選擇要處理的檔案：")
    for i, filename in enumerate(available_files):
        print(f"[{i}] {filename}")
    
    try:
        choice = int(input("輸入編號: "))
        return available_files[choice]
    except:
        print("輸入無效。")
        return None

def sort_links():
    input_file = get_input_file()
    if not input_file:
        return

    output_dir = "output"
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    download_links = []
    undownload_links = []

    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取所有網址並去重
    urls = re.findall(r'https?://[^\s,，。]+', content)
    urls = list(dict.fromkeys(urls))

    for url in urls:
        clean_url = url.strip().strip('，').strip('。')
        
        # --- 精確過濾邏輯 ---
        
        # 1. Instagram 過濾：僅保留貼文 (/p/) 或連續短片 (/reel/)
        is_ig_media = 'instagram.com' in clean_url and ('/p/' in clean_url or '/reel' in clean_url)
        
        # 2. Facebook 過濾：
        #    a. 判斷是否為 FB 相關網域
        #    b. 排除主頁，僅保留具備特定 ID 路徑的連結 (videos, reel, watch, posts, story)
        is_fb_domain = 'facebook.com' in clean_url or 'fb.watch' in clean_url
        is_fb_media = False
        
        if is_fb_domain:
            # 定義有效的內容關鍵字清單
            fb_content_keywords = ['/videos', '/reel', '/watch', '/posts', '/story', 'v=', 'fbid=']
            if any(k in clean_url for k in fb_content_keywords):
                is_fb_media = True

        # --- 分流結果 ---
        if is_ig_media or is_fb_media:
            download_links.append(clean_url)
        else:
            undownload_links.append(clean_url)

    # --- 寫入檔案 (使用 "w" 模式強制覆蓋舊檔) ---
    download_path = os.path.join(output_dir, "download_link.txt")
    undownload_path = os.path.join(output_dir, "undownload_link.txt")

    with open(download_path, "w", encoding="utf-8") as f:
        f.write("\n".join(download_links))
        
    with open(undownload_path, "w", encoding="utf-8") as f:
        f.write("\n".join(undownload_links))
    
    print("-" * 45)
    print(f"處理檔案: {input_file}")
    print(f"結果已儲存至 '{output_dir}' (已自動覆蓋舊檔)：")
    print(f"✅ 判定可下載 (貼文/影片): {len(download_links)} 筆")
    print(f"❌ 判定不可下載 (個人主頁/其他): {len(undownload_links)} 筆")
    print("-" * 45)

if __name__ == "__main__":
    sort_links()