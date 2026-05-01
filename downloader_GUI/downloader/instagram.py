import html
import http.cookiejar
import os
import re
import shutil
import threading
import time
from urllib.parse import urlparse, unquote

import instaloader
import requests
import yt_dlp
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config import DOWNLOAD_DIR, TEMP_DIR, DATA_DIR, COOKIES_FILE
from utils.filename import safe_title
from utils.logger import get_logger

try:
    from opencc import OpenCC
except Exception:
    OpenCC = None

_cc = OpenCC("s2t") if OpenCC else None


def _to_traditional(text: str) -> str:
    if not text:
        return text

    text = str(text)

    if _cc:
        try:
            return _cc.convert(text)
        except Exception:
            return text

    return text

logger = get_logger("instagram")

_MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".mp4", ".webp", ".m4v", ".mov"}
_DL_TIMEOUT = 300
_MAX_CAROUSEL_ITEMS = 40
_MIN_FILE_SIZE = 18 * 1024

_L = None
_cookie_file = None
_is_logged_in = False


def setup():
    global _L, _is_logged_in

    os.makedirs(TEMP_DIR, exist_ok=True)

    # 重要：
    # Instaloader 的 dirname_pattern 會把 target 當成資料夾名稱模板。
    # 若把 Windows 絕對路徑直接丟給 download_post(target=TEMP_DIR)，
    # 會在 downloader_GUI 底下長出 C:\Users\... 這種錯誤資料夾。
    # 這裡固定把輸出限制在 TEMP_DIR/post 底下，再由 move_files() 統一搬移與重新命名。
    _L = instaloader.Instaloader(
        dirname_pattern=os.path.join(TEMP_DIR, "{target}"),
        filename_pattern="{shortcode}",
        download_videos=True,
        download_video_thumbnails=False,
        save_metadata=False,
        compress_json=False,
        download_comments=False,
        post_metadata_txt_pattern="",
        quiet=True,
    )
    _is_logged_in = False


def _find_ffmpeg():
    candidates = [
        os.path.join(os.getcwd(), "ffmpeg.exe"),
        os.path.join(os.getcwd(), "bin", "ffmpeg.exe"),
        os.path.join(os.getcwd(), "tools", "ffmpeg.exe"),
        shutil.which("ffmpeg"),
        shutil.which("ffmpeg.exe"),
    ]

    for path in candidates:
        if path and os.path.exists(path):
            return path

    return None


def _load_cookiejar_to_instaloader(cookies_path: str) -> int:
    if _L is None:
        setup()

    if not cookies_path or not os.path.exists(cookies_path):
        return 0

    loaded = 0

    try:
        jar = http.cookiejar.MozillaCookieJar()
        jar.load(cookies_path, ignore_discard=True, ignore_expires=True)

        for cookie in jar:
            domain = cookie.domain or ""

            if "instagram.com" not in domain:
                continue

            try:
                _L.context._session.cookies.set_cookie(cookie)
                loaded += 1
            except Exception:
                pass

        if loaded:
            logger.info(f"已載入 IG cookies 到 instaloader session: {loaded}")

    except Exception as e:
        logger.warning(f"載入 IG cookies 到 instaloader 失敗: {e}")

    return loaded


def use_cookies(cookies_path: str):
    global _cookie_file

    _cookie_file = cookies_path

    if _L is None:
        setup()

    logger.info(f"使用 cookies: {cookies_path}")
    _load_cookiejar_to_instaloader(cookies_path)


def _export_cookies(username: str):
    global _cookie_file

    cookies_dir = os.path.join(DATA_DIR, "cookies")
    os.makedirs(cookies_dir, exist_ok=True)

    path = os.path.join(cookies_dir, f"{username}.txt")

    try:
        jar = http.cookiejar.MozillaCookieJar(path)

        for cookie in _L.context._session.cookies:
            jar.set_cookie(cookie)

        jar.save(ignore_discard=True, ignore_expires=True)

        _cookie_file = path
        logger.info(f"已匯出 session cookies: {path}")

    except Exception as e:
        logger.warning(f"Cookie 匯出失敗: {e}")


def login_with_retry(username: str, password: str, get_code_fn):
    global _is_logged_in

    if _L is None:
        setup()

    last_err = Exception("未知錯誤")

    for attempt in range(3):
        try:
            setup()

            logger.info(f"開始登入 Instagram（第 {attempt + 1}/3 輪）: {username}")

            _L.login(username, password)
            _is_logged_in = True

            _export_cookies(username)

            logger.info(f"Instagram 登入成功: {username}")
            return

        except instaloader.exceptions.TwoFactorAuthRequiredException:
            logger.info(f"需要 2FA（第 {attempt + 1}/3 輪）")

            code = get_code_fn()

            if not code:
                raise Exception("使用者取消 2FA 驗證碼輸入")

            try:
                _L.two_factor_login(code.strip())
                _is_logged_in = True

                _export_cookies(username)

                logger.info(f"Instagram 2FA 登入成功: {username}")
                return

            except Exception as e:
                last_err = e
                logger.warning(f"2FA 驗證失敗（第 {attempt + 1}/3 輪）: {e}")

        except Exception as e:
            last_err = e
            logger.warning(f"Instagram 登入失敗（第 {attempt + 1}/3 輪）: {e}")

    raise Exception(f"Instagram 登入失敗（已重試 3 次）。最後錯誤：{last_err}")


def clear_temp():
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    os.makedirs(TEMP_DIR, exist_ok=True)




