import html
import http.cookiejar
import os
import re
import shutil
import subprocess
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


def _clean_error_text(err: str) -> str:
    """Remove terminal ANSI color codes and normalize whitespace before classification."""
    text = html.unescape(str(err or ""))
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_soft_retry_ig_error(err: str) -> bool:
    """
    Errors that are usually transient IG anti-bot / lazy-load / empty-media cases.

    These must not become permanent FAILED or hard BLOCKED unless Playwright
    actually sees login / checkpoint / challenge / private / missing-page content.
    """
    e = _clean_error_text(err).lower()
    return any(k in e for k in [
        "instagram sent an empty media response",
        "empty media response",
        "requested content is not available, rate-limit reached or login required",
        "rate-limit reached or login required",
        "playwright 頁面已開啟，但未抓到有效貼文主媒體",
        "playwright 頁面已開啟，但本輪未抓到有效貼文主媒體",
        "playwright 有抓到媒體 url，但全部被判定為垃圾圖",
        "fetching post metadata failed",
        "json query to graphql/query",
        "expecting value: line 1 column 1",
    ])


def _prefer_final_status(*pairs):
    """
    多引擎結果合併規則：
    - SUCCESS 已在呼叫端先 return
    - MISSING 優先，代表頁面真的消失，不重試
    - RETRY 次優先，代表暫時性風控 / timeout，之後可補跑
    - BLOCKED 僅在明確 Playwright login / checkpoint / audience restricted 時保留
    - yt-dlp empty media / mixed login-required help text 不視為永久 FAILED / BLOCKED
    """
    statuses = []
    for st, err in pairs:
        if not st:
            continue
        normalized = "MISSING" if st == "UNAVAILABLE" else (st or "FAILED")
        statuses.append((normalized, _clean_error_text(err or "")))

    for st, err in statuses:
        if st == "MISSING":
            return "MISSING", err

    for st, err in statuses:
        if st == "RETRY":
            return "RETRY", err

    # If any engine produced a soft/transient IG empty-media signal, keep it
    # retryable instead of polluting FAILED.  Actual Playwright login/checkpoint
    # pages are returned as BLOCKED earlier by _collect_ig_media_playwright().
    for st, err in statuses:
        if _is_soft_retry_ig_error(err):
            return "RETRY", err or "Instagram 暫時空回應 / lazy-load 未載入媒體，建議稍後重試"

    for st, err in statuses:
        if st == "BLOCKED":
            return "BLOCKED", err

    if statuses:
        joined = " | ".join([f"{st}={err}" for st, err in statuses])
        return "FAILED", joined

    return "FAILED", "未知錯誤"


def _classify_error(err: str):
    raw = _clean_error_text(err)
    e = raw.lower()

    # yt-dlp / Instaloader frequently report generic cookie/login guidance when IG
    # returns an empty media body.  That is not a confirmed permission BLOCKED case.
    # Keep it retryable unless Playwright has actually seen login/checkpoint/challenge.
    if _is_soft_retry_ig_error(raw):
        return "RETRY", raw

    if _is_ytdlp_non_video_post_error(e):
        return "FAILED", raw

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
        return "RETRY", raw

    if any(k in e for k in [
        "404",
        "not found",
        "page not found",
        "內容不存在",
        "deleted",
    ]):
        return "MISSING", raw

    if any(k in e for k in [
        "checkpoint",
        "challenge",
        "private profile",
        "privateprofile",
        "specific audience",
        "not available to everyone",
        "特定受眾無法查看",
        "此內容並未開放所有人查看",
        "for users aged",
        "restricted",
        "generic instagram",
        "accounts/login",
    ]):
        return "BLOCKED", raw

    # Avoid treating yt-dlp's generic "--cookies / login required" help text as a
    # hard block.  Real login pages are detected in _is_generic_ig_page().
    if any(k in e for k in [
        "requires login",
        "login required",
        "sign in",
        "use --cookies-from-browser",
        "use --cookies",
    ]):
        return "RETRY", raw

    return "FAILED", raw


