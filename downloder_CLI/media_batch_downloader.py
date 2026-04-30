import getpass
import http.cookiejar
import os
import random
import re
import shutil
import time
from typing import List, Tuple

import instaloader
import yt_dlp
from opencc import OpenCC


# ============================================================
# 路徑設定：固定以 downloder_CLI 目錄為基準
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

BASE_DIR = os.path.join(SCRIPT_DIR, "downloads")
TEMP_DL_DIR = os.path.join(SCRIPT_DIR, "temp_insta_dl")
DATA_DIR = os.path.join(SCRIPT_DIR, "data")

CHECKPOINT_FILE = os.path.join(DATA_DIR, "processed_links.log")
FAILED_LOG_FILE = os.path.join(DATA_DIR, "failed_links.log")
RETRY_NEEDED_FILE = os.path.join(DATA_DIR, "retry_needed.txt")
UNAVAILABLE_FILE = os.path.join(DATA_DIR, "unavailable_links.txt")
COOKIES_FILE = os.path.join(SCRIPT_DIR, "cookies.txt")

PREPROCESS_DOWNLOAD_LINK = os.path.join(
    PROJECT_ROOT,
    "pre-processing",
    "output",
    "download_link.txt",
)

os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(TEMP_DL_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# 初始化設定
# ============================================================

cc = OpenCC("s2t")

# 關鍵修正：
# 不再把 TEMP_DL_DIR 當作 download_post(target=...) 傳入。
# 改用 dirname_pattern 固定輸出到真正的 temp 目錄。
L = instaloader.Instaloader(
    download_videos=True,
    download_video_thumbnails=False,
    save_metadata=False,
    compress_json=False,
    post_metadata_txt_pattern="",
    dirname_pattern=TEMP_DL_DIR,
)


class InvalidPageError(Exception):
    """頁面已失效或已刪除，不應加入重試清單。"""


# ============================================================
# 全域登入狀態
# ============================================================

is_logged_in = False
logged_in_username = ""


# ============================================================
# 工具函式
# ============================================================

def export_cookies_for_ytdlp():
    """
    將 instaloader Session Cookies 導出為 Netscape 格式，
    供 yt-dlp 備援引擎使用。
    """
    try:
        cj = http.cookiejar.MozillaCookieJar(COOKIES_FILE)

        for cookie in L.context._session.cookies:
            cj.set_cookie(cookie)

        cj.save(ignore_discard=True, ignore_expires=True)
        print(f"[Cookie] 已導出 Cookies 至 {COOKIES_FILE}")

    except Exception as e:
        print(f"[Cookie] 導出失敗，yt-dlp 備援可能無法取得身份: {e}")


def try_login_flow():
    """
    強制互動式登入：
    每次執行都重新登入，不自動載入 Session 檔案。
    """
    global is_logged_in, logged_in_username

    username = input("請輸入要登入的 IG 帳號 (留空則以匿名模式執行): ").strip()

    if not username:
        print("[Session] 以匿名模式執行。")
        return

    L.context._session.cookies.clear()

    password = getpass.getpass("請輸入 IG 密碼: ")

    try:
        L.login(username, password)

    except instaloader.exceptions.TwoFactorAuthRequiredException:
        code = input("[驗證] IG 要求驗證碼，請輸入收到的 6 位數代碼: ").strip()
        time.sleep(5)

        try:
            L.two_factor_login(code)
            time.sleep(3)

        except Exception:
            print("[失敗] 驗證碼錯誤或已過期，清除 Cookie 並結束登入嘗試。")
            L.context._session.cookies.clear()
            return

    except Exception as e:
        print(f"[Session] 登入失敗: {e}")
        return

    is_logged_in = True
    logged_in_username = username

    try:
        L.save_session_to_file()
    except Exception as e:
        print(f"[Session] 儲存 Session 失敗，但可繼續執行: {e}")

    print("[Session] 登入成功！")
    export_cookies_for_ytdlp()


def clean_text(text):
    """
    清理檔名：
    - 簡轉繁
    - 關鍵字替換：一二 -> Bubu, 布布 -> Dudu
    - 移除 Windows 非法字元
    - 限制長度
    """
    if not text:
        return "Untitled"

    t = cc.convert(str(text).split("\n")[0].strip())

    t = t.replace("一二", "Bubu ")
    t = t.replace("布布", "Dudu")

    t = re.sub(r'[\\/*?:"<>|]', "", t)
    t = re.sub(r"\s+", " ", t).strip()

    if not t:
        return "Untitled"

    return t[:60].strip()


def force_clear_temp():
    """強制排空暫存目錄，防止多檔下載失敗後殘留。"""
    if os.path.exists(TEMP_DL_DIR):
        shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)

    os.makedirs(TEMP_DL_DIR, exist_ok=True)