def _clear_temp_after_terminal_failure(status: str, reason: str = ""):
    """Clean TEMP_DIR after terminal non-success Instagram results.

    每筆 IG 任務開始前會清理上一筆殘留；任務若以 FAILED / BLOCKED /
    MISSING / RETRY 結束，也會清空 post/ 暫存。SUCCESS 不在這裡清，
    仍由 move_files() 在成功搬移後負責清理，避免誤刪正在搬移的媒體。
    """
    if (status or "").upper() == "SUCCESS":
        return

    try:
        leftover = _list_media_files(TEMP_DIR)
    except Exception:
        leftover = []

    if leftover:
        logger.info(
            f"IG 清理暫存 post/：status={status}, "
            f"leftover={len(leftover)}, reason={reason or 'n/a'}"
        )

    clear_temp()

def _extract_shortcode(url: str):
    m = re.search(r"/(?:p|reel|reels)/([^/?#&]+)", url)
    return m.group(1) if m else None


def _normalize_ig_url(url: str) -> str:
    shortcode = _extract_shortcode(url)

    if not shortcode:
        return url

    if "/reel/" in url or "/reels/" in url:
        return f"https://www.instagram.com/reel/{shortcode}/"

    return f"https://www.instagram.com/p/{shortcode}/"



def _is_ig_reel_url(url: str) -> bool:
    low = (url or "").lower()
    return "/reel/" in low or "/reels/" in low


def _is_ig_post_url(url: str) -> bool:
    low = (url or "").lower()
    return "/p/" in low


def _is_ytdlp_non_video_post_error(err: str) -> bool:
    e = (err or "").lower()
    return any(k in e for k in [
        "no video formats found",
        "there is no video in this post",
        "requested format is not available",
    ])


def _prefer_final_status(*pairs):
    """
    多引擎結果合併規則：
    - SUCCESS 已在呼叫端先 return
    - MISSING 優先，代表頁面真的消失，不重試
    - RETRY 次優先，代表暫時性風控 / timeout，之後可補跑
    - BLOCKED 僅在明確 login / checkpoint / audience restricted 時保留
    - 單純 no video formats 不視為 BLOCKED，避免圖文貼文被誤標
    """
    statuses = []
    for st, err in pairs:
        if not st:
            continue
        normalized = "MISSING" if st == "UNAVAILABLE" else (st or "FAILED")
        statuses.append((normalized, err or ""))

    for st, err in statuses:
        if st == "MISSING":
            return "MISSING", err

    for st, err in statuses:
        if st == "RETRY":
            return "RETRY", err

    for st, err in statuses:
        if st == "BLOCKED":
            return "BLOCKED", err

    if statuses:
        joined = " | ".join([f"{st}={err}" for st, err in statuses])
        return "FAILED", joined

    return "FAILED", "未知錯誤"


def _classify_error(err: str):
    e = (err or "").lower()

    # yt-dlp 的 empty media response 代表 yt-dlp 沒拿到媒體資料，
    # 不等於帳號 / IP 被封鎖。先回 FAILED，讓 download() 後續交給
    # Playwright 讀實際頁面，再判斷是 MISSING、BLOCKED 或一般 FAILED。
    if "empty media response" in e:
        return "FAILED", err

    if _is_ytdlp_non_video_post_error(e):
        return "FAILED", err

    if any(k in e for k in [
        "please wait a few minutes",
        "rate limit",
        "too many requests",
        "temporarily blocked",
        "try again later",
        "429",
        "timeout",
        "timed out",
        "net::err_timed_out",
    ]):
        return "RETRY", err

    if any(k in e for k in [
        "login",
        "checkpoint",
        "challenge",
        "private profile",
        "privateprofile",
        "requires login",
        "sign in",
        "specific audience",
        "not available to everyone",
        "特定受眾無法查看",
        "此內容並未開放所有人查看",
        "for users aged",
        "restricted",
        "generic instagram",
        "accounts/login",
    ]):
        return "BLOCKED", err

    if any(k in e for k in [
        "404",
        "not found",
        "page not found",
        "內容不存在",
        "deleted",
    ]):
        return "MISSING", err

    return "FAILED", err


def _load_netscape_cookies(path: str, domain_keyword: str):
    cookies = []

    if not path or not os.path.exists(path):
        return cookies

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()

                if not line or line.startswith("#"):
                    continue

                parts = line.split("\t")

                if len(parts) != 7:
                    continue

                domain, _, cookie_path, secure_flag, expires, name, value = parts

                if domain_keyword not in domain:
                    continue

                clean_domain = domain
                http_only = False

                if clean_domain.startswith("#HttpOnly_"):
                    clean_domain = clean_domain.replace("#HttpOnly_", "", 1)
                    http_only = True

                cookie = {
                    "name": name,
                    "value": value,
                    "domain": clean_domain,
                    "path": cookie_path or "/",
                    "secure": secure_flag.upper() == "TRUE",
                    "httpOnly": http_only,
                }

                try:
                    exp = int(expires)

                    if exp > 0:
                        cookie["expires"] = exp

                except Exception:
                    pass

                cookies.append(cookie)

    except Exception as e:
        logger.warning(f"讀取 cookies 失敗: {e}")

    return cookies