def _load_netscape_cookies(path: str, domain_keyword: str):
    cookies = []

    if not path or not os.path.exists(path):
        return cookies

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()

                if not line:
                    continue

                # Netscape cookie files often store HttpOnly cookies as
                # #HttpOnly_.instagram.com.  Do not treat those as comments;
                # sessionid is commonly HttpOnly and is required for logged-in IG.
                http_only = False
                if line.startswith("#HttpOnly_"):
                    line = line.replace("#HttpOnly_", "", 1)
                    http_only = True
                elif line.startswith("#"):
                    continue

                parts = line.split("\t")

                if len(parts) != 7:
                    continue

                domain, _, cookie_path, secure_flag, expires, name, value = parts

                if domain_keyword not in domain:
                    continue

                clean_domain = domain

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


def _click_next_ig_locked(page, target_shortcode: str) -> bool:
    """Click Instagram carousel next while keeping the task scoped to one shortcode.

    This intentionally keeps the older broad selector strategy that worked for
    carousel flipping, but adds a guard immediately after each click.  If IG
    navigates from the requested /p/<shortcode>/ or /reel/<shortcode>/ to a
    different post/profile/recommendation page, collection stops before any media
    from the wrong post can be added.
    """
    before_url = ""
    try:
        before_url = page.url or ""
    except Exception:
        before_url = ""

    moved = _click_next_ig(page)
    if not moved:
        return False

    if target_shortcode and not _is_target_shortcode_context(page, target_shortcode):
        try:
            logger.warning(
                f"IG carousel scope guard: next click left target={target_shortcode}; "
                f"before={before_url}; after={page.url}; stop collecting to avoid wrong post"
            )
        except Exception:
            logger.warning(
                f"IG carousel scope guard: next click left target={target_shortcode}; stop collecting to avoid wrong post"
            )
        return False

    return True


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



def _try_get_config_value(name: str, default=None):
    try:
        import config  # local project config.py
        return getattr(config, name, default)
    except Exception:
        return default


def _get_project_ig_parser_profile_root() -> str:
    """Project-local dedicated Chrome user-data directory for IG fallback.

    This replaces the old external .bat profile isolation setup.  By default the
    downloader uses downloader_GUI/data/chrome_ig_parser as its own browser
    profile, so it does not compete with the user's daily Chrome Default profile.
    """
    configured = (
        os.environ.get("IG_CHROME_USER_DATA_DIR")
        or _try_get_config_value("IG_CHROME_USER_DATA_DIR", "")
        or ""
    )
    configured = str(configured).strip().strip('"')
    if configured:
        os.makedirs(configured, exist_ok=True)
        return configured

    root = os.path.join(DATA_DIR, "chrome_ig_parser")
    os.makedirs(root, exist_ok=True)
    return root


def _resolve_chrome_user_data_dir() -> str:
    """Resolve the dedicated IG parser Chrome profile root.

    Default is project-local data/chrome_ig_parser.  This is intentionally not the
    normal Chrome Default profile, so the downloader can run without closing the
    user's daily browser and without profile-lock blank-window issues.

    If a user explicitly wants to use the system Chrome profile, set:
      IG_ALLOW_SYSTEM_CHROME_PROFILE=1
    or define IG_CHROME_USER_DATA_DIR manually.
    """
    dedicated = _get_project_ig_parser_profile_root()
    if dedicated:
        return dedicated

    allow_system = (
        os.environ.get("IG_ALLOW_SYSTEM_CHROME_PROFILE")
        or str(_try_get_config_value("IG_ALLOW_SYSTEM_CHROME_PROFILE", ""))
    ).lower() in {"1", "true", "yes", "on"}

    if not allow_system:
        return ""

    candidates = []
    local_appdata = os.environ.get("LOCALAPPDATA") or ""
    appdata = os.environ.get("APPDATA") or ""

    if local_appdata:
        candidates.extend([
            os.path.join(local_appdata, "Google", "Chrome", "User Data"),
            os.path.join(local_appdata, "Microsoft", "Edge", "User Data"),
            os.path.join(local_appdata, "BraveSoftware", "Brave-Browser", "User Data"),
        ])

    if appdata:
        candidates.append(os.path.join(appdata, "Opera Software", "Opera Stable"))

    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate

    return ""


def _resolve_chrome_profile_directory() -> str:
    configured = (
        os.environ.get("IG_CHROME_PROFILE_DIRECTORY")
        or _try_get_config_value("IG_CHROME_PROFILE_DIRECTORY", "")
        or ""
    )
    configured = str(configured).strip().strip('"')
    return configured or "Default"