def append_unique_line(path, line):
    """以去重方式追加單行文字。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)

    existing = set()
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = {x.strip() for x in f if x.strip()}

    if line in existing:
        return

    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def log_failed(url, reason):
    """將無法下載的連結寫入 failed_links.log。"""
    os.makedirs(DATA_DIR, exist_ok=True)

    with open(FAILED_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{url}  [{reason}]\n")

    print(f"  [!] 已記錄至 failed_links.log：{reason}")


def log_unavailable(url):
    """將確定失效 / 已刪除的 URL 寫入 unavailable_links.txt。"""
    append_unique_line(UNAVAILABLE_FILE, url)


def log_retry_needed(url):
    """將需要補考的 URL 寫入 retry_needed.txt。"""
    append_unique_line(RETRY_NEEDED_FILE, url)


def load_checkpoint():
    """讀取斷點紀錄，回傳已成功處理的 URL 集合。"""
    if not os.path.exists(CHECKPOINT_FILE):
        return set()

    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def save_checkpoint(url):
    """將成功的 URL 追加寫入斷點紀錄。"""
    os.makedirs(DATA_DIR, exist_ok=True)

    with open(CHECKPOINT_FILE, "a", encoding="utf-8") as f:
        f.write(url + "\n")
        f.flush()
        os.fsync(f.fileno())


def ensure_file_written(path):
    """確認下載或搬移後的檔案確實存在。"""
    if not os.path.exists(path):
        raise IOError(f"檔案未找到: {path}")

    if os.path.getsize(path) <= 0:
        raise IOError(f"檔案大小異常: {path}")


def is_media_file(path):
    """判斷是否為要搬移的媒體檔。"""
    ext = os.path.splitext(path)[1].lower()
    return ext in [".jpg", ".jpeg", ".png", ".mp4"]


def collect_temp_media_files() -> List[str]:
    """
    遞迴掃描 temp_insta_dl，避免 Instaloader 額外包一層資料夾時漏抓。

    回傳完整路徑，並依檔名排序。
    """
    media_files = []

    if not os.path.exists(TEMP_DL_DIR):
        return media_files

    for root, _, files in os.walk(TEMP_DL_DIR):
        for filename in files:
            full_path = os.path.join(root, filename)
            if is_media_file(full_path):
                media_files.append(full_path)

    media_files.sort(key=lambda p: os.path.basename(p).lower())
    return media_files


def safe_remove_existing_file(path):
    """若目標檔案已存在，先移除。"""
    if os.path.exists(path):
        os.remove(path)


def safe_remove_existing_dir(path):
    """若目標資料夾已存在，先移除。"""
    if os.path.exists(path):
        shutil.rmtree(path, ignore_errors=True)


# ============================================================
# Instagram fallback：yt-dlp
# ============================================================

def ig_fallback_ytdlp(url, title):
    """
    yt-dlp 備援下載：
    - instaloader 失敗時使用
    - 若已登入，固定加入 cookiefile
    - 最終檔名強制套用 clean_text 規範
    - 若遇到內容不公開 / 已失效，寫入 unavailable / failed
    """
    print("  [備援] 切換至 yt-dlp 下載...")

    ydl_opts_base = {
        "format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "overwrites": True,
        "headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.instagram.com/",
        },
    }

    if is_logged_in and os.path.exists(COOKIES_FILE):
        ydl_opts_base["cookiefile"] = os.path.abspath(COOKIES_FILE)

    try:
        with yt_dlp.YoutubeDL({**ydl_opts_base, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)

    except yt_dlp.utils.DownloadError as e:
        err_str = str(e)

        if (
            "empty media response" in err_str
            or "This content isn't available" in err_str
            or "available to everyone" in err_str
            or "login required" in err_str.lower()
        ):
            log_unavailable(url)
            log_failed(url, "[頁面已失效] 內容已刪除或不可存取")
            raise InvalidPageError("頁面已失效")

        raise

    if title == "Untitled":
        title = clean_text(info.get("description") or info.get("title"))

    final_path = os.path.join(BASE_DIR, f"{title}.mp4")

    ydl_opts_dl = {
        **ydl_opts_base,
        "outtmpl": final_path,
    }

    with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
        ydl.download([url])

    ensure_file_written(final_path)

    print(f"  -> yt-dlp 備援完成: {title}.mp4")
    return final_path


# ============================================================
# Instagram 下載
# ============================================================

def handle_ig(url):
    """
    Instagram 雙引擎智能切換：
    - 優先 instaloader
    - 403 / metadata failed 時切 yt-dlp
    """
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

        # 關鍵修正：
        # dirname_pattern 已固定為 TEMP_DL_DIR，
        # 這裡 target 只給普通名稱，避免絕對路徑被 Instaloader 當成資料夾名稱。
        L.download_post(post, target="post")

        media_files = collect_temp_media_files()

        if not media_files:
            raise IOError("instaloader 未產生任何媒體檔案")

        if len(media_files) == 1:
            src = media_files[0]
            src_ext = os.path.splitext(src)[1].lower()
            new_ext = ".mp4" if src_ext == ".mp4" else ".jpg"

            final_path = os.path.join(BASE_DIR, f"{title}{new_ext}")
            safe_remove_existing_file(final_path)

            shutil.move(src, final_path)
            ensure_file_written(final_path)

            print(f"  -> 單檔完成: {title}{new_ext}")

        else:
            final_target_path = os.path.join(BASE_DIR, title)
            safe_remove_existing_dir(final_target_path)
            os.makedirs(final_target_path, exist_ok=True)

            for i, src in enumerate(media_files, 1):
                src_ext = os.path.splitext(src)[1].lower()
                new_ext = ".mp4" if src_ext == ".mp4" else ".jpg"

                dest = os.path.join(final_target_path, f"{i}{new_ext}")
                shutil.move(src, dest)
                ensure_file_written(dest)

            print(f"  -> 多檔目錄完成: {title}/")

        shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
        os.makedirs(TEMP_DL_DIR, exist_ok=True)
        return True

    except instaloader.exceptions.PrivateProfileNotFollowedException:
        print("[權限受限] 此 Reels 僅限特定觀眾，已跳過。")
        log_failed(url, "[權限受限] 此 Reels 僅限特定觀眾")
        shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
        os.makedirs(TEMP_DL_DIR, exist_ok=True)
        return False

    except Exception as e:
        err_str = str(e)

        if "401" in err_str or "please wait a few minutes" in err_str.lower():
            print("[流量限制] IP 遭暫時封鎖，加入補考清單。")
            log_retry_needed(url)
            shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
            os.makedirs(TEMP_DL_DIR, exist_ok=True)
            return False

        if (
            "403" in err_str
            or "Fetching Post metadata failed" in err_str
            or "login" in err_str.lower()
        ):
            print(f"  [!] instaloader 受阻 ({err_str[:80]})，切換引擎...")

            try:
                force_clear_temp()
                final_path = ig_fallback_ytdlp(url, title)
                shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
                os.makedirs(TEMP_DL_DIR, exist_ok=True)
                ensure_file_written(final_path)
                return True

            except InvalidPageError:
                shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
                os.makedirs(TEMP_DL_DIR, exist_ok=True)
                return "invalid"

            except Exception as e2:
                print(f"  [!] yt-dlp 備援也失敗: {e2}")
                shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
                os.makedirs(TEMP_DL_DIR, exist_ok=True)
                return False

        print(f"IG 下載出錯 {url}: {e}")
        shutil.rmtree(TEMP_DL_DIR, ignore_errors=True)
        os.makedirs(TEMP_DL_DIR, exist_ok=True)
        return False


# ============================================================
# Facebook 下載：CLI 版維持 yt-dlp 單檔影片下載
# ============================================================

def handle_fb(url):
    """
    CLI 版 Facebook 下載：
    - 使用 yt-dlp 下載 Facebook 單檔影片
    - Facebook 多圖精準下載請使用 downloader_GUI 版本
    """
    try:
        ydl_opts_info = {
            "quiet": True,
            "no_warnings": True,
        }

        if os.path.exists(COOKIES_FILE):
            ydl_opts_info["cookiefile"] = os.path.abspath(COOKIES_FILE)

        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            title = clean_text(info.get("description") or info.get("title"))

        print(f"[FB] 正在下載單檔影片: {title}")

        final_path = os.path.join(BASE_DIR, f"{title}.mp4")

        ydl_opts_dl = {
            "outtmpl": final_path,
            "format": "mp4",
            "quiet": True,
            "no_warnings": True,
            "overwrites": True,
        }

        if os.path.exists(COOKIES_FILE):
            ydl_opts_dl["cookiefile"] = os.path.abspath(COOKIES_FILE)

        with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
            ydl.download([url])

        ensure_file_written(final_path)
        print(f"  -> FB 單檔完成: {title}.mp4")
        return True

    except Exception as e:
        print(f"FB 下載出錯 {url}: {e}")
        return False


# ============================================================
# 主流程
# ============================================================

def print_path_summary():
    """啟動時輸出目前實際使用路徑，方便確認沒有跑到專案根目錄。"""
    print("\n[Path] CLI runtime paths")
    print(f"  SCRIPT_DIR   : {SCRIPT_DIR}")
    print(f"  PROJECT_ROOT : {PROJECT_ROOT}")
    print(f"  DOWNLOAD_DIR : {BASE_DIR}")
    print(f"  TEMP_DIR     : {TEMP_DL_DIR}")
    print(f"  DATA_DIR     : {DATA_DIR}")
    print(f"  COOKIES_FILE : {COOKIES_FILE}")
    print(f"  INPUT_FILE   : {PREPROCESS_DOWNLOAD_LINK}")
    print("-" * 60)


def load_input_urls():
    """讀取 pre-processing/output/download_link.txt。"""
    if not os.path.exists(PREPROCESS_DOWNLOAD_LINK):
        print(f"找不到輸入檔: {PREPROCESS_DOWNLOAD_LINK}")
        print("請先執行 pre-processing/link_sorter.py 產生 output/download_link.txt")
        return []

    with open(PREPROCESS_DOWNLOAD_LINK, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def main():
    print_path_summary()
    try_login_flow()

    urls = load_input_urls()
    if not urls:
        return

    processed = load_checkpoint()

    skipped = sum(1 for u in urls if u in processed)
    if skipped:
        print(f"[斷點] 已載入紀錄，略過 {skipped}/{len(urls)} 個已完成連結。")

    fail_streak = 0
    base_fail_sleep = 60

    count_success = 0
    count_retry = 0
    count_unavailable = 0
    count_skipped = 0

    for i, url in enumerate(urls):
        if url in processed:
            count_skipped += 1
            continue

        print(f"\n進度: [{i + 1}/{len(urls)}]")
        print(f"URL: {url}")

        success = False

        if "instagram.com" in url:
            result = handle_ig(url)

            if result == "invalid":
                print("[失效] 連結已損毀或刪除，跳過紀錄。")
                count_unavailable += 1
                continue

            if not result:
                log_failed(url, "[下載失敗] instaloader 與 yt-dlp 備援皆失敗")
                log_retry_needed(url)
                print("[跳過] 連結已更新至 retry_needed.txt，準備下一個進度。")
                count_retry += 1
                continue

            success = result

        elif "facebook.com" in url or "fb.watch" in url:
            success = handle_fb(url)

        else:
            print(f"[跳過] 不支援的 URL: {url}")
            continue

        if success:
            save_checkpoint(url)
            processed.add(url)
            fail_streak = 0
            count_success += 1

            sleep_sec = random.uniform(20, 40)
            print(f"[冷卻] 等待 {sleep_sec:.1f} 秒後處理下一筆...")
            time.sleep(sleep_sec)

        else:
            fail_streak += 1
            sleep_time = min(base_fail_sleep * (2 ** (fail_streak - 1)), 300)

            print(f"  偵測到連線壓力，增加冷卻時間... ({sleep_time}s)")
            time.sleep(sleep_time)

    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("下載馬拉松任務完成！")
    print(f"✅ 成功下載：{count_success} 筆")
    print(f"⏭️ 已略過：{count_skipped} 筆")
    print(f"⚠️ 需要補考：{count_retry} 筆 (請見 data/retry_needed.txt)")
    print(f"❌ 連結失效：{count_unavailable} 筆 (請見 data/unavailable_links.txt)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━")


if __name__ == "__main__":
    main()