def _clean_title(raw: str, fallback: str = "Instagram_Post") -> str:
    """
    清理 Instagram post title / caption。

    原則：
    - 目錄名稱只使用 post title / caption
    - 不保留創作者名稱
    - 不保留「在 Instagram / on Instagram」
    - 一律簡體轉繁體

    Examples:
    - "aaaaa 在 Instagram abcdefg"
      -> "abcdefg"

    - "bbbbbb 在 Instagram zxcvb"
      -> "zxcvb"

    - "Kristy Jessica 在 Instagram All I need is some creativity, peace..."
      -> "All I need is some creativity, peace..."
    """
    if not raw:
        return fallback

    text = html.unescape(str(raw)).strip()
    text = re.sub(r"\s+", " ", text)

    m = re.search(
        r"^.+?\s+on\s+Instagram\s*[:：]\s*[“\"]?(.+?)[”\"]?\s*$",
        text,
        flags=re.I,
    )
    if m:
        text = m.group(1).strip()

    m = re.search(
        r"^.+?\s+在\s+Instagram\s+(.+)$",
        text,
        flags=re.I,
    )
    if m:
        text = m.group(1).strip()

    m = re.search(
        r"^.+?\s+在\s+Instagram\s*(?:上)?\s*[:：]\s*(.+)$",
        text,
        flags=re.I,
    )
    if m:
        text = m.group(1).strip()

    text = re.sub(r"\s*•\s*Instagram.*$", "", text, flags=re.I).strip()
    text = re.sub(r"\s*-\s*Instagram.*$", "", text, flags=re.I).strip()
    text = text.strip("“”\"' ")

    if not text:
        return fallback

    low = text.lower().strip()

    if low in {
        "instagram",
        "login instagram",
        "login",
        "untitled",
        "photos and videos",
        "instagram photos and videos",
    }:
        return fallback

    if re.fullmatch(r"\(\d+\)\s*instagram", low):
        return fallback

    text = _to_traditional(text)

    if len(text) > 90:
        text = text[:90].rstrip(" ._-")

    return text or fallback


def _is_bad_ig_media_url(src: str) -> bool:
    low = (src or "").lower()

    if not low:
        return True

    bad = [
        "static.cdninstagram.com",
        "/rsrc.php",
        "instagram.com/static",
        "profile_pic",
        "s150x150",
        "s100x100",
        "s32x32",
        "s40x40",
        "s50x50",
        "s64x64",
        "emoji",
        "sprite",
        "icon",
        "logo",
        "t51.2885-19",
        "t51.82787-19",
        "_nc_sid=bf7eb4",
        "instagram.com/images",
        "apple-touch-icon",
        "favicon",
    ]

    return any(x in low for x in bad)


def _looks_like_real_ig_media_url(src: str) -> bool:
    if not src:
        return False

    src = html.unescape(unquote(src.strip()))
    low = src.lower()

    if _is_bad_ig_media_url(low):
        return False

    if low.startswith("data:"):
        return False

    if ".mp4" in low or ".m4v" in low or ".mov" in low:
        return True

    if not ("cdninstagram.com" in low or "fbcdn.net" in low or "instagram.f" in low):
        return False

    return any(x in low for x in [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        "format=jpg",
        "format=jpeg",
        "format=png",
        "format=webp",
    ])


def _media_quality_score(url: str) -> int:
    low = (url or "").lower()
    score = 0

    if ".mp4" in low or ".m4v" in low or ".mov" in low:
        score += 10000

    for n in [4096, 3000, 2160, 1920, 1440, 1350, 1280, 1080, 960, 864, 750, 640, 480, 320]:
        if str(n) in low:
            score += n

    if "s1440x1440" in low:
        score += 3000

    if "s1080x1080" in low:
        score += 2500

    if "s960x960" in low:
        score += 1800

    if "s640x640" in low:
        score += 300

    if "c288.0.864.864a" in low:
        score -= 1000

    if "t51.82787-15" in low:
        score += 1500

    if "t51.2885-15" in low:
        score += 1500

    return score


def _ext_from_url(url: str, default_ext=".jpg"):
    path = urlparse(url).path.lower()
    ext = os.path.splitext(path)[1]

    if ext in _MEDIA_EXTS:
        return ext

    low = url.lower()

    if ".mp4" in low:
        return ".mp4"

    if ".m4v" in low:
        return ".m4v"

    if ".mov" in low:
        return ".mov"

    if ".webp" in low or "format=webp" in low:
        return ".webp"

    if ".png" in low or "format=png" in low:
        return ".png"

    return default_ext


def _dedupe_media(items):
    out = []
    seen = set()

    for item in items:
        if isinstance(item, str):
            src = item.strip()
            media_type = "video" if any(x in src.lower() for x in [".mp4", ".m4v", ".mov"]) else "image"
            item = {
                "src": src,
                "type": media_type,
                "score": 0,
            }

        src = (item.get("src") or "").strip()

        if not src:
            continue

        src = html.unescape(unquote(src))

        if not _looks_like_real_ig_media_url(src):
            continue

        path = urlparse(src.split("?")[0]).path
        basename = os.path.basename(path)
        key = basename or src[:180]

        if key in seen:
            continue

        seen.add(key)

        item["src"] = src
        item["score"] = item.get("score", 0) + _media_quality_score(src)

        out.append(item)

    return sorted(out, key=lambda x: x.get("score", 0), reverse=True)


def _list_media_files(root_dir: str):
    out = []

    if not os.path.exists(root_dir):
        return out

    for root, _, files in os.walk(root_dir):
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()

            if ext not in _MEDIA_EXTS:
                continue

            path = os.path.join(root, filename)

            try:
                size = os.path.getsize(path)
            except Exception:
                size = 0

            if size >= _MIN_FILE_SIZE:
                out.append(path)

    out.sort()
    return out