def open_ig_parser_profile(start_url: str = "https://www.instagram.com/") -> str:
    """Open the project-local IG_Parser Chrome profile for one-time login/trust setup.

    Called by the GUI button.  This replaces setup_ig_parser_profile.bat and keeps
    the workflow inside the app.
    """
    user_data_dir = _get_project_ig_parser_profile_root()
    profile_dir = _resolve_chrome_profile_directory()

    chrome = (
        shutil.which("chrome")
        or shutil.which("chrome.exe")
        or os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe")
        or os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe")
    )

    if not chrome or not os.path.exists(chrome):
        raise Exception("找不到 Google Chrome；請先安裝 Chrome，或把 chrome.exe 加入 PATH。")

    args = [
        chrome,
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        start_url or "https://www.instagram.com/",
    ]

    subprocess.Popen(args)
    return user_data_dir




def _get_persistent_manual_wait_seconds(default: int = 45) -> int:
    """Seconds to keep headed Chrome Profile fallback open for manual IG confirmation.

    Default is intentionally longer than the original 25s because age/audience
    restriction pages often need a visible browser round-trip before media
    requests are released.
    """
    raw = (
        os.environ.get("IG_PERSISTENT_MANUAL_WAIT_SECONDS")
        or _try_get_config_value("IG_PERSISTENT_MANUAL_WAIT_SECONDS", default)
        or default
    )
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(5, min(180, value))


def _manual_wait_persistent_profile(page, reason: str = ""):
    """Bring the visible Chrome fallback window forward and wait for user action."""
    wait_sec = _get_persistent_manual_wait_seconds()

    try:
        page.bring_to_front()
    except Exception:
        pass

    logger.info(
        "IG persistent fallback 視窗已開啟；若看到『未滿18歲 / 特定對象 / 確認觀看 / 登入驗證』提示，"
        f"請在 Chrome 視窗手動確認。等待 {wait_sec}s 後重新擷取媒體。reason={reason or 'manual-check'}"
    )

    try:
        page.wait_for_timeout(wait_sec * 1000)
    except Exception:
        time.sleep(wait_sec)

    try:
        page.bring_to_front()
    except Exception:
        pass

    _warmup_ig_page_for_media(page, is_reel=_is_ig_reel_url(page.url or ""))


def _get_persistent_context_page(context):
    """Reuse persistent context's first page instead of opening a second blank tab.

    launch_persistent_context usually creates an initial about:blank page.  If we
    immediately call new_page(), the visible front tab can remain blank while the
    real IG tab is hidden behind it.  Reusing the first page prevents the
    "Chrome window opens but shows nothing" symptom.
    """
    try:
        pages = list(context.pages)
    except Exception:
        pages = []

    if pages:
        page = pages[0]
    else:
        page = context.new_page()

    try:
        page.bring_to_front()
    except Exception:
        pass

    # Close extra blank pages only.  Do not close pages with non-blank URLs because
    # the user's profile may restore legitimate tabs.
    for extra in pages[1:]:
        try:
            extra_url = (extra.url or "").lower()
            if extra_url in {"about:blank", "chrome://newtab/"}:
                extra.close()
        except Exception:
            pass

    return page
def _is_ig_audience_restricted_page(page) -> bool:
    """Detect IG age/audience restriction pages.

    These are not MISSING.  They mean cookies.txt/headless context is not trusted
    enough, while the user's real browser profile may still be able to view it.
    """
    body = ""
    title = ""

    try:
        body = page.locator("body").first.inner_text(timeout=2500) or ""
    except Exception:
        body = ""

    try:
        title = page.title() or ""
    except Exception:
        title = ""

    text = f"{body}\n{title}".lower()

    markers = [
        "未滿18歲的用戶無法觀看此內容",
        "未滿 18 歲的用戶無法觀看此內容",
        "僅向特定對象顯示其個人檔案和內容",
        "僅向特定對象顯示",
        "此帳號已設定限制",
        "age-restricted",
        "age restricted",
        "not available to everyone",
        "specific audience",
        "restricted profile",
        "restricted account",
    ]

    return any(marker.lower() in text for marker in markers)


