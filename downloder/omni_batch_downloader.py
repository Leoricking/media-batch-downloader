import instaloader
import yt_dlp
import os
import re
import time
import random
import shutil
import getpass
import http.cookiejar
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
CHECKPOINT_FILE = "processed_links.log"
FAILED_LOG_FILE = "failed_links.log"
RETRY_NEEDED_FILE = "retry_needed.txt"
UNAVAILABLE_FILE = "unavailable_links.txt"
COOKIES_FILE = "cookies.txt"

if not os.path.exists(BASE_DIR): os.makedirs(BASE_DIR)

class InvalidPageError(Exception):
    """頁面已失效或已刪除，不應加入重試清單"""
    pass

# --- 全域登入狀態 ---
is_logged_in = False
logged_in_username = ""

def export_cookies_for_ytdlp():
    """將 instaloader Session Cookies 導出為 Netscape 格式，供 yt-dlp 備援引擎使用"""
    try:
        cj = http.cookiejar.MozillaCookieJar(COOKIES_FILE)
        for cookie in L.context._session.cookies:
            cj.set_cookie(cookie)
        cj.save(ignore_discard=True, ignore_expires=True)
        print(f"[Cookie] 已導出 Cookies 至 {COOKIES_FILE}")
    except Exception as e:
        print(f"[Cookie] 導出失敗，yt-dlp 備援可能無法取得身份: {e}")

def try_login_flow():
    """強制互動式登入：每次執行都重新登入，不自動載入 Session 檔案"""
    global is_logged_in, logged_in_username
    username = input("請輸入要登入的 IG 帳號 (留空則以匿名模式執行): ").strip()

    if not username:
        print("[Session] 以匿名模式執行。")
        return

    # 每次登入前清除舊有 Session，確保全新狀態
    L.context._session.cookies.clear()

    password = getpass.getpass("請輸入 IG 密碼: ")
    try:
        L.login(username, password)
    except instaloader.exceptions.TwoFactorAuthRequiredException:
        code = input("[驗證] IG 要求驗證碼，請輸入收到的 6 位數代碼: ").strip()
        time.sleep(5)  # 執行前緩衝，避免請求過快
        try:
            L.two_factor_login(code)
            time.sleep(3)  # 執行後緩衝，確保 IG 伺服器 Session 同步完成
        except Exception as e:
            print(f"[失敗] 驗證碼錯誤或已過期，清除 Cookie 並結束登入嘗試。")
            L.context._session.cookies.clear()
            return
    except Exception as e:
        print(f"[Session] 登入失敗: {e}")
        return

    is_logged_in = True
    logged_in_username = username
    L.save_session_to_file()
    print("[Session] 登入成功！")
    export_cookies_for_ytdlp()

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

def force_clear_temp():
    """強制排空暫存目錄，防止多檔下載失敗後的殘留"""
    if os.path.exists(TEMP_DL_DIR):
        shutil.rmtree(TEMP_DL_DIR)
    os.makedirs(TEMP_DL_DIR)