def _safe_output_name(raw: str, fallback: str = "Instagram_Post", max_len: int = 56) -> str:
    """Return a short Windows-safe output name while preserving existing title logic."""
    name = safe_title(_clean_title(raw, fallback))
    if not name:
        name = fallback

    # Windows path safety: remove control chars / replacement chars and trim trailing
    # spaces/dots.  Long IG captions easily trigger WinError 3 / MAX_PATH in deep dirs.
    name = re.sub(r"[\x00-\x1f\x7f]+", "", str(name))
    name = name.replace("�", "").strip(" ._-")
    name = re.sub(r"\s+", " ", name).strip()
    if not name:
        name = fallback

    if len(name) > max_len:
        name = name[:max_len].rstrip(" ._-")
    return name or fallback


def _unique_path(path: str) -> str:
    """Return a non-conflicting path without deleting existing successful downloads."""
    if not os.path.exists(path):
        return path

    base, ext = os.path.splitext(path)
    for i in range(2, 1000):
        candidate = f"{base}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate

    return f"{base}_{int(time.time())}{ext}"


def move_files(title: str, fallback_name: str = "Instagram_Post") -> bool:
    """
    Move downloaded media from TEMP_DIR to DOWNLOAD_DIR.

    Conservative fixes:
    - Keep original single-file / folder output behavior.
    - Do not delete existing successful folders by default; use unique suffix instead.
    - Use short Windows-safe names to avoid WinError 3 / MAX_PATH.
    - If title-based move fails, retry once with a short fallback name.
    """
    files = _list_media_files(TEMP_DIR)

    if not files:
        return False

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    candidate_names = []
    for raw in [title, fallback_name, "Instagram_Post"]:
        name = _safe_output_name(raw, "Instagram_Post")
        if name and name not in candidate_names:
            candidate_names.append(name)

    last_error = None

    for name in candidate_names:
        try:
            if len(files) == 1:
                src = files[0]
                ext = os.path.splitext(src)[1].lower()
                final_ext = ".mp4" if ext in {".mp4", ".m4v", ".mov"} else ".jpg"

                dst = _unique_path(os.path.join(DOWNLOAD_DIR, f"{name}{final_ext}"))
                shutil.move(src, dst)

                logger.info(f"IG 單檔完成: {os.path.basename(dst)}")

            else:
                folder = _unique_path(os.path.join(DOWNLOAD_DIR, name))
                os.makedirs(folder, exist_ok=True)

                for i, src in enumerate(files, 1):
                    ext = os.path.splitext(src)[1].lower()
                    final_ext = ".mp4" if ext in {".mp4", ".m4v", ".mov"} else ".jpg"

                    dst = os.path.join(folder, f"{i}{final_ext}")
                    shutil.move(src, dst)

                logger.info(f"IG 多檔完成: {os.path.basename(folder)}/ ({len(files)} 個)")

            clear_temp()
            return True

        except Exception as e:
            last_error = e
            logger.warning(f"IG move_files 失敗，改用備援檔名重試: name={name} | {e}")
            # Refresh the temp file list after partial move attempts.
            files = _list_media_files(TEMP_DIR)
            if not files:
                # If files disappeared during move, treat it as completed instead of
                # letting yt-dlp overwrite a successful Playwright capture as FAILED.
                logger.info("IG move_files 後 TEMP 已清空，視為搬移成功")
                clear_temp()
                return True

    logger.warning(f"IG move_files 全部失敗: {last_error}")
    return False


def _collect_from_instaloader_shortcode(url: str):
    if _L is None:
        setup()

    cookie_path = _cookie_file if (_cookie_file and os.path.exists(_cookie_file)) else COOKIES_FILE

    if os.path.exists(cookie_path):
        _load_cookiejar_to_instaloader(cookie_path)

    shortcode = _extract_shortcode(url)

    if not shortcode:
        raise Exception("無法解析 Instagram shortcode")

    clear_temp()

    post = instaloader.Post.from_shortcode(_L.context, shortcode)
    title = post.caption or post.owner_username or shortcode

    _L.download_post(post, target=TEMP_DIR)

    if move_files(title):
        return "SUCCESS", ""

    raise Exception("instaloader 已執行，但沒有有效媒體輸出")