def _warmup_ig_page_for_media(page, is_reel: bool = False):
    """Nudge IG's lazy-loaded media to appear in DOM/network."""
    try:
        page.wait_for_timeout(900)
    except Exception:
        pass

    for js in [
        "() => window.scrollBy(0, 260)",
        "() => document.querySelector('article, main, div[role=\"dialog\"]')?.scrollIntoView({block:'center'})",
        "() => { const v=document.querySelector('video'); if (v) { try { v.muted=true; v.play().catch(()=>{}); } catch(e){} } }",
    ]:
        try:
            page.evaluate(js)
            page.wait_for_timeout(700)
        except Exception:
            pass

    try:
        page.mouse.click(700, 460)
        page.wait_for_timeout(900)
    except Exception:
        pass

    if is_reel:
        try:
            page.keyboard.press("Space")
            page.wait_for_timeout(1500)
        except Exception:
            pass

    try:
        page.wait_for_load_state("networkidle", timeout=6500)
    except Exception:
        pass


def _get_video_current_sources(page):
    items = []
    try:
        raw = page.evaluate(
            """
            () => {
              const out = [];
              for (const v of Array.from(document.querySelectorAll('video'))) {
                const urls = [
                  v.currentSrc || '',
                  v.src || '',
                  v.getAttribute('src') || ''
                ];
                for (const s of Array.from(v.querySelectorAll('source'))) {
                  urls.push(s.src || s.getAttribute('src') || '');
                }
                for (const u of urls) {
                  if (u) out.push(u);
                }
              }
              return out;
            }
            """
        ) or []
    except Exception:
        raw = []

    for src in raw:
        if _looks_like_real_ig_media_url(src):
            items.append({
                "src": src,
                "type": "video",
                "score": 1200000 + _media_quality_score(src),
            })

    return _dedupe_media(items)


def _get_performance_ig_media(page):
    items = []
    try:
        raw = page.evaluate(
            """
            () => performance.getEntriesByType('resource')
              .map(e => e.name || '')
              .filter(Boolean)
              .slice(-800)
            """
        ) or []
    except Exception:
        raw = []

    for src in raw:
        if _looks_like_real_ig_media_url(src):
            media_type = "video" if any(x in src.lower() for x in [".mp4", ".m4v", ".mov"]) else "image"
            items.append({
                "src": src,
                "type": media_type,
                "score": 700000 + _media_quality_score(src),
            })

    return _dedupe_media(items)


def _extract_ig_media_from_text(text: str):
    items = []
    if not text:
        return items

    decoded = html.unescape(str(text))
    decoded = decoded.replace("\\u0026", "&").replace("\\/", "/").replace("\\/", "/")

    patterns = [
        r'https?://[^"\'<>\s]+?(?:\.mp4|\.m4v|\.mov|\.jpg|\.jpeg|\.png|\.webp)(?:\?[^"\'<>\s]*)?',
        r'https?://[^"\'<>\s]+?(?:cdninstagram\.com|fbcdn\.net|instagram\.f)[^"\'<>\s]+',
    ]

    for pat in patterns:
        for m in re.finditer(pat, decoded, flags=re.I):
            src = html.unescape(unquote(m.group(0)))
            if _looks_like_real_ig_media_url(src):
                media_type = "video" if any(x in src.lower() for x in [".mp4", ".m4v", ".mov"]) else "image"
                items.append({
                    "src": src,
                    "type": media_type,
                    "score": 500000 + _media_quality_score(src),
                })

    return _dedupe_media(items)


def _download_filtered_items_from_context(context, filtered, harvested_media, title: str, shortcode: str, referer: str):
    """Write filtered IG media items and move them using existing move_files()."""
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
                    referer=referer,
                )
            success_count += 1

        except Exception as e:
            logger.warning(f"IG 略過無效媒體: {media_url[:180]} | {e}")

    if success_count <= 0:
        return "RETRY", "Playwright 有抓到媒體 URL，但本輪全部下載失敗或過小；可能是 IG CDN 暫時空回應，建議稍後重試"

    temp_files_after_capture = _list_media_files(TEMP_DIR)
    logger.info(
        f"IG Playwright 已成功寫入 {success_count} 個媒體；"
        f"TEMP 有效檔案={len(temp_files_after_capture)}，準備搬移"
    )

    if move_files(title, fallback_name=shortcode or "Instagram_Post"):
        logger.info(f"確認 Playwright 成功擷取 {success_count} 個媒體資源")
        return "SUCCESS", ""

    if _list_media_files(TEMP_DIR):
        if move_files(shortcode or "Instagram_Post", fallback_name="Instagram_Post"):
            logger.info(f"確認 Playwright 備援搬移成功：{success_count} 個媒體資源")
            return "SUCCESS", ""

    return "FAILED", "Playwright 已寫入媒體，但搬移檔案失敗；請檢查下載資料夾權限或路徑長度"