def log_failed(url, reason):
    """將無法下載的連結寫入 failed_links.log"""
    with open(FAILED_LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{url}  [{reason}]\n")
    print(f"  [!] 已記錄至 failed_links.log：{reason}")

def log_unavailable(url):
    """將確定失效/已刪除的純 URL 寫入 unavailable_links.txt（去重）"""
    if os.path.exists(UNAVAILABLE_FILE):
        with open(UNAVAILABLE_FILE, 'r', encoding='utf-8') as f:
            if url in {line.strip() for line in f if line.strip()}:
                return
    with open(UNAVAILABLE_FILE, 'a', encoding='utf-8') as f:
        f.write(url + '\n')

def log_retry_needed(url):
    """將純 URL 寫入 retry_needed.txt（去重），供日後重新嘗試"""
    if os.path.exists(RETRY_NEEDED_FILE):
        with open(RETRY_NEEDED_FILE, 'r', encoding='utf-8') as f:
            existing = {line.strip() for line in f if line.strip()}
        if url in existing:
            return
    with open(RETRY_NEEDED_FILE, 'a', encoding='utf-8') as f:
        f.write(url + '\n')

def ig_fallback_ytdlp(url, title):
    """yt-dlp 備援下載（instaloader 失敗時使用）
    - 若 is_logged_in，固定加入 cookiefile 繞過權限牆
    - 最終檔名強制套用 clean_text 規範
    - 若遇到「內容不公開」權限錯誤，寫入 failed_links.log 並拋出 PermissionError
    """
    print(f"  [備援] 切換至 yt-dlp 下載...")

    ydl_opts_base = {
        'format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'overwrites': True,
        'headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Referer': 'https://www.instagram.com/',
        },
    }
    if is_logged_in:
        ydl_opts_base['cookiefile'] = os.path.abspath(COOKIES_FILE)

    # 先取得 info 以確認標題與權限
    try:
        with yt_dlp.YoutubeDL({**ydl_opts_base, 'skip_download': True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        err_str = str(e)
        if ("empty media response" in err_str or
                "This content isn't available" in err_str or
                "available to everyone" in err_str):
            log_unavailable(url)
            log_failed(url, "[頁面已失效] 內容已刪除或不可存取")
            raise InvalidPageError("頁面已失效")
        raise

    # 若 instaloader 未能取得標題，改用 yt-dlp 的描述欄位（仍套用 clean_text）
    if title == "Untitled":
        title = clean_text(info.get('description') or info.get('title'))

    final_path = os.path.join(BASE_DIR, f"{title}.mp4")
    ydl_opts_dl = {**ydl_opts_base, 'outtmpl': final_path}

    with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
        ydl.download([url])

    print(f"  -> yt-dlp 備援完成: {title}.mp4")
    return final_path

def handle_ig(url):
    """雙引擎智能切換：優先 instaloader，403 或元資料失敗時自動切換 yt-dlp"""
    force_clear_temp()
    title = "Untitled"

    try:
        match = re.search(r"/(?:p|reels|reel)/([^/?#&]+)", url)
        if not match:
            print(f"[IG] 無法解析 URL: {url}")
            return False
        shortcode = match.group(1)

        post = instaloader.Post.from_shortcode(L.context, shortcode)
        title = clean_text(post.caption)

        print(f"[IG] 正在下載: {title}")
        L.download_post(post, target=TEMP_DL_DIR)

        all_files = sorted([f for f in os.listdir(TEMP_DL_DIR)])
        media_files = [f for f in all_files if os.path.splitext(f)[1].lower() in ['.jpg', '.jpeg', '.png', '.mp4']]

        if len(media_files) == 1:
            # --- 規則 A: 單一檔案 ---
            f = media_files[0]
            f_ext = os.path.splitext(f)[1].lower()
            new_ext = '.mp4' if f_ext == '.mp4' else '.jpg'
            final_path = os.path.join(BASE_DIR, f"{title}{new_ext}")

            if os.path.exists(final_path): os.remove(final_path)
            shutil.move(os.path.join(TEMP_DL_DIR, f), final_path)

            # 確認檔案確實寫入磁碟
            if not os.path.exists(final_path):
                raise IOError(f"檔案移動後找不到: {final_path}")
            print(f"  -> 單檔完成: {title}{new_ext}")
        else:
            # --- 規則 B: 多個檔案，建立目錄 ---
            final_target_path = os.path.join(BASE_DIR, title)
            if os.path.exists(final_target_path): shutil.rmtree(final_target_path)
            os.makedirs(final_target_path)

            for i, f in enumerate(media_files, 1):
                f_ext = os.path.splitext(f)[1].lower()
                new_ext = '.mp4' if f_ext == '.mp4' else '.jpg'
                dest = os.path.join(final_target_path, f"{i}{new_ext}")
                shutil.move(os.path.join(TEMP_DL_DIR, f), dest)
                if not os.path.exists(dest):
                    raise IOError(f"檔案移動後找不到: {dest}")
            print(f"  -> 多檔目錄完成: {title}/")

        shutil.rmtree(TEMP_DL_DIR)
        return True

    except instaloader.exceptions.PrivateProfileNotFollowedException:
        print("[權限受限] 此 Reels 僅限特定觀眾，已跳過。")
        log_failed(url, "[權限受限] 此 Reels 僅限特定觀眾")
        shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
        return False

    except Exception as e:
        err_str = str(e)
        # 401 / 流量限制 → 直接加入補考清單，不嘗試備援引擎
        if "401" in err_str or "please wait a few minutes" in err_str.lower():
            print("[流量限制] IP 遭暫時封鎖，加入補考清單。")
            log_retry_needed(url)
            shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
            return False
        # 403 / GraphQL 元資料失敗 → 切換至 yt-dlp
        if "403" in err_str or "Fetching Post metadata failed" in err_str or "login" in err_str.lower():
            print(f"  [!] instaloader 受阻 ({err_str[:80]})，切換引擎...")
            try:
                force_clear_temp()
                final_path = ig_fallback_ytdlp(url, title)
                shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
                # 確認備援檔案確實寫入磁碟
                if not os.path.exists(final_path):
                    raise IOError(f"備援檔案未找到: {final_path}")
                return True
            except InvalidPageError:
                # 已記錄至 failed_links.log，不加入重試清單
                shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
                return "invalid"
            except Exception as e2:
                print(f"  [!] yt-dlp 備援也失敗: {e2}")
                shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
                return False
        else:
            print(f"IG 下載出錯 {url}: {e}")
            shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
            return False

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

        # 確認檔案確實寫入磁碟
        if not os.path.exists(final_path):
            raise IOError(f"檔案未找到: {final_path}")
        return True

    except Exception as e:
        print(f"FB 下載出錯 {url}: {e}")
        return False

def load_checkpoint():
    """讀取斷點紀錄，回傳已成功處理的 URL 集合"""
    if not os.path.exists(CHECKPOINT_FILE):
        return set()
    with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip())

def save_checkpoint(url):
    """將成功的 URL 追加寫入斷點紀錄"""
    with open(CHECKPOINT_FILE, 'a', encoding='utf-8') as f:
        f.write(url + '\n')
        f.flush()
        os.fsync(f.fileno())

def main():
    try_login_flow()

    input_file = os.path.join("..", "pre-processing", "output", "download_link.txt")
    if not os.path.exists(input_file):
        print(f"找不到輸入檔: {input_file}")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]

    processed = load_checkpoint()
    skipped = sum(1 for u in urls if u in processed)
    if skipped:
        print(f"[斷點] 已載入紀錄，略過 {skipped}/{len(urls)} 個已完成連結。")

    fail_streak = 0
    base_fail_sleep = 60
    count_success = 0
    count_retry = 0
    count_unavailable = 0

    for i, url in enumerate(urls):
        # 已完成的連結不發起任何網路請求，直接跳過
        if url in processed:
            continue

        print(f"\n進度: [{i+1}/{len(urls)}]")

        if "instagram.com" in url:
            result = handle_ig(url)
            if result == "invalid":
                print("[失效] 連結已損毀或刪除，跳過紀錄。")
                count_unavailable += 1
                continue
            elif not result:
                log_failed(url, "[下載失敗] instaloader 與 yt-dlp 備援皆失敗")
                log_retry_needed(url)
                print("[跳過] 連結已更新至 retry_needed.txt，準備下一個進度。")
                count_retry += 1
                continue
            success = result
        elif "facebook.com" in url or "fb.watch" in url:
            success = handle_fb(url)
        else:
            continue

        if success:
            save_checkpoint(url)
            processed.add(url)
            fail_streak = 0
            count_success += 1
            time.sleep(random.uniform(10, 20))
        else:
            fail_streak += 1
            sleep_time = min(base_fail_sleep * (2 ** (fail_streak - 1)), 300)
            print(f"  偵測到連線壓力，增加冷卻時間... ({sleep_time}s)")
            time.sleep(sleep_time)

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("下載馬拉松任務完成！")
    print(f"✅ 成功下載：{count_success} 筆")
    print(f"⚠️ 需要補考：{count_retry} 筆 (請見 retry_needed.txt)")
    print(f"❌ 連結失效：{count_unavailable} 筆 (請見 unavailable_links.txt)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━")

if __name__ == "__main__":
    main()