def _download_via_ytdlp(url: str, quick: bool = False):
    """
    yt-dlp fallback。

    v11.24 conservative fix:
    - 避免同一個 IG 圖文貼文因為 no video formats / empty media response 被重複測 6~10 次。
    - Reel 才優先嘗試影片格式。
    - /p/ 圖文貼文只做快速備援，不把「沒有影片」當成 BLOCKED。
    """
    clear_temp()
    url = _normalize_ig_url(url)

    ffmpeg_path = _find_ffmpeg()
    is_reel = _is_ig_reel_url(url)
    is_post = _is_ig_post_url(url)

    if ffmpeg_path:
        formats = [
            "bestvideo+bestaudio/best",
            "best",
        ]
    else:
        if is_reel:
            logger.warning("未找到 ffmpeg，yt-dlp 將使用單檔格式，避免合併失敗")

        formats = [
            "best[ext=mp4]/best[protocol^=http]/best",
            "best",
        ]

    # 圖文貼文用 yt-dlp 常見結果是 No video formats / There is no video in this post。
    # 此時重複測多種 cookie / format 只會製造大量 ERROR log 與 IG 風控壓力。
    if quick or is_post:
        formats = formats[:1]

    variants = []

    if _cookie_file and os.path.exists(_cookie_file):
        variants.append({
            "cookiefile": os.path.abspath(_cookie_file),
        })

    if os.path.exists(COOKIES_FILE) and COOKIES_FILE != _cookie_file:
        variants.append({
            "cookiefile": os.path.abspath(COOKIES_FILE),
        })

    if not variants:
        variants.append({})

    if quick or is_post:
        variants = variants[:1]

    last_error = "未知錯誤"

    for extra in variants:
        for fmt in formats:
            try:
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "outtmpl": os.path.join(TEMP_DIR, "%(title).120s.%(ext)s"),
                    "overwrites": True,
                    "noplaylist": False,
                    "format": fmt,
                    "ignore_no_formats_error": False,
                    "http_headers": {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/123.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://www.instagram.com/",
                        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                    },
                }

                if ffmpeg_path:
                    ydl_opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)
                    ydl_opts["merge_output_format"] = "mp4"

                ydl_opts.update(extra)

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)

                    title = (
                        info.get("description")
                        or info.get("title")
                        or _extract_shortcode(url)
                        or "Instagram_Post"
                    )

                if move_files(title):
                    return "SUCCESS", ""

                last_error = "yt-dlp 已執行，但沒有有效媒體檔案"

            except Exception as e:
                last_error = str(e)

                if _is_ytdlp_non_video_post_error(last_error):
                    logger.info(f"yt-dlp 判定非影片貼文，停止 yt-dlp 重複嘗試: {last_error}")
                    return "FAILED", last_error

                logger.warning(f"yt-dlp 失敗: {last_error}")

                if quick:
                    return _classify_error(last_error)

    return _classify_error(last_error)

def _get_ig_title(page, fallback_shortcode: str = ""):
    candidates = []

    for sel in [
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
        'meta[property="og:description"]',
        'meta[name="description"]',
    ]:
        try:
            val = page.locator(sel).first.get_attribute("content")

            if val:
                candidates.append(val)

        except Exception:
            pass

    try:
        title = page.title() or ""

        if title:
            candidates.append(title)

    except Exception:
        pass

    for candidate in candidates:
        cleaned = _clean_title(candidate, fallback_shortcode or "Instagram_Post")

        if cleaned and cleaned != "Instagram_Post":
            return cleaned

    return fallback_shortcode or "Instagram_Post"



def _is_missing_ig_page(page) -> bool:
    """
    優先判斷 Instagram 貼文是否已不存在。

    這類頁面屬於內容層級缺失，應歸類為 MISSING；
    不可因為頁面 title 類似 Instagram 或沒有抓到媒體，就誤判成 BLOCKED。
    """
    try:
        current_url = (page.url or "").lower()
        if any(x in current_url for x in [
            "/404",
            "page_not_found",
            "not_found",
        ]):
            return True
    except Exception:
        pass

    body_text = ""
    try:
        body_text = page.locator("body").first.inner_text(timeout=2500) or ""
    except Exception:
        body_text = ""

    title_text = ""
    try:
        title_text = page.title() or ""
    except Exception:
        title_text = ""

    combined = f"{body_text}\n{title_text}".strip()
    combined_lower = combined.lower()

    missing_markers = [
        "很抱歉，此頁面無法使用",
        "連結可能故障",
        "頁面已遭移除",
        "此頁面無法使用",
        "找不到此頁面",
        "內容不存在",
        "貼文已移除",
        "Sorry, this page isn't available",
        "Sorry, this page is not available",
        "The link you followed may be broken",
        "The page may have been removed",
        "Page Not Found",
        "Content isn't available",
        "Content is not available",
        "This content isn't available",
        "This content is not available",
        "Post unavailable",
        "Not Found",
    ]

    return any(marker.lower() in combined_lower for marker in missing_markers)

def _is_generic_ig_page(page) -> bool:
    """
    判斷是否真的被導到登入 / challenge / checkpoint。

    注意：
    Instagram 公開貼文在 JS 還沒完全 hydrate 前，page.title() 常常只是 "Instagram"。
    舊版只要 title == Instagram 就判 BLOCKED，會把很多可公開瀏覽的貼文誤判。
    """
    current = ""

    try:
        current = (page.url or "").lower()

        if (
            "/accounts/login" in current
            or "/login" in current
            or "challenge" in current
            or "checkpoint" in current
        ):
            return True

    except Exception:
        pass

    body = ""

    try:
        body = (page.locator("body").first.inner_text(timeout=2500) or "").lower()
    except Exception:
        body = ""

    login_markers = [
        "log in to instagram",
        "login instagram",
        "登入 instagram",
        "登入即可查看",
        "請登入",
        "sign up",
        "create an account",
        "accounts/login",
        "challenge_required",
        "checkpoint",
        "verify your account",
        "help us confirm",
    ]

    if any(marker in body for marker in login_markers):
        return True

    return False