def _is_target_shortcode_context(page, target_shortcode: str) -> bool:
    """Return True only when the visible page is still the requested post/reel.

    IG restricted posts can briefly redirect from /p/<shortcode>/ to an account
    grid.  If we keep scanning performance/html/network in that state, we may
    collect the whole account or the "more posts" recommendations.  This guard
    keeps a task scoped to the original shortcode.
    """
    if not target_shortcode:
        return True

    current = ""
    try:
        current = page.url or ""
    except Exception:
        current = ""

    if f"/p/{target_shortcode}" in current or f"/reel/{target_shortcode}" in current or f"/reels/{target_shortcode}" in current:
        return True

    try:
        canonical = page.locator('link[rel="canonical"]').first.get_attribute("href") or ""
        if target_shortcode in canonical:
            return True
    except Exception:
        pass

    try:
        og_url = page.locator('meta[property="og:url"]').first.get_attribute("content") or ""
        if target_shortcode in og_url:
            return True
    except Exception:
        pass

    return False


def _collect_visible_target_media(page, target_shortcode: str, include_meta: bool = True):
    """Collect only the visible post/reel media, never profile-grid media.

    Based on the older working carousel flow, but every round is guarded by the
    requested shortcode.  This preserves the working flip behavior while avoiding
    the regression where a /p/ task navigates into another post or profile grid.
    """
    if not _is_target_shortcode_context(page, target_shortcode):
        try:
            logger.warning(
                f"IG shortcode scope guard: page left target shortcode={target_shortcode}; "
                f"current_url={page.url}，停止掃描帳號頁/推薦貼文"
            )
        except Exception:
            pass
        return []

    collected = []
    seen_media_keys = set()

    for round_index in range(_MAX_CAROUSEL_ITEMS):
        if not _is_target_shortcode_context(page, target_shortcode):
            try:
                logger.warning(
                    f"IG shortcode scope guard: before collect round={round_index}, "
                    f"page left target={target_shortcode}; current_url={page.url}; stop before wrong post"
                )
            except Exception:
                pass
            break

        current = _get_current_slide_main_media(page)
        if not current and include_meta and not collected:
            current = _get_meta_ig_media(page)[:1]
        if not current:
            break

        item = current[0]
        src = item.get("src", "")
        key = _media_key_from_url(src)

        if key not in seen_media_keys:
            seen_media_keys.add(key)
            collected.append(item)

        moved = _click_next_ig_locked(page, target_shortcode)
        if not moved:
            break

    return _dedupe_media(collected)


def _collect_limited_target_fallback_media(page, harvested_media, target_shortcode: str, allow_broad_fallback: bool = False):
    """Fallback media collection that is safe for single post/reel tasks.

    Performance/html/network sources are broad and can contain profile-grid or
    recommendation images.  They are allowed only when explicitly requested and
    the page is still scoped to the original shortcode.  The result is capped so
    it cannot turn a single /p/ task into an account dump.
    """
    if not _is_target_shortcode_context(page, target_shortcode):
        return []

    items = []
    items.extend(_get_video_current_sources(page))
    items.extend(_get_meta_ig_media(page)[:1])

    if allow_broad_fallback:
        items.extend(_get_performance_ig_media(page)[:3])
        try:
            items.extend(_extract_ig_media_from_text(page.content())[:3])
        except Exception:
            pass
        for harvested in sorted(harvested_media.values(), key=lambda x: x.get("score", 0), reverse=True)[:3]:
            if _looks_like_real_ig_media_url(harvested.get("src", "")):
                items.append({
                    "src": harvested.get("src", ""),
                    "type": harvested.get("type", "image"),
                    "score": harvested.get("score", 0),
                    "from": "network",
                })

    # If the DOM did not expose a carousel, do not allow broad fallback to exceed
    # a tiny number.  This prevents downloading "more posts from this account".
    return _dedupe_media(items)[:3]


