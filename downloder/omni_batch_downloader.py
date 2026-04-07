import instaloader
import yt_dlp
import os
import re
import time
import random
import shutil
from opencc import OpenCC

# --- 初始化設定 ---
cc = OpenCC('s2t')
L = instaloader.Instaloader(
    download_videos=True,
    download_video_thumbnails=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern=""
)

BASE_DIR = "downloads"
TEMP_DL_DIR = "temp_insta_dl"

if not os.path.exists(BASE_DIR): os.makedirs(BASE_DIR)

def clean_text(text):
    """清理檔名：簡轉繁、關鍵字替換 (一二->Bubu, 布布->Dudu)、非法字元移除"""
    if not text: return "Untitled"
    
    # 1. 簡轉繁
    t = cc.convert(text.split('\n')[0].strip())
    
    # 2. 關鍵字替換 (串接後 "一二布布" 會變成 "Bubu Dudu")
    t = t.replace("一二", "Bubu ").replace("布布", "Dudu")
    
    # 3. 移除非法字元並限制長度
    t = re.sub(r'[\\/*?:"<>|]', '', t)
    return t[:60].strip()

def handle_ig(url):
    try:
        match = re.search(r"/(?:p|reels|reel)/([^/?#&]+)", url)
        if not match: return
        shortcode = match.group(1)
        
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        title = clean_text(post.caption)
        
        # 準備暫存區下載
        if os.path.exists(TEMP_DL_DIR): shutil.rmtree(TEMP_DL_DIR)
        os.makedirs(TEMP_DL_DIR)
        
        print(f"[IG] 正在下載: {title}")
        L.download_post(post, target=TEMP_DL_DIR)
        
        # 獲取下載後的媒體檔案
        all_files = sorted([f for f in os.listdir(TEMP_DL_DIR)])
        media_files = [f for f in all_files if os.path.splitext(f)[1].lower() in ['.jpg', '.jpeg', '.png', '.mp4']]
        
        if len(media_files) == 1:
            # --- 規則 A: 單一檔案，直接存放在 downloads ---
            f = media_files[0]
            f_ext = os.path.splitext(f)[1].lower()
            new_ext = '.mp4' if f_ext == '.mp4' else '.jpg'
            final_path = os.path.join(BASE_DIR, f"{title}{new_ext}")
            
            if os.path.exists(final_path): os.remove(final_path)
            shutil.move(os.path.join(TEMP_DL_DIR, f), final_path)
            print(f"  -> 單檔完成: {title}{new_ext}")
        else:
            # --- 規則 B: 多個檔案，建立目錄 ---
            final_target_path = os.path.join(BASE_DIR, title)
            if os.path.exists(final_target_path): shutil.rmtree(final_target_path)
            os.makedirs(final_target_path)
            
            for i, f in enumerate(media_files, 1):
                f_ext = os.path.splitext(f)[1].lower()
                new_ext = '.mp4' if f_ext == '.mp4' else '.jpg'
                shutil.move(os.path.join(TEMP_DL_DIR, f), os.path.join(final_target_path, f"{i}{new_ext}"))
            print(f"  -> 多檔目錄完成: {title}/")

        shutil.rmtree(TEMP_DL_DIR)

    except Exception as e:
        print(f"IG 下載出錯 {url}: {e}")

def handle_fb(url):
    try:
        ydl_opts_info = {'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            title = clean_text(info.get('description') or info.get('title'))

        print(f"[FB] 正在下載單檔影片: {title}")
        final_path = os.path.join(BASE_DIR, f"{title}.mp4")
        
        ydl_opts_dl = {
            'outtmpl': final_path,
            'format': 'mp4',
            'quiet': True,
            'no_warnings': True,
            'overwrites': True
        }
        with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
            ydl.download([url])

    except Exception as e:
        print(f"FB 下載出錯 {url}: {e}")

def main():
    input_file = os.path.join("..", "pre-processing", "output", "download_link.txt")
    if not os.path.exists(input_file):
        print(f"找不到輸入檔: {input_file}")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]

    for i, url in enumerate(urls):
        print(f"\n進度: [{i+1}/{len(urls)}]")
        if "instagram.com" in url:
            handle_ig(url)
            time.sleep(random.uniform(20, 35)) 
        elif "facebook.com" in url or "fb.watch" in url:
            handle_fb(url)
            time.sleep(random.uniform(5, 10))

    print("\n所有任務已完成！")

if __name__ == "__main__":
    main()