def _get_current_slide_main_media(page):
    js = """
    () => {
      const scopes = [];
      const dialog = document.querySelector('div[role="dialog"]');
      const article = document.querySelector('article');

      if (dialog) scopes.push(dialog);
      if (article) scopes.push(article);
      if (scopes.length === 0) scopes.push(document);

      const result = [];

      function bad(low) {
        const blackList = [
          'static.cdninstagram.com',
          '/rsrc.php',
          'instagram.com/static',
          'profile_pic',
          's150x150',
          's100x100',
          's32x32',
          's40x40',
          's50x50',
          's64x64',
          'emoji',
          'sprite',
          'icon',
          'logo',
          't51.2885-19',
          't51.82787-19',
          '_nc_sid=bf7eb4',
          'favicon',
          'apple-touch-icon'
        ];

        return blackList.some(x => low.includes(x));
      }

      function pushCandidate(el, src) {
        if (!src) return;

        const low = src.toLowerCase();

        if (bad(low)) return;

        const looksReal =
          low.includes('.mp4') ||
          low.includes('.m4v') ||
          low.includes('.mov') ||
          low.includes('cdninstagram.com') ||
          low.includes('fbcdn.net') ||
          low.includes('instagram.f');

        if (!looksReal) return;

        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);

        const w = r.width || 0;
        const h = r.height || 0;
        const left = r.left || 0;
        const top = r.top || 0;

        const visible = (
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          parseFloat(style.opacity || '1') > 0 &&
          w >= 120 &&
          h >= 120 &&
          left > -600 &&
          top > -600 &&
          left < window.innerWidth + 600 &&
          top < window.innerHeight + 600
        );

        if (!visible) return;

        const naturalW = el.naturalWidth || el.videoWidth || 0;
        const naturalH = el.naturalHeight || el.videoHeight || 0;

        const centerX = left + w / 2;
        const centerY = top + h / 2;

        const dx = Math.abs(centerX - window.innerWidth / 2);
        const dy = Math.abs(centerY - window.innerHeight / 2);

        result.push({
          type: el.tagName.toLowerCase() === 'video' ? 'video' : 'image',
          src,
          score: (w * h) + ((naturalW || 0) * (naturalH || 0) / 4) - (dx * 3 + dy * 3),
          area: w * h,
          naturalArea: (naturalW || 0) * (naturalH || 0)
        });
      }

      for (const scope of scopes) {
        const nodes = Array.from(scope.querySelectorAll('img, video'));

        for (const el of nodes) {
          pushCandidate(el, (el.currentSrc || el.src || '').trim());
          pushCandidate(el, (el.getAttribute('src') || '').trim());

          const srcset = el.getAttribute('srcset') || '';

          if (srcset) {
            const parts = srcset.split(',').map(x => x.trim()).filter(Boolean);

            for (const part of parts) {
              const u = part.split(/\\s+/)[0];
              pushCandidate(el, u);
            }
          }
        }
      }

      result.sort((a, b) => b.score - a.score);

      return result;
    }
    """

    try:
        items = page.evaluate(js) or []
    except Exception:
        items = []

    items = _dedupe_media(items)

    return items[:1] if items else []


def _get_meta_ig_media(page):
    items = []

    selectors = [
        ('meta[property="og:video"]', "video"),
        ('meta[property="og:video:url"]', "video"),
        ('meta[property="og:video:secure_url"]', "video"),
        ('meta[name="twitter:player:stream"]', "video"),
        ('meta[property="og:image"]', "image"),
        ('meta[property="og:image:secure_url"]', "image"),
        ('meta[name="twitter:image"]', "image"),
    ]

    for sel, media_type in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()

            for i in range(count):
                val = loc.nth(i).get_attribute("content")

                if val and _looks_like_real_ig_media_url(val):
                    items.append({
                        "type": media_type,
                        "src": val,
                        "score": 900000,
                    })

        except Exception:
            continue

    return _dedupe_media(items)


def _click_next_ig(page):
    selectors = [
        'button[aria-label="Next"]',
        'button[aria-label="下一張"]',
        'button[aria-label="下一則"]',
        'button[aria-label="下一步"]',
        'div[role="button"][aria-label="Next"]',
        'div[role="button"][aria-label="下一張"]',
        'div[role="button"][aria-label="下一則"]',
        'svg[aria-label="Next"]',
        'svg[aria-label="下一張"]',
    ]

    for sel in selectors:
        try:
            loc = page.locator(sel)

            if loc.count() > 0:
                target = loc.first

                try:
                    target.click(timeout=1500)

                except Exception:
                    handle = target.element_handle()

                    if handle:
                        page.evaluate(
                            "(el) => el.closest('button, div[role=button]')?.click()",
                            handle,
                        )

                page.wait_for_timeout(1400)
                return True

        except Exception:
            continue

    return False


def _media_key_from_url(src: str) -> str:
    """Create a stable key for IG CDN URLs while preserving order and avoiding duplicates."""
    src = html.unescape(unquote((src or "").strip()))
    try:
        path = urlparse(src.split("?")[0]).path
        basename = os.path.basename(path)
        if basename:
            return basename
    except Exception:
        pass
    return src[:180]


def _is_probably_valid_media_body(body: bytes, media_url: str = "", content_type: str = "") -> bool:
    """Validate downloaded IG media bytes without being overly strict."""
    if not body or len(body) < _MIN_FILE_SIZE:
        return False

    ct = (content_type or "").lower()
    if "text/html" in ct or "application/json" in ct:
        return False

    head = body[:32]
    if head.startswith(b"\xff\xd8\xff"):
        return True
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return True
    if b"ftyp" in head[:16]:
        return True

    low = (media_url or "").lower()
    if any(x in low for x in [".jpg", ".jpeg", ".png", ".webp", ".mp4", ".m4v", ".mov", "format=jpg", "format=webp", "format=png"]):
        return True
    if ct.startswith("image/") or ct.startswith("video/") or "octet-stream" in ct:
        return True

    return False