def _collect_ig_media_playwright_persistent(p, url: str, reason: str = ""):
    """Fallback using the project-local logged-in Chrome profile.

    This version is strictly shortcode-scoped.  It never scans or finalizes media
    after IG redirects the visible page to a profile/account grid.
    """
    enabled = _try_get_config_value("IG_PERSISTENT_PROFILE_ENABLED", True)
    if str(enabled).lower() in {"0", "false", "no", "off"}:
        return "RETRY", "IG persistent Chrome profile fallback disabled"

    user_data_dir = _resolve_chrome_user_data_dir()
    profile_dir = _resolve_chrome_profile_directory()
    shortcode = _extract_shortcode(url) or ""

    if not shortcode:
        return "FAILED", "無法解析 Instagram shortcode；為避免誤抓整個帳號，已停止"

    if not user_data_dir:
        return "RETRY", "找不到 Chrome User Data；請先按『初始化 IG_Parser』完成專用 Profile 登入"

    context = None
    try:
        logger.info(
            f"IG 啟用已登入 Chrome Profile fallback: user_data_dir={user_data_dir}, "
            f"profile={profile_dir}, target_shortcode={shortcode}, reason={reason or 'network harvest=0'}"
        )

        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",
            headless=False,
            no_viewport=True,
            locale="zh-TW",
            args=[
                f"--profile-directory={profile_dir}",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--start-maximized",
            ],
        )

        try:
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        except Exception:
            pass

        harvested_media = {}
        page = _get_persistent_context_page(context) if '_get_persistent_context_page' in globals() else context.pages[0] if context.pages else context.new_page()
        page.on("response", lambda response: _capture_playwright_response(response, harvested_media))

        try:
            page.bring_to_front()
        except Exception:
            pass

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except PlaywrightTimeoutError:
            logger.warning("IG persistent profile goto 超時，改用目前頁面")
        except Exception as e:
            logger.warning(f"IG persistent profile goto 失敗，改用目前頁面: {e}")

        try:
            page.bring_to_front()
        except Exception:
            pass

        _warmup_ig_page_for_media(page, is_reel=_is_ig_reel_url(url))

        if _is_missing_ig_page(page):
            return "MISSING", "Instagram 顯示：很抱歉，此頁面無法使用；連結可能故障或頁面已遭移除"

        if not _is_target_shortcode_context(page, shortcode):
            return "RETRY", (
                f"IG 頁面已離開目標貼文 {shortcode}，目前可能停在帳號首頁或推薦區。"
                "為避免誤抓整個帳號，已停止本輪下載；請在專用 Chrome 直接開目標貼文並完成確認後重試。"
            )

        if _is_generic_ig_page(page) or _is_ig_audience_restricted_page(page):
            _manual_wait_persistent_profile(page, reason="age/audience/login page before harvest")

            if _is_missing_ig_page(page):
                return "MISSING", "Instagram 顯示：很抱歉，此頁面無法使用；連結可能故障或頁面已遭移除"
            if not _is_target_shortcode_context(page, shortcode):
                return "RETRY", (
                    f"手動確認後頁面離開目標貼文 {shortcode}；為避免誤抓整個帳號，已停止。"
                    "請重新貼目標貼文 URL 後重試。"
                )

        title = _get_ig_title(page, fallback_shortcode=shortcode or "Instagram_Post")

        visible_media = _collect_visible_target_media(page, shortcode)
        filtered = _dedupe_media(visible_media)

        # Only when visible DOM has no media do we use a very limited fallback.
        # Never append broad performance/html/network data after visible media,
        # otherwise IG profile grids and "more posts" thumbnails may be downloaded.
        if not filtered:
            filtered = _collect_limited_target_fallback_media(
                page,
                harvested_media,
                shortcode,
                allow_broad_fallback=True,
            )

        logger.info(
            f"IG persistent profile scoped media count={len(filtered)}; "
            f"visible={len(visible_media)}, network harvest={len(harvested_media)}, target={shortcode}"
        )

        if not filtered:
            _manual_wait_persistent_profile(page, reason="persistent scoped media count=0")
            if not _is_target_shortcode_context(page, shortcode):
                return "RETRY", (
                    f"二次掃描時頁面已離開目標貼文 {shortcode}；為避免誤抓整個帳號，已停止。"
                )
            visible_media = _collect_visible_target_media(page, shortcode)
            filtered = _dedupe_media(visible_media)
            if not filtered:
                filtered = _collect_limited_target_fallback_media(
                    page,
                    harvested_media,
                    shortcode,
                    allow_broad_fallback=True,
                )
            logger.info(
                f"IG persistent profile second-pass scoped media count={len(filtered)}; "
                f"visible={len(visible_media)}, network harvest={len(harvested_media)}, target={shortcode}"
            )

        if not filtered:
            return "RETRY", "已登入 IG_Parser Profile 但仍未抓到目標貼文媒體；請確認專用 Profile 已完成年齡/帳號驗證後重試"

        # Final post lock before writing files.  The collected media were gathered
        # only while the page was scoped to the target shortcode; if a later Next
        # click left the post, do not continue scanning or append anything else.
        # We still allow writing already collected scoped media instead of falling
        # back to yt-dlp, because yt-dlp cannot use the project-local IG_Parser
        # trust state.
        return _download_filtered_items_from_context(
            context,
            filtered,
            harvested_media,
            title,
            shortcode,
            referer=url,
        )

    except Exception as e:
        msg = str(e)
        if "user data directory is already in use" in msg.lower() or "process singleton" in msg.lower():
            return "RETRY", (
                "IG_Parser Chrome Profile 目前被同一個專用 Chrome 視窗佔用。"
                "請關閉 IG_Parser 視窗後重試；日常 Chrome 不需要關閉。"
            )
        return _classify_error(msg)

    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass


def _collect_ig_media_playwright(url: str):
    clear_temp()

    browser = None
    context = None

    url = _normalize_ig_url(url)
    shortcode = _extract_shortcode(url) or ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ],
            )

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

            try:
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            except Exception:
                pass

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

            _warmup_ig_page_for_media(page, is_reel=_is_ig_reel_url(url))

            if _is_missing_ig_page(page):
                return "MISSING", "Instagram 顯示：很抱歉，此頁面無法使用；連結可能故障或頁面已遭移除"

            if _is_ig_audience_restricted_page(page):
                status_p, error_p = _collect_ig_media_playwright_persistent(
                    p,
                    url,
                    reason="IG age/audience restricted page in cookies.txt context",
                )
                if status_p == "SUCCESS":
                    return status_p, error_p
                return status_p, error_p

            if _is_generic_ig_page(page):
                status_p, error_p = _collect_ig_media_playwright_persistent(
                    p,
                    url,
                    reason="IG login/challenge page in cookies.txt context",
                )
                if status_p == "SUCCESS":
                    return status_p, error_p
                return "BLOCKED", error_p or "Playwright 看到的是 login / challenge / checkpoint 頁面，不是貼文主體"

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

                moved = _click_next_ig_locked(page, shortcode)

                if not moved:
                    break

            filtered = _dedupe_media(collected)

            # Shortcode scope guard:
            # If the target post/reel DOM is visible, do not append broad
            # performance/html/network candidates.  Those often include profile-grid
            # thumbnails or "more posts" recommendations and can make one task look
            # like an account dump.  The network cache is still used later when a
            # filtered media URL has the same media key.
            fallback_video = []
            fallback_perf = []
            fallback_html = []
            if not filtered:
                fallback_video = _get_video_current_sources(page)
                if _is_target_shortcode_context(page, shortcode):
                    fallback_perf = _get_performance_ig_media(page)[:3]
                    try:
                        fallback_html = _extract_ig_media_from_text(page.content())[:3]
                    except Exception:
                        fallback_html = []
                filtered = _dedupe_media(fallback_video + fallback_perf + fallback_html)[:3]

            logger.info(
                f"IG filtered media count={len(filtered)}; network harvest={len(harvested_media)}; "
                f"fallback source counts: video_current={len(fallback_video)}, "
                f"performance={len(fallback_perf)}, html={len(fallback_html)}, target={shortcode}"
            )

            if not filtered:
                status_p, error_p = _collect_ig_media_playwright_persistent(
                    p,
                    url,
                    reason="normal Playwright network harvest=0",
                )
                if status_p == "SUCCESS":
                    return status_p, error_p
                return "RETRY", error_p or "Playwright 頁面已開啟，但本輪未抓到有效貼文主媒體；可能是 IG lazy-load / 暫時空回應，建議稍後重試"

            status_write, error_write = _download_filtered_items_from_context(
                context,
                filtered,
                harvested_media,
                title,
                shortcode,
                referer=url,
            )
            return status_write, error_write

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
                return "RETRY", "Playwright 有抓到媒體 URL，但本輪全部下載失敗或過小；可能是 IG CDN 暫時空回應，建議稍後重試"

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