def _write_media_body(dst: str, body: bytes, media_url: str = "", content_type: str = "") -> int:
    if not _is_probably_valid_media_body(body, media_url=media_url, content_type=content_type):
        raise Exception(f"媒體內容無效或過小: {len(body) if body else 0} bytes")

    with open(dst, "wb") as f:
        f.write(body)

    return len(body)


def _capture_playwright_response(response, harvested: dict):
    """Harvest real browser-loaded IG media responses as a safe fallback."""
    try:
        media_url = response.url or ""
        if not _looks_like_real_ig_media_url(media_url):
            return
        if response.status < 200 or response.status >= 300:
            return

        headers = response.headers or {}
        content_type = headers.get("content-type", "") or headers.get("Content-Type", "") or ""
        low_ct = content_type.lower()
        if low_ct and not (low_ct.startswith("image/") or low_ct.startswith("video/") or "octet-stream" in low_ct):
            return

        body = response.body()
        if not _is_probably_valid_media_body(body, media_url=media_url, content_type=content_type):
            return

        media_type = "video" if any(x in media_url.lower() for x in [".mp4", ".m4v", ".mov"]) or low_ct.startswith("video/") else "image"
        key = _media_key_from_url(media_url)
        score = _media_quality_score(media_url) + len(body)

        old = harvested.get(key)
        if old and old.get("score", 0) >= score:
            return

        harvested[key] = {
            "src": html.unescape(unquote(media_url)),
            "type": media_type,
            "body": body,
            "content_type": content_type,
            "score": score,
            "from": "network",
        }
    except Exception:
        return


def _download_with_playwright_request(context, url: str, dst: str, referer: str):
    """Download one IG media URL using Playwright request context."""
    base_headers = {
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,video/*,*/*;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    referers = []
    for r in [referer, "https://www.instagram.com/", "https://www.instagram.com"]:
        if r and r not in referers:
            referers.append(r)

    last_error = "未知錯誤"

    for ref in referers:
        headers = dict(base_headers)
        headers["Referer"] = ref
        try:
            resp = context.request.get(
                url,
                headers=headers,
                timeout=60000,
            )

            if not resp.ok:
                last_error = f"Playwright request failed: HTTP {resp.status}"
                continue

            content_type = ""
            try:
                content_type = resp.headers.get("content-type", "") or ""
            except Exception:
                content_type = ""

            body = resp.body()
            return _write_media_body(dst, body, media_url=url, content_type=content_type)

        except Exception as e:
            last_error = str(e)
            continue

    raise Exception(last_error)


def _collect_ig_media_playwright(url: str):
    clear_temp()

    browser = None
    context = None

    url = _normalize_ig_url(url)
    shortcode = _extract_shortcode(url) or ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            context = browser.new_context(
                viewport={
                    "width": 1400,
                    "height": 980,
                },
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                locale="zh-TW",
            )

            cookie_path = _cookie_file if (_cookie_file and os.path.exists(_cookie_file)) else COOKIES_FILE
            ig_cookies = _load_netscape_cookies(cookie_path, "instagram.com")

            if ig_cookies:
                try:
                    context.add_cookies(ig_cookies)
                    logger.info(f"Playwright 載入 IG cookies: {len(ig_cookies)}")

                except Exception as e:
                    logger.warning(f"add_cookies 失敗: {e}")

            harvested_media = {}
            page = context.new_page()
            page.on("response", lambda response: _capture_playwright_response(response, harvested_media))

            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=45000,
                )

            except PlaywrightTimeoutError:
                logger.warning("Playwright goto 超時，改用目前頁面")

            page.wait_for_timeout(3500)

            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass

            if _is_missing_ig_page(page):
                return "MISSING", "Instagram 顯示：很抱歉，此頁面無法使用；連結可能故障或頁面已遭移除"

            if _is_generic_ig_page(page):
                return "BLOCKED", "Playwright 看到的是 login / challenge / checkpoint 頁面，不是貼文主體"

            title = _get_ig_title(
                page,
                fallback_shortcode=shortcode or "Instagram_Post",
            )

            collected = []
            seen_media_keys = set()

            for _ in range(_MAX_CAROUSEL_ITEMS):
                current = _get_current_slide_main_media(page)

                if not current and not collected:
                    current = _get_meta_ig_media(page)[:1]

                if not current:
                    break

                item = current[0]
                src = item.get("src", "")
                key = _media_key_from_url(src)

                if key not in seen_media_keys:
                    seen_media_keys.add(key)
                    collected.append(item)

                moved = _click_next_ig(page)

                if not moved:
                    break

            filtered = _dedupe_media(collected)

            # Merge browser-harvested media as a non-invasive fallback.
            # This keeps the original DOM extraction intact while fixing carousel posts
            # where direct CDN requests are rejected even though the browser already
            # loaded the full images on screen.
            for harvested in sorted(harvested_media.values(), key=lambda x: x.get("score", 0), reverse=True):
                if not _looks_like_real_ig_media_url(harvested.get("src", "")):
                    continue
                h_key = _media_key_from_url(harvested.get("src", ""))
                exists = any(_media_key_from_url(x.get("src", "")) == h_key for x in filtered)
                if not exists:
                    filtered.append({
                        "src": harvested.get("src", ""),
                        "type": harvested.get("type", "image"),
                        "score": harvested.get("score", 0),
                        "from": "network",
                    })

            filtered = _dedupe_media(filtered)

            logger.info(f"IG filtered media count={len(filtered)}; network harvest={len(harvested_media)}")

            if not filtered:
                return "FAILED", "Playwright 頁面已開啟，但未抓到有效貼文主媒體"

            success_count = 0

            for i, item in enumerate(filtered, 1):
                media_type = item.get("type", "image")
                media_url = item.get("src", "")

                ext = (
                    ".mp4"
                    if media_type == "video" or any(x in media_url.lower() for x in [".mp4", ".m4v", ".mov"])
                    else _ext_from_url(media_url, ".jpg")
                )

                dst = os.path.join(TEMP_DIR, f"ig_{i}{ext}")
                media_key = _media_key_from_url(media_url)

                try:
                    harvested = harvested_media.get(media_key)
                    if harvested and harvested.get("body"):
                        _write_media_body(
                            dst,
                            harvested.get("body"),
                            media_url=media_url,
                            content_type=harvested.get("content_type", ""),
                        )
                        logger.info(f"IG 使用 browser network cache 寫入第 {i} 個媒體")
                    else:
                        _download_with_playwright_request(
                            context,
                            media_url,
                            dst,
                            referer=url,
                        )
                    success_count += 1

                except Exception as e:
                    logger.warning(f"IG 略過無效媒體: {media_url[:180]} | {e}")

            if success_count <= 0:
                return "FAILED", "Playwright 有抓到媒體 URL，但全部被判定為垃圾圖 / 非有效媒體"

            # Critical fix:
            # If Playwright / browser network cache has already written media files,
            # this task must not be overwritten by yt-dlp's expected image-post error
            # (No video formats found).  First try normal title-based move, then a
            # short shortcode fallback to avoid Windows path errors.
            temp_files_after_capture = _list_media_files(TEMP_DIR)
            logger.info(
                f"IG Playwright 已成功寫入 {success_count} 個媒體；"
                f"TEMP 有效檔案={len(temp_files_after_capture)}，準備搬移"
            )

            if move_files(title, fallback_name=shortcode or "Instagram_Post"):
                logger.info(f"確認 Playwright 成功擷取 {success_count} 個媒體資源")
                return "SUCCESS", ""

            # Last-resort physical confirmation: success_count > 0 means the browser
            # did write valid bytes.  If normal move failed due Windows path / lock,
            # attempt a forced shortcode move before declaring failure.
            if _list_media_files(TEMP_DIR):
                if move_files(shortcode or "Instagram_Post", fallback_name="Instagram_Post"):
                    logger.info(f"確認 Playwright 備援搬移成功：{success_count} 個媒體資源")
                    return "SUCCESS", ""

            return "FAILED", "Playwright 已寫入媒體，但搬移檔案失敗；請檢查下載資料夾權限或路徑長度"

    except Exception as e:
        return _classify_error(str(e))

    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass

        try:
            if browser:
                browser.close()
        except Exception:
            pass


def download(url: str):
    if _L is None:
        setup()

    # 每筆 IG 任務開始前清理上一筆失敗 / 中斷殘留的 post/ 暫存檔。
    # 注意：SUCCESS 的清理由 move_files() 負責，避免誤刪正在搬移中的媒體。
    clear_temp()

    result_box = [(None, None)]

    def _run():
        normalized_url = _normalize_ig_url(url)
        is_reel = _is_ig_reel_url(normalized_url)
        is_post = _is_ig_post_url(normalized_url)

        instaloader_status = None
        instaloader_error = ""

        try:
            status, error = _collect_from_instaloader_shortcode(normalized_url)
            result_box[0] = (status, error)
            return

        except Exception as e:
            instaloader_status = "FAILED"
            instaloader_error = str(e)
            logger.warning(f"instaloader 失敗: {e}")

        # 圖文貼文優先走 Playwright。
        # 舊版流程是 instaloader 失敗後先跑 yt-dlp，多個 format/cookie 變體會反覆噴
        # No video formats found / empty media response，導致大量 BLOCKED 與等待。
        if is_post and not is_reel:
            status3, error3 = _collect_ig_media_playwright(normalized_url)

            if status3 in {"SUCCESS", "MISSING", "BLOCKED", "RETRY"}:
                result_box[0] = (status3, error3)
                return

            # Playwright 也失敗時，才讓 yt-dlp 做一次快速備援。
            status2, error2 = _download_via_ytdlp(normalized_url, quick=True)

            if status2 == "SUCCESS":
                result_box[0] = (status2, error2)
                return

            result_box[0] = _prefer_final_status(
                (status3, error3),
                (status2, error2),
                (instaloader_status, instaloader_error),
            )
            return

        # Reel / 影片仍然維持 yt-dlp 優先，失敗再用 Playwright。
        status2, error2 = _download_via_ytdlp(normalized_url, quick=False)

        if status2 == "SUCCESS":
            result_box[0] = (status2, error2)
            return

        status3, error3 = _collect_ig_media_playwright(normalized_url)

        if status3 == "SUCCESS":
            result_box[0] = (status3, error3)
            return

        result_box[0] = _prefer_final_status(
            (status3, error3),
            (status2, error2),
            (instaloader_status, instaloader_error),
        )

    t = threading.Thread(
        target=_run,
        daemon=True,
    )

    t.start()
    t.join(_DL_TIMEOUT)

    if t.is_alive():
        logger.error(f"Instagram 下載超時: {url}")
        clear_temp()
        return "RETRY", f"下載超時 ({_DL_TIMEOUT}s)"

    result = result_box[0] or ("FAILED", "未知錯誤")
    status, reason = result

    if status != "SUCCESS":
        _clear_temp_after_terminal_failure(status, reason)

    return result
