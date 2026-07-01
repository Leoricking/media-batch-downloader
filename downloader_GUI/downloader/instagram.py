# v12.09 Exact Shortcode Structured Scan + Best-Available Image Gate
# v12.08 Original CDN Variant Without Declared-Size Gate
# v12.07 Structured Original CDN Variant Fallback
# v12.06 Persistent Profile Headless-First Single Download Fix
# v12.05 Structured Candidate Build Exact-URL Fix
# v12.04 Exact Image Variant Retry Fix
import html
import json
import hashlib
import http.cookiejar
import os
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse, unquote, parse_qs, parse_qsl, urlencode, urlunparse

import instaloader
import requests
import yt_dlp
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config import DOWNLOAD_DIR, TEMP_DIR, DATA_DIR, COOKIES_FILE

try:
    from config import IG_PARSER_PROFILE_DIR
except Exception:
    IG_PARSER_PROFILE_DIR = os.path.join(DATA_DIR, "chrome_ig_parser")
from utils.filename import safe_title
from utils.logger import get_logger

try:
    from opencc import OpenCC
except Exception:
    OpenCC = None

_cc = OpenCC("s2t") if OpenCC else None

try:
    from PIL import Image
except Exception:
    Image = None


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

# v11.90 Structured Shortcode Ownership Fix
# v12.03 Latest Profile Flow + Structured High-Resolution Retry
# v11.98 Profile Backend URL Harvest + Sequential Queue Expansion
# v11.97 Profile Headless-First Persistent Scan
# v11.96 Profile Verified Click Scan Fix
# v11.95 Profile Grid Anchor + Declared Count Fix
# v11.94 Profile Grid Exact-Scope Fix
# v11.93 Persistent Profile After Quality-Reject Fix
# v11.88 Final Hard-Gated Exact Media Pipeline
# v11.87 Verified Carousel Walk Final
# v11.86 Carousel Probe-Until-End Lock
# v11.85 Complete Carousel Direct-Index Collection
# v11.84 Final Restored Stable Flow
# v11.78.2 Git-OK Naming Rule Restore
# v11.78.1 Structured Caption/Account Fix
# v11.78 Authenticated Structured Extraction (No Carousel Flipping)
# v11.66.6 Small Init-Segment Capture + Fragment Assembly
# v11.66.5 Requested-Slide Video Prime + Init Segment Capture
# v11.66.4 Browser Fragment Assembly + Complete Video Validation
# v11.66.3 Complete MP4 Range Rebuild + No False SUCCESS
# v11.66.2 Reject Fragment-Only MP4 Cache
# v11.66.1 Single-Point Requested-Slide Video Replacement
# v11.66 Restore v11.64 Baseline + Safe Title/Media Cache Fix
# v11.64 Target-Dialog Lock + No-Scroll Carousel Walk
# v11.63 Main-Media Focus + Verified ArrowRight Fallback
# v11.62 Restore Carousel Hover/Selector Helpers
# v11.61 Persistent Carousel Failure Isolation
# v11.60 Spatially-Locked Carousel Next Click
# v11.59 Trusted Locator Click + Global Carousel Scope
# v11.58 Visible Arrow Coordinate Click + Visual Frame Verification
# v11.57 Real Mouse Next-Arrow + Slide-State Verification
# v11.56 Age-Restricted Profile Unlock + Carousel Gesture Fallback
# v11.55 Restricted Carousel Robust Next Click
# v11.52 Canonical First-Slide Navigation Lock
# v11.50 Dynamic Carousel End-Walk Lock
# v11.49 Sponsored Carousel Full-Slide Lock

# v11.48 IG Caption Lock + Full-Frame Media Validation

_MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".mp4", ".webp", ".m4v", ".mov"}
_DL_TIMEOUT = 300
_MAX_CAROUSEL_ITEMS = 40
_MIN_FILE_SIZE = 5 * 1024

_L = None
_cookie_file = None
_is_logged_in = False
_LAST_CAROUSEL_EXPECTED_COUNT = 0
_LAST_CAROUSEL_TARGET = ""
_LAST_CAROUSEL_WALK_COMPLETE = True

# v11.38 Title Prefetch Before Download + v11.37 Full-frame Carousel Capture
# v11.34 profile-batch context: shortcode -> username map for downloads/<username>/ output.
_PROFILE_SHORTCODE_OWNER: dict[str, str] = {}
_DOWNLOAD_CONTEXT = threading.local()
_PREFETCHED_TITLES: dict[str, str] = {}
_PREFETCHED_TITLES_LOCK = threading.RLock()
_PREFETCHED_ACCOUNTS: dict[str, str] = {}
_PREFETCHED_ACCOUNTS_LOCK = threading.RLock()


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
    """Normalize IG content URL while preserving img_index routing context."""
    shortcode = _extract_shortcode(url)

    if not shortcode:
        return url

    if "/reel/" in url or "/reels/" in url:
        return f"https://www.instagram.com/reel/{shortcode}/"

    normalized = f"https://www.instagram.com/p/{shortcode}/"

    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query or "")
        img_index = (query.get("img_index") or [""])[0].strip()
        if img_index.isdigit() and int(img_index) > 0:
            normalized += "?" + urlencode({"img_index": img_index})
    except Exception:
        pass

    return normalized



def _get_requested_img_index(url: str) -> int:
    """Return the requested Instagram carousel index from the original URL."""
    try:
        parsed = urlparse(url or "")
        query = parse_qs(parsed.query or "")
        raw = (query.get("img_index") or ["1"])[0]
        value = int(str(raw).strip())
        return value if value > 0 else 1
    except Exception:
        return 1


def _has_visible_carousel_next(page) -> bool:
    """Detect a visible carousel next control without relying on IG CSS classes."""
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
            for i in range(min(loc.count(), 4)):
                if loc.nth(i).is_visible(timeout=250):
                    return True
        except Exception:
            continue
    return False


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

    # Signed Instagram CDN URLs can omit .jpg/.mp4 in the visible path.
    # Static/profile/icon resources were already rejected above.
    return True


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


def _dedupe_media(items, preserve_order: bool = False):
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

    if preserve_order:
        return out

    return sorted(out, key=lambda x: x.get("score", 0), reverse=True)


def _natural_media_sort_key(path: str):
    """Sort ig_1, ig_2, ..., ig_10 in numeric carousel order.

    A normal string sort puts ig_10 before ig_2, which can scramble final
    move_files() numbering even when the downloader wrote temp files in the
    correct carousel order.  Keep this helper local and conservative so it
    does not affect download logic outside file ordering.
    """
    name = os.path.basename(path or "").lower()
    parts = re.split(r"(\d+)", name)
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return key


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

    out.sort(key=_natural_media_sort_key)
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



def _remember_profile_child_owner(username: str, urls: list[str]) -> None:
    username = (username or "").strip()
    if not username:
        return
    for u in urls or []:
        sc = _extract_shortcode(u)
        if sc:
            _PROFILE_SHORTCODE_OWNER[sc] = username


def _get_profile_owner_for_url(url: str) -> str:
    sc = _extract_shortcode(url)
    if not sc:
        return ""
    return _PROFILE_SHORTCODE_OWNER.get(sc, "") or ""


def _set_current_profile_output_owner(owner: str) -> None:
    owner = (owner or "").strip()
    if owner:
        try:
            _DOWNLOAD_CONTEXT.profile_owner = _safe_output_name(owner, "Instagram_Profile", max_len=48)
        except Exception:
            _DOWNLOAD_CONTEXT.profile_owner = owner[:48]
    else:
        try:
            _DOWNLOAD_CONTEXT.profile_owner = ""
        except Exception:
            pass


def _get_current_download_root() -> str:
    try:
        owner = getattr(_DOWNLOAD_CONTEXT, "profile_owner", "") or ""
    except Exception:
        owner = ""
    if owner:
        return os.path.join(DOWNLOAD_DIR, owner)
    return DOWNLOAD_DIR

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

    output_root = _get_current_download_root()
    os.makedirs(output_root, exist_ok=True)

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
                final_ext = _real_ext_for_file(src)

                dst = _unique_path(os.path.join(output_root, f"{name}{final_ext}"))
                shutil.move(src, dst)

                logger.info(f"IG 單檔完成: {os.path.basename(dst)}")

            else:
                folder = _unique_path(os.path.join(output_root, name))
                os.makedirs(folder, exist_ok=True)

                for i, src in enumerate(files, 1):
                    ext = os.path.splitext(src)[1].lower()
                    final_ext = _real_ext_for_file(src)

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

    downloaded = _list_media_files(TEMP_DIR)

    if _is_ig_reel_url(url):
        videos = [
            path for path in downloaded
            if _real_ext_for_file(path) == ".mp4"
        ]
        if not videos:
            clear_temp()
            raise Exception(
                "instaloader Reel 只取得封面縮圖，未取得真正 MP4"
            )
        for path in downloaded:
            if path not in videos:
                try:
                    os.remove(path)
                except Exception:
                    pass

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

                downloaded = _list_media_files(TEMP_DIR)

                if is_reel:
                    videos = [
                        path for path in downloaded
                        if _real_ext_for_file(path) == ".mp4"
                    ]
                    if not videos:
                        clear_temp()
                        last_error = "yt-dlp Reel 只取得封面縮圖，未取得真正 MP4"
                        continue
                    for path in downloaded:
                        if path not in videos:
                            try:
                                os.remove(path)
                            except Exception:
                                pass

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

def _clean_ig_caption_candidate(raw: str, fallback_shortcode: str = "") -> str:
    """Convert IG meta/DOM text into the real post caption.

    Rejects Instagram UI prompts such as "絕不錯過 <account> 的任何貼文" and
    strips engagement/author/date prefixes from og:description.  The remaining
    text is the actual caption, including secondary lines such as sponsorship
    notes when they are part of the post body.
    """
    if not raw:
        return ""

    text = html.unescape(str(raw)).replace("\\n", "\n").replace("\r", "\n")
    text = text.replace("\u200b", " ").strip()

    # Typical Instagram metadata:
    # 336 likes, 23 comments - jessterific on/於 May 8, 2026: "caption"
    # 122 likes, 0 comments - account on ...: "caption"
    prefix_patterns = [
        r'^\s*[\d.,]+(?:[KMB萬千])?\s+likes?\s*,\s*[\d.,]+(?:[KMB萬千])?\s+comments?\s*-\s*[^:：]{1,120}\s+(?:on|於)\s+[^:：]{1,100}\s*[:：]\s*["“]?',
        r'^\s*[\d.,]+(?:[KMB萬千])?\s*(?:個)?讚\s*[，,]\s*[\d.,]+(?:[KMB萬千])?\s*(?:則)?留言\s*-\s*[^:：]{1,120}\s*(?:於|在)\s+[^:：]{1,100}\s*[:：]\s*["“]?',
        r'^\s*[^\n:：]{1,80}\s+(?:on|在)\s+Instagram\s*[:：]\s*["“]?',
    ]
    for pat in prefix_patterns:
        text = re.sub(pat, "", text, count=1, flags=re.I)

    # Remove only the wrapping metadata quote, not quotation marks inside caption.
    text = text.strip()

    # Restricted/profile-backed posts can expose only engagement/date metadata,
    # e.g. "2,529 likes, 20 comments - tjztimes 於 March 17, 2026".
    # This is not the caption and must not become the output folder name.
    engagement_only_patterns = [
        r'^\s*[\d.,]+(?:[KMB萬千])?\s+likes?\s*,\s*[\d.,]+(?:[KMB萬千])?\s+comments?\s*-\s*[^:：]{1,120}\s+(?:on|於)\s+.+$',
        r'^\s*[\d.,]+(?:[KMB萬千])?\s*(?:個)?讚\s*[，,]\s*[\d.,]+(?:[KMB萬千])?\s*(?:則)?留言\s*-\s*[^:：]{1,120}\s*(?:於|在)\s+.+$',
    ]
    if any(re.fullmatch(pat, text, flags=re.I) for pat in engagement_only_patterns):
        return ""
    if len(text) >= 2 and text[0] in {'"', '“'} and text[-1] in {'"', '”'}:
        text = text[1:-1].strip()
    else:
        text = text.lstrip('"“').rstrip('"”').strip()

    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        low = line.lower()
        if re.search(r"絕不錯過\s*[^\s]{1,60}\s*的任何貼文", line, flags=re.I):
            continue
        if re.search(r"never miss any posts? from", low):
            continue
        if low in {
            "instagram", "追蹤", "追蹤中", "follow", "following", "查看翻譯",
            "view translation", "尚無留言", "no comments yet", "開始對話",
        }:
            continue
        if re.fullmatch(r"[\d.,]+\s*(?:likes?|comments?|個讚|則留言)", low):
            continue
        lines.append(line)

    text = " ".join(lines).strip()
    if not text:
        return ""

    cleaned = _clean_title(text, fallback_shortcode or "Instagram_Post")
    if cleaned in {"Instagram_Post", fallback_shortcode}:
        return ""
    return cleaned


def _is_bad_ig_caption_candidate(text: str, fallback_shortcode: str = "") -> bool:
    if not text:
        return True
    low = text.lower().strip()
    if text in {"Instagram_Post", fallback_shortcode}:
        return True
    bad_markers = [
        "絕不錯過", "的任何貼文", "never miss any posts", "開始對話",
        "尚無留言", "view translation", "查看翻譯",
        "建立帳號或登入 instagram",
        "建立帳號或登入 instagram -",
        "與瞭解你的人分享你感興趣的內容",
        "create an account or log in to instagram",
        "share what you're into with the people who get you",
        "connect with friends, share what you're up to",
        "connect with friends",
        "see what's new from others all over the world",
        "see what’s new from others all over the world",
        "log in • instagram",
        "instagram photos and videos",
    ]
    if any(x.lower() in low for x in bad_markers):
        return True
    return False


def _get_ig_title(page, fallback_shortcode: str = ""):
    # Prefer description metadata because it contains the full caption and is not
    # polluted by follow prompts/comments in the visible sidebar.
    for sel in [
        'meta[property="og:description"]',
        'meta[name="description"]',
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
    ]:
        try:
            val = page.locator(sel).first.get_attribute("content") or ""
            clean = _clean_ig_caption_candidate(val, fallback_shortcode)
            if clean and not _is_bad_ig_caption_candidate(clean, fallback_shortcode):
                return clean
        except Exception:
            pass

    try:
        clean = _clean_ig_caption_candidate(page.title() or "", fallback_shortcode)
        if clean and not _is_bad_ig_caption_candidate(clean, fallback_shortcode):
            return clean
    except Exception:
        pass

    return fallback_shortcode or "Instagram_Post"


def _cache_prefetched_title(url: str, title: str) -> str:
    shortcode = _extract_shortcode(url) or ""
    clean = _safe_output_name(title, "", max_len=90).strip()

    if _is_bad_ig_caption_candidate(clean, shortcode):
        clean = ""

    if shortcode:
        with _PREFETCHED_TITLES_LOCK:
            if clean:
                _PREFETCHED_TITLES[shortcode] = clean
            else:
                _PREFETCHED_TITLES.pop(shortcode, None)

    return clean


def _get_prefetched_title(url: str) -> str:
    shortcode = _extract_shortcode(url) or ""
    if not shortcode:
        return ""
    with _PREFETCHED_TITLES_LOCK:
        return _PREFETCHED_TITLES.get(shortcode, "") or ""



def _extract_post_account_hint_from_url(url: str) -> str:
    """Return username from /<username>/p/<shortcode>/ style shared URLs."""
    try:
        parsed = urlparse(str(url or ""))
        parts = [part for part in (parsed.path or "").split("/") if part]
        if len(parts) >= 3 and parts[1].lower() in {"p", "reel", "reels"}:
            return _clean_ig_account(parts[0])
    except Exception:
        pass
    return ""



def _clean_ig_account(raw: str) -> str:
    text = html.unescape(str(raw or "")).strip().lstrip("@").strip()
    if not text:
        return ""
    m = re.search(r"(?:instagram\.com/)?([A-Za-z0-9._]{1,30})(?:/|$)", text, flags=re.I)
    if m and "/" in text:
        text = m.group(1)
    text = text.strip().lstrip("@").strip()
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", text):
        return ""
    if text.lower() in _IG_PROFILE_RESERVED_PATHS:
        return ""
    return text


def _cache_prefetched_account(url: str, account: str) -> str:
    shortcode = _extract_shortcode(url) or ""
    clean = _clean_ig_account(account)
    if shortcode and clean:
        with _PREFETCHED_ACCOUNTS_LOCK:
            _PREFETCHED_ACCOUNTS[shortcode] = clean
    return clean


def _get_prefetched_account(url: str) -> str:
    shortcode = _extract_shortcode(url) or ""
    if not shortcode:
        return ""
    with _PREFETCHED_ACCOUNTS_LOCK:
        return _PREFETCHED_ACCOUNTS.get(shortcode, "") or ""


def _get_ig_post_account(page) -> str:
    # Prefer the author link inside the target post header.  Avoid comment users
    # by checking the first few links nearest the article/dialog header.
    selectors = [
        'article header a[href^="/"]',
        'div[role="dialog"] header a[href^="/"]',
        'article a[href^="/"]',
        'div[role="dialog"] a[href^="/"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 12)):
                node = loc.nth(i)
                href = node.get_attribute("href") or ""
                account = _clean_ig_account(href)
                if not account:
                    text = node.inner_text(timeout=500) or ""
                    account = _clean_ig_account(text)
                if account:
                    return account
        except Exception:
            continue

    # Metadata fallback. Common examples include:
    # "successful101_official on Instagram: ..." or
    # "ju.littleshop • Instagram photos and videos".
    for sel in ['meta[property="og:title"]', 'meta[name="twitter:title"]']:
        try:
            value = page.locator(sel).first.get_attribute("content") or ""
            patterns = [
                r'^\s*([A-Za-z0-9._]{1,30})\s+(?:on|在)\s+Instagram',
                r'^\s*([A-Za-z0-9._]{1,30})\s*[•·-]\s*Instagram',
                r'@([A-Za-z0-9._]{1,30})',
            ]
            for pat in patterns:
                m = re.search(pat, value, flags=re.I)
                if m:
                    account = _clean_ig_account(m.group(1))
                    if account:
                        return account
        except Exception:
            pass
    return ""


def _publish_ig_task_account(task_url: str, account: str) -> None:
    clean = _clean_ig_account(account)
    if not clean:
        return
    try:
        import queue_manager
        queue_manager.update_task_account(task_url, clean)
    except Exception as e:
        logger.debug(f"IG task account publish skipped: {e}")


def prefetch_post_info(url: str) -> tuple[str, str, str]:
    """Resolve post caption and author account before media download.

    Returns: (title, account, error).  Both values are published to queue/UI as
    soon as they are available.
    """
    shortcode = _extract_shortcode(url) or ""
    if not shortcode:
        return "", "", "無法解析 Instagram shortcode"

    cached_title = _get_prefetched_title(url)
    cached_account = _get_prefetched_account(url)
    if cached_title and cached_account:
        _publish_ig_task_title(url, cached_title)
        _publish_ig_task_account(url, cached_account)
        return cached_title, cached_account, "cached"

    normalized = _normalize_ig_url(url)
    context = None
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--disable-infobars"],
            )
            context = browser.new_context(
                viewport={"width": 1400, "height": 980},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                locale="zh-TW",
            )
            cookie_path = _cookie_file if (_cookie_file and os.path.exists(_cookie_file)) else COOKIES_FILE
            cookies = _load_netscape_cookies(cookie_path, "instagram.com")
            if cookies:
                try:
                    context.add_cookies(cookies)
                except Exception:
                    pass
            try:
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            except Exception:
                pass

            page = context.new_page()
            try:
                _goto_instagram_target_clean(page, normalized, target_shortcode=shortcode, timeout=35000)
            except PlaywrightTimeoutError:
                logger.info(f"IG info prefetch goto timeout, use current page: {shortcode}")
            page.wait_for_timeout(1800)
            if _is_missing_ig_page(page):
                return "", "", "MISSING"
            if _is_generic_ig_page(page) or _is_ig_audience_restricted_page(page):
                return "", "", "需要 IG Parser Profile"

            title = cached_title or _get_ig_full_caption_title(page, fallback_shortcode=shortcode)
            account = cached_account or _get_ig_post_account(page)
            if _is_bad_ig_caption_candidate(title, shortcode):
                title = ""
            clean_title = _cache_prefetched_title(url, title)
            clean_account = _cache_prefetched_account(
                url,
                account or _extract_post_account_hint_from_url(url),
            )

            if clean_title and not _is_bad_ig_caption_candidate(clean_title, shortcode):
                _publish_ig_task_title(url, clean_title)
            if clean_account:
                _publish_ig_task_account(url, clean_account)

            if clean_title or clean_account:
                logger.info(
                    f"IG info prefetch completed before download: "
                    f"account={clean_account or 'unknown'}, title={clean_title or 'unknown'}"
                )
                return clean_title, clean_account, ""
            return "", "", "未取得有效標題或帳號"
    except Exception as e:
        logger.info(f"IG info prefetch skipped: {e}")
        return "", "", str(e)
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


def prefetch_post_title(url: str) -> tuple[str, str]:
    """Backward-compatible title-only API used by older worker versions."""
    title, _account, error = prefetch_post_info(url)
    return title, error


def _publish_ig_task_title(task_url: str, title: str) -> None:
    """Publish the resolved post caption to queue/UI without coupling downloader to GUI."""
    clean = _safe_output_name(title, "", max_len=90).strip()
    if not clean or _is_bad_ig_caption_candidate(clean, _extract_shortcode(task_url) or ""):
        return
    try:
        import queue_manager
        queue_manager.update_task_title(task_url, clean)
    except Exception as e:
        logger.debug(f"IG task title publish skipped: {e}")


def _expand_ig_caption_more(page) -> bool:
    """Expand Instagram's localized More button before reading a long caption."""
    selectors = [
        'button:has-text("更多")',
        'div[role="button"]:has-text("更多")',
        'span:has-text("更多")',
        'button:has-text("more")',
        'div[role="button"]:has-text("more")',
        'span:has-text("more")',
        'button:has-text("顯示更多")',
        'div[role="button"]:has-text("顯示更多")',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = min(loc.count(), 8)
            for i in range(count):
                item = loc.nth(i)
                try:
                    if not item.is_visible(timeout=250):
                        continue
                    txt = (item.inner_text(timeout=500) or "").strip().lower()
                    if txt not in {"更多", "more", "顯示更多"} and not txt.endswith("更多"):
                        continue
                    item.click(timeout=1200, force=True)
                    page.wait_for_timeout(650)
                    logger.info("IG caption 已點擊『更多 / more』展開完整內文")
                    return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def _get_ig_full_caption_title(page, fallback_shortcode: str = "") -> str:
    """Read the true post caption, preferring metadata over UI/sidebar text."""
    _expand_ig_caption_more(page)

    # Meta description is the most stable source for the actual caption and keeps
    # secondary lines such as 贊助 bk8 / 贊助 me88.  Clean the engagement prefix.
    for sel in ['meta[property="og:description"]', 'meta[name="description"]']:
        try:
            value = page.locator(sel).first.get_attribute("content") or ""
            clean = _clean_ig_caption_candidate(value, fallback_shortcode)
            if clean and not _is_bad_ig_caption_candidate(clean, fallback_shortcode):
                logger.info(f"IG post title resolved from metadata caption: {clean}")
                return clean
        except Exception:
            pass

    # DOM fallback: stay inside the post article/dialog and score meaningful text.
    candidates = []
    selectors = [
        'article h1',
        'div[role="dialog"] h1',
        'article ul li span[dir="auto"]',
        'article div[dir="auto"] span',
        'div[role="dialog"] ul li span[dir="auto"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            count = min(loc.count(), 20)
            for i in range(count):
                node = loc.nth(i)
                if not node.is_visible(timeout=300):
                    continue
                value = node.inner_text(timeout=800) or ""
                clean = _clean_ig_caption_candidate(value, fallback_shortcode)
                if not clean or _is_bad_ig_caption_candidate(clean, fallback_shortcode):
                    continue
                score = len(clean)
                if re.search(r"[，。！？!?]", clean):
                    score += 80
                if "贊助" in clean or "赞助" in clean:
                    score += 30
                candidates.append((score, clean))
        except Exception:
            continue

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        clean = candidates[0][1]
        logger.info(f"IG post title resolved from scoped DOM caption: {clean}")
        return clean

    return _get_ig_title(page, fallback_shortcode=fallback_shortcode)

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
    """Return the visible slide's full-frame, highest-resolution media URL.

    Instagram often exposes square/cropped CDN variants alongside the real 4:5
    source.  This resolver loads candidate dimensions in-page and strongly
    prefers the URL whose intrinsic aspect ratio matches the visible media frame.
    """
    js = r"""
    async () => {
      const scopes = [];
      const dialog = document.querySelector('div[role="dialog"]');
      const article = document.querySelector('article');
      if (dialog) scopes.push(dialog);
      if (article) scopes.push(article);
      if (!scopes.length) scopes.push(document);

      function bad(low) {
        return [
          'static.cdninstagram.com','/rsrc.php','instagram.com/static','profile_pic',
          's150x150','s100x100','s32x32','s40x40','s50x50','s64x64',
          'emoji','sprite','icon','logo','t51.2885-19','t51.82787-19',
          '_nc_sid=bf7eb4','favicon','apple-touch-icon'
        ].some(x => low.includes(x));
      }

      function visible(el) {
        const r = el.getBoundingClientRect();
        const st = getComputedStyle(el);
        return st.display !== 'none' && st.visibility !== 'hidden' &&
          parseFloat(st.opacity || '1') > 0 && r.width >= 120 && r.height >= 120 &&
          r.right > -40 && r.bottom > -40 && r.left < innerWidth + 40 && r.top < innerHeight + 40;
      }

      const raw = [];
      for (const scope of scopes) {
        const nodes = Array.from(scope.querySelectorAll('img, video')).filter(visible);
        for (const el of nodes) {
          const r = el.getBoundingClientRect();
          const frameRatio = r.height ? r.width / r.height : 0;
          const centerX = r.left + r.width / 2;
          const centerY = r.top + r.height / 2;
          const mediaCenterX = r.left < innerWidth * 0.55 ? innerWidth * 0.33 : innerWidth / 2;
          const mediaCenterY = innerHeight / 2;
          const dx = Math.abs(centerX - mediaCenterX);
          const dy = Math.abs(centerY - mediaCenterY);
          const overlapX = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
          const overlapY = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
          const base = overlapX * overlapY * 8 + r.width * r.height - dx * 6000 - dy * 80;

          const urls = new Map();
          const add = (u, bonus=0) => {
            u = (u || '').trim();
            if (!u) return;
            const low = u.toLowerCase();
            if (bad(low)) return;
            if (!(low.includes('.mp4') || low.includes('.m4v') || low.includes('.mov') ||
                  low.includes('cdninstagram.com') || low.includes('fbcdn.net') || low.includes('instagram.f'))) return;
            urls.set(u, Math.max(urls.get(u) || 0, bonus));
          };

          add(el.currentSrc || el.src || '', 3000);
          add(el.getAttribute('src') || '', 2000);
          const srcset = el.getAttribute('srcset') || '';
          for (const part of srcset.split(',').map(x => x.trim()).filter(Boolean)) {
            const bits = part.split(/\s+/);
            const u = bits[0];
            const d = bits[1] || '';
            let bonus = 6000;
            if (d.endsWith('w')) bonus += (parseInt(d, 10) || 0) * 20;
            if (d.endsWith('x')) bonus += Math.floor((parseFloat(d) || 1) * 15000);
            add(u, bonus);
          }

          for (const [src, bonus] of urls.entries()) {
            raw.push({
              elType: el.tagName.toLowerCase(), src, bonus, base, frameRatio,
              renderWidth: r.width, renderHeight: r.height
            });
          }
        }
      }

      raw.sort((a,b) => (b.base + b.bonus) - (a.base + a.bonus));
      const limited = raw.slice(0, 28);

      const measured = await Promise.all(limited.map(async c => {
        if (c.elType === 'video' || /\.(mp4|m4v|mov)(\?|$)/i.test(c.src)) {
          return {...c, type:'video', sourceWidth:0, sourceHeight:0, score:c.base+c.bonus+1000000};
        }
        const dims = await new Promise(resolve => {
          const img = new Image();
          const timer = setTimeout(() => resolve([0,0]), 3500);
          img.onload = () => { clearTimeout(timer); resolve([img.naturalWidth || 0, img.naturalHeight || 0]); };
          img.onerror = () => { clearTimeout(timer); resolve([0,0]); };
          img.src = c.src;
        });
        const sw = dims[0], sh = dims[1];
        const sourceRatio = sh ? sw / sh : 0;
        let ratioPenalty = 0;
        if (c.frameRatio > 0 && sourceRatio > 0) {
          ratioPenalty = Math.abs(Math.log(sourceRatio / c.frameRatio)) * 4500000;
        }
        let cropPenalty = 0;
        const low = c.src.toLowerCase();
        if (low.includes('c288.0.864.864a')) cropPenalty += 2500000;
        const m = low.match(/p(\d{3,4})x(\d{3,4})/);
        if (m && c.frameRatio > 0) {
          const ur = parseInt(m[1],10) / Math.max(1, parseInt(m[2],10));
          cropPenalty += Math.abs(Math.log(ur / c.frameRatio)) * 2500000;
        }
        const pixelBonus = sw * sh / 2;
        return {
          ...c, type:'image', sourceWidth:sw, sourceHeight:sh,
          sourceRatio, score:c.base+c.bonus+pixelBonus-ratioPenalty-cropPenalty
        };
      }));

      measured.sort((a,b) => b.score - a.score);
      return measured;
    }
    """

    try:
        items = page.evaluate(js) or []
    except Exception:
        items = []

    items = _dedupe_media(items, preserve_order=False)
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




def _find_main_ig_media_geometry(page) -> dict:
    """Find the active target-post media inside the visible post container.

    Restricted posts often open as a dialog over the account grid.  Searching
    document-wide can select a background profile tile or recommendation image.
    This resolver therefore prefers the topmost visible role=dialog, then a
    visible article, and never ranks media outside that target-post container.
    """
    try:
        return page.evaluate(
            r"""
            () => {
              const vw = innerWidth || 1600;
              const vh = innerHeight || 1000;

              const visible = el => {
                const r = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                return (
                  st.display !== 'none' &&
                  st.visibility !== 'hidden' &&
                  parseFloat(st.opacity || '1') > 0 &&
                  r.width > 40 &&
                  r.height > 40 &&
                  r.right > 0 &&
                  r.bottom > 0 &&
                  r.left < vw &&
                  r.top < vh
                );
              };

              const dialogs = Array.from(
                document.querySelectorAll('div[role="dialog"]')
              ).filter(visible);

              const articles = Array.from(
                document.querySelectorAll('article')
              ).filter(visible);

              let root = null;
              let rootKind = '';

              if (dialogs.length) {
                dialogs.sort((a, b) => {
                  const za = parseInt(getComputedStyle(a).zIndex || '0', 10) || 0;
                  const zb = parseInt(getComputedStyle(b).zIndex || '0', 10) || 0;
                  const ra = a.getBoundingClientRect();
                  const rb = b.getBoundingClientRect();
                  return (zb - za) || (rb.width * rb.height - ra.width * ra.height);
                });
                root = dialogs[0];
                rootKind = 'dialog';
              } else if (articles.length) {
                articles.sort((a, b) => {
                  const ra = a.getBoundingClientRect();
                  const rb = b.getBoundingClientRect();
                  const aa = Math.max(0, Math.min(ra.right, vw) - Math.max(ra.left, 0)) *
                             Math.max(0, Math.min(ra.bottom, vh) - Math.max(ra.top, 0));
                  const ab = Math.max(0, Math.min(rb.right, vw) - Math.max(rb.left, 0)) *
                             Math.max(0, Math.min(rb.bottom, vh) - Math.max(rb.top, 0));
                  return ab - aa;
                });
                root = articles[0];
                rootKind = 'article';
              } else {
                root = document;
                rootKind = 'document';
              }

              const rr = root === document
                ? {left:0, top:0, right:vw, bottom:vh, width:vw, height:vh}
                : root.getBoundingClientRect();

              const nodes = Array.from(root.querySelectorAll('img,video'))
                .map(el => {
                  const r = el.getBoundingClientRect();
                  const st = getComputedStyle(el);
                  const src = (
                    el.currentSrc || el.src ||
                    el.getAttribute('src') || ''
                  ).toLowerCase();

                  const overlapX = Math.max(
                    0, Math.min(r.right, vw, rr.right) -
                    Math.max(r.left, 0, rr.left)
                  );
                  const overlapY = Math.max(
                    0, Math.min(r.bottom, vh, rr.bottom) -
                    Math.max(r.top, 0, rr.top)
                  );
                  const visibleArea = overlapX * overlapY;
                  const cx = r.left + r.width / 2;
                  const cy = r.top + r.height / 2;
                  const ratio = r.height > 0 ? r.width / r.height : 0;

                  const bad =
                    src.includes('profile_pic') ||
                    src.includes('s150x150') ||
                    src.includes('s100x100') ||
                    src.includes('s64x64') ||
                    src.includes('emoji') ||
                    src.includes('sprite') ||
                    src.includes('static.cdninstagram.com') ||
                    r.width < 220 ||
                    r.height < 220 ||
                    visibleArea < 45000 ||
                    ratio < 0.25 ||
                    ratio > 3.2 ||
                    st.display === 'none' ||
                    st.visibility === 'hidden' ||
                    parseFloat(st.opacity || '1') <= 0;

                  const realMedia =
                    el.tagName.toLowerCase() === 'video' ||
                    src.includes('cdninstagram.com') ||
                    src.includes('fbcdn.net');

                  // Prefer media pane on the left/center of the active post root.
                  const targetX = rr.left + rr.width * 0.38;
                  const targetY = rr.top + rr.height * 0.50;
                  const centerPenalty =
                    Math.abs(cx - targetX) * 900 +
                    Math.abs(cy - targetY) * 650;

                  const score =
                    visibleArea * 18 +
                    (realMedia ? 2200000 : 0) -
                    centerPenalty;

                  return {
                    left:r.left, top:r.top, right:r.right, bottom:r.bottom,
                    width:r.width, height:r.height,
                    x:cx, y:cy, visibleArea, score, bad, src,
                    rootKind,
                    rootLeft:rr.left, rootTop:rr.top,
                    rootRight:rr.right, rootBottom:rr.bottom,
                    rootWidth:rr.width, rootHeight:rr.height
                  };
                })
                .filter(x => !x.bad)
                .sort((a,b) => b.score - a.score);

              return nodes[0] || null;
            }
            """
        ) or {}
    except Exception:
        return {}



def _hover_main_ig_media(page) -> bool:
    """Hover the actual post media to reveal carousel controls."""
    media = _find_main_ig_media_geometry(page)
    if not media:
        return False

    try:
        page.mouse.move(float(media["x"]), float(media["y"]))
        page.wait_for_timeout(500)
        return True
    except Exception as e:
        logger.debug(f"IG main-media hover skipped: {e}")
        return False



def _carousel_next_selectors() -> list[str]:
    """Return the shared localized selectors for carousel Next controls.

    Spatial validation is still performed later, so unrelated top-page
    「下一步」 buttons cannot pass solely because their label matches.
    """
    return [
        'button[aria-label="Next"]',
        'button[aria-label*="Next" i]',
        'button[aria-label="下一張"]',
        'button[aria-label*="下一" i]',
        'button[aria-label*="次へ" i]',
        'button:has(svg[aria-label="Next"])',
        'button:has(svg[aria-label*="Next" i])',
        'button:has(svg[aria-label*="下一" i])',
        'button:has(svg[aria-label*="次へ" i])',
        'div[role="button"][aria-label="Next"]',
        'div[role="button"][aria-label*="Next" i]',
        'div[role="button"][aria-label*="下一" i]',
        'div[role="button"][aria-label*="次へ" i]',
        'div[role="button"]:has(svg[aria-label="Next"])',
        'div[role="button"]:has(svg[aria-label*="Next" i])',
        'div[role="button"]:has(svg[aria-label*="下一" i])',
        'div[role="button"]:has(svg[aria-label*="次へ" i])',
        'svg[aria-label="Next"]',
        'svg[aria-label*="Next" i]',
        'svg[aria-label*="下一" i]',
        'svg[aria-label*="次へ" i]',
        '.coreSpriteRightChevron',
    ]



def _get_main_ig_media_rect(page) -> dict:
    """Return the geometry selected by the shared main-media finder."""
    return _find_main_ig_media_geometry(page)



def _is_box_near_main_media_next(box: dict, media: dict) -> bool:
    """Reject unrelated 下一步 buttons outside the media's right-edge zone."""
    if not box or not media:
        return False

    try:
        cx = float(box["x"]) + float(box["width"]) / 2.0
        cy = float(box["y"]) + float(box["height"]) / 2.0
        media_right = float(media["right"])
        media_left = float(media["left"])
        media_top = float(media["top"])
        media_height = float(media["height"])
        media_width = float(media["width"])
        media_mid_y = media_top + media_height / 2.0
    except Exception:
        return False

    horizontal_ok = (
        cx >= media_right - 125
        and cx <= media_right + 90
        and cx >= media_left + media_width * 0.58
    )
    vertical_ok = abs(cy - media_mid_y) <= max(115, media_height * 0.34)
    size_ok = (
        float(box.get("width") or 0) >= 12
        and float(box.get("height") or 0) >= 12
        and float(box.get("width") or 0) <= 130
        and float(box.get("height") or 0) <= 130
    )
    return bool(horizontal_ok and vertical_ok and size_ok)



def _click_next_ig(page):
    """Advance the active target-post carousel without scrolling the page.

    The post can be displayed as a dialog above an account grid.  Playwright's
    locator.click() may scroll an off-screen match into view and expose another
    post.  This implementation accepts only controls already visible inside the
    active dialog/article and clicks their current viewport coordinates.
    """
    _hover_main_ig_media(page)
    media = _get_main_ig_media_rect(page)

    if not media:
        logger.warning("IG target-post media rectangle not found before Next click")
        return False

    try:
        viewport_w = float(page.evaluate("() => innerWidth"))
        viewport_h = float(page.evaluate("() => innerHeight"))
    except Exception:
        viewport_w, viewport_h = 1400.0, 1600.0

    candidates = []
    seen_keys = set()

    for sel in _carousel_next_selectors():
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 20)):
                node = loc.nth(i)
                try:
                    if not node.is_visible(timeout=250):
                        continue

                    clickable = node
                    try:
                        ancestor = node.locator(
                            "xpath=ancestor-or-self::*[self::button or @role='button'][1]"
                        )
                        if ancestor.count() > 0 and ancestor.first.is_visible(timeout=200):
                            clickable = ancestor.first
                    except Exception:
                        pass

                    box = clickable.bounding_box()
                    if not box:
                        continue

                    # Never auto-scroll an element into view.  It must already be
                    # inside the current target-post viewport.
                    cx = float(box["x"]) + float(box["width"]) / 2.0
                    cy = float(box["y"]) + float(box["height"]) / 2.0
                    if not (2 <= cx <= viewport_w - 2 and 2 <= cy <= viewport_h - 2):
                        continue

                    if not _is_box_near_main_media_next(box, media):
                        continue

                    label = ""
                    try:
                        label = " ".join(filter(None, [
                            clickable.get_attribute("aria-label") or "",
                            clickable.get_attribute("title") or "",
                            clickable.get_attribute("class") or "",
                        ])).lower()
                    except Exception:
                        label = ""

                    if any(x in label for x in [
                        "previous", "上一張", "上一則", "上一步",
                        "往回", "返回", "back", "leftchevron",
                    ]):
                        continue

                    disabled = False
                    try:
                        disabled = bool(clickable.is_disabled())
                    except Exception:
                        pass
                    try:
                        disabled = disabled or (
                            (clickable.get_attribute("aria-disabled") or "").lower() == "true"
                        )
                    except Exception:
                        pass
                    if disabled:
                        continue

                    key = (
                        round(float(box["x"]), 1),
                        round(float(box["y"]), 1),
                        round(float(box["width"]), 1),
                        round(float(box["height"]), 1),
                    )
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)

                    target_x = float(media["right"]) - 28.0
                    target_y = float(media["top"]) + float(media["height"]) / 2.0
                    distance = abs(cx - target_x) * 5.0 + abs(cy - target_y)
                    candidates.append((distance, sel, box))
                except Exception:
                    continue
        except Exception:
            continue

    candidates.sort(key=lambda x: x[0])

    for _distance, sel, box in candidates:
        try:
            x = float(box["x"]) + float(box["width"]) / 2.0
            y = float(box["y"]) + float(box["height"]) / 2.0
            page.mouse.move(x, y)
            page.wait_for_timeout(120)
            page.mouse.click(x, y, delay=90)
            page.wait_for_timeout(850)
            logger.info(
                f"IG target-dialog Next mouse click: selector={sel}, "
                f"x={int(x)}, y={int(y)}"
            )
            return True
        except Exception:
            continue

    # The arrow can be visually painted without a stable DOM label.  Click the
    # visible media's right edge only when it is inside the viewport and target root.
    try:
        x = min(float(media["right"]) - 24.0, viewport_w - 3.0)
        y = float(media["top"]) + float(media["height"]) / 2.0
        root_top = float(media.get("rootTop") or 0.0)
        root_bottom = float(media.get("rootBottom") or viewport_h)

        if (
            x > float(media["left"]) + float(media["width"]) * 0.60
            and 2 <= x <= viewport_w - 2
            and max(2.0, root_top) <= y <= min(viewport_h - 2.0, root_bottom)
        ):
            page.mouse.move(x, y)
            page.wait_for_timeout(150)
            page.mouse.click(x, y, delay=100)
            page.wait_for_timeout(850)
            logger.info(
                f"IG target-dialog media-edge Next click: x={int(x)}, y={int(y)}, "
                f"root={media.get('rootKind') or 'unknown'}"
            )
            return True
    except Exception:
        pass

    logger.info(
        f"IG target-dialog Next control not currently visible; "
        f"skip scrolling and use keyboard fallback"
    )
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


def _detect_media_format_from_bytes(body: bytes, media_url: str = "", content_type: str = "") -> str:
    """Detect the real media format from bytes/content-type/URL.

    Returns: jpg, png, webp, mp4, or unknown.
    This prevents WEBP bytes from being saved as a fake .jpg.
    """
    head = body[:32] if body else b""
    ct = (content_type or "").lower()
    low = (media_url or "").lower()

    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"RIFF") and b"WEBP" in head[:16]:
        return "webp"
    if b"ftyp" in head[:16]:
        return "mp4"

    if "image/jpeg" in ct or "image/jpg" in ct:
        return "jpg"
    if "image/png" in ct:
        return "png"
    if "image/webp" in ct:
        return "webp"
    if ct.startswith("video/"):
        return "mp4"

    if ".mp4" in low or ".m4v" in low or ".mov" in low:
        return "mp4"
    if ".webp" in low or "format=webp" in low:
        return "webp"
    if ".png" in low or "format=png" in low:
        return "png"
    if ".jpg" in low or ".jpeg" in low or "format=jpg" in low or "format=jpeg" in low:
        return "jpg"

    return "unknown"


def _replace_file_ext(path: str, ext: str) -> str:
    if not ext.startswith("."):
        ext = "." + ext
    base, _ = os.path.splitext(path)
    return base + ext


def _convert_webp_bytes_to_jpeg(body: bytes):
    """Return real JPEG bytes for WEBP input, or None if Pillow is unavailable."""
    if Image is None:
        return None
    try:
        from io import BytesIO
        with Image.open(BytesIO(body)) as img:
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            else:
                img = img.convert("RGB")
            out = BytesIO()
            img.save(out, format="JPEG", quality=95, optimize=True)
            return out.getvalue()
    except Exception as e:
        logger.warning(f"IG WEBP 轉 JPEG 失敗，保留 .webp: {e}")
        return None



def _is_complete_mp4_body(body: bytes) -> bool:
    """Return True only for a self-contained MP4 file.

    Instagram video playback may be delivered as fragmented MP4 range responses.
    A media fragment beginning with `moof`/`mdat` but lacking the initialization
    boxes `ftyp` and `moov` cannot be opened as a standalone .mp4 file.
    """
    if not body or len(body) < _MIN_FILE_SIZE:
        return False

    probe_head = body[:1024 * 1024]
    probe_tail = body[-1024 * 1024:] if len(body) > 1024 * 1024 else b""

    has_ftyp = b"ftyp" in body[:128]
    has_moov = b"moov" in probe_head or b"moov" in probe_tail
    return bool(has_ftyp and has_moov)




def _is_playable_mp4_body(body: bytes) -> bool:
    """Return True only when MP4 initialization and media payload are present."""
    if not _is_complete_mp4_body(body):
        return False

    probe = body[:2 * 1024 * 1024]
    if len(body) > 2 * 1024 * 1024:
        probe += body[-2 * 1024 * 1024:]

    return b"mdat" in probe or b"moof" in probe


def _build_complete_mp4_from_harvested_candidates(candidates) -> bytes:
    """Assemble one playable MP4 from browser-captured init/media fragments."""
    parts = []
    seen_hashes = set()

    for candidate in candidates or []:
        candidate_parts = list(candidate.get("parts") or [])

        if candidate.get("body"):
            candidate_parts.append({
                "body": candidate.get("body"),
                "content_range": candidate.get("content_range", ""),
                "captured_at": candidate.get("captured_at", 0),
            })

        for part in candidate_parts:
            body = part.get("body") or b""
            if not body:
                continue

            digest = hashlib.sha256(body).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)

            content_range = part.get("content_range", "") or ""
            range_start = None
            match = re.search(
                r"bytes\s+(\d+)-(\d+)/(\d+|\*)",
                content_range,
                flags=re.I,
            )
            if match:
                range_start = int(match.group(1))

            parts.append({
                "body": body,
                "range_start": range_start,
                "captured_at": float(part.get("captured_at") or 0),
            })

    for part in parts:
        if _is_playable_mp4_body(part["body"]):
            return part["body"]

    init_parts = [
        part for part in parts
        if b"ftyp" in part["body"][:128]
        and (
            b"moov" in part["body"][:1024 * 1024]
            or b"moov" in part["body"][-1024 * 1024:]
        )
    ]
    media_parts = [
        part for part in parts
        if b"moof" in part["body"][:1024 * 1024]
        or b"mdat" in part["body"][:1024 * 1024]
    ]

    if not init_parts or not media_parts:
        return b""

    media_parts.sort(
        key=lambda part: (
            part["range_start"] is None,
            part["range_start"] if part["range_start"] is not None else 0,
            part["captured_at"],
        )
    )

    for init in sorted(init_parts, key=lambda part: part["captured_at"]):
        rebuilt = bytearray(init["body"])
        init_hash = hashlib.sha256(init["body"]).hexdigest()

        for media in media_parts:
            if hashlib.sha256(media["body"]).hexdigest() == init_hash:
                continue
            rebuilt.extend(media["body"])

        body = bytes(rebuilt)
        if _is_playable_mp4_body(body):
            return body

    return b""



def _write_media_body(dst: str, body: bytes, media_url: str = "", content_type: str = "") -> int:
    if not _is_probably_valid_media_body(body, media_url=media_url, content_type=content_type):
        raise Exception(f"媒體內容無效或過小: {len(body) if body else 0} bytes")

    real_fmt = _detect_media_format_from_bytes(body, media_url=media_url, content_type=content_type)

    if real_fmt == "mp4" and not _is_playable_mp4_body(body):
        raise Exception(
            "影片資料不是可獨立播放的完整 MP4（需同時包含 ftyp/moov 與 moof/mdat）"
        )

    out_body = body
    out_path = dst

    if real_fmt == "webp":
        jpeg_body = _convert_webp_bytes_to_jpeg(body)
        if jpeg_body:
            out_body = jpeg_body
            out_path = _replace_file_ext(dst, ".jpg")
            logger.info(f"IG WEBP 已轉換為真正 JPEG: {os.path.basename(out_path)}")
        else:
            out_path = _replace_file_ext(dst, ".webp")
            logger.info(f"IG Pillow 不可用，WEBP 保留真實副檔名: {os.path.basename(out_path)}")
    elif real_fmt == "jpg":
        out_path = _replace_file_ext(dst, ".jpg")
    elif real_fmt == "png":
        out_path = _replace_file_ext(dst, ".png")
    elif real_fmt == "mp4":
        out_path = _replace_file_ext(dst, ".mp4")

    if out_path != dst and os.path.exists(dst):
        try:
            os.remove(dst)
        except Exception:
            pass

    with open(out_path, "wb") as f:
        f.write(out_body)

    return len(out_body)


def _real_ext_for_file(path: str) -> str:
    try:
        with open(path, "rb") as f:
            body = f.read(64)
    except Exception:
        body = b""
    fmt = _detect_media_format_from_bytes(body, media_url=path)
    if fmt == "jpg":
        return ".jpg"
    if fmt == "png":
        return ".png"
    if fmt == "webp":
        return ".webp"
    if fmt == "mp4":
        return ".mp4"
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in _MEDIA_EXTS else ".jpg"



def _classify_mp4_fragment_body(body: bytes) -> str:
    """Classify small/large MP4 response bodies.

    Instagram's MP4 initialization segment can be much smaller than the normal
    5 KiB media threshold.  Rejecting all bodies below _MIN_FILE_SIZE discards
    the exact ftyp/moov segment required to assemble a playable fragmented MP4.
    """
    if not body or len(body) < 16:
        return ""

    head = body[:1024 * 1024]
    tail = body[-1024 * 1024:] if len(body) > 1024 * 1024 else b""

    has_ftyp = b"ftyp" in body[:128]
    has_moov = b"moov" in head or b"moov" in tail
    has_moof = b"moof" in head
    has_mdat = b"mdat" in head or b"mdat" in tail

    if has_ftyp and has_moov and (has_moof or has_mdat):
        return "complete"
    if has_ftyp or has_moov:
        return "init"
    if has_moof or has_mdat:
        return "media"
    return ""



def _capture_playwright_response(response, harvested: dict):
    """Harvest IG media and preserve all video init/media fragments."""
    try:
        media_url = response.url or ""
        if response.status < 200 or response.status >= 300:
            return

        headers = response.headers or {}
        content_type = (
            headers.get("content-type", "")
            or headers.get("Content-Type", "")
            or ""
        )
        content_range = (
            headers.get("content-range", "")
            or headers.get("Content-Range", "")
            or ""
        )
        low_ct = content_type.lower()
        low_url = media_url.lower()
        is_instagram_cdn = (
            "cdninstagram.com" in low_url
            or "fbcdn.net" in low_url
            or "instagram.f" in low_url
        )
        is_video_response = (
            low_ct.startswith("video/")
            or "octet-stream" in low_ct
            or any(x in low_url for x in [".mp4", ".m4v", ".mov"])
        )

        if not is_video_response and not _looks_like_real_ig_media_url(media_url):
            return
        if is_video_response and not is_instagram_cdn:
            return

        if low_ct and not (
            low_ct.startswith("image/")
            or low_ct.startswith("video/")
            or "octet-stream" in low_ct
        ):
            return

        body = response.body()
        fragment_kind = (
            _classify_mp4_fragment_body(body)
            if is_video_response
            else ""
        )

        if is_video_response:
            # MP4 init segments are commonly only a few hundred bytes to a few
            # KiB. Keep them when they contain ftyp/moov even though they are
            # smaller than the normal media-file threshold.
            if not fragment_kind and not _is_probably_valid_media_body(
                body,
                media_url=media_url,
                content_type=content_type,
            ):
                return
        else:
            if not _is_probably_valid_media_body(
                body,
                media_url=media_url,
                content_type=content_type,
            ):
                return

        is_video = is_video_response
        score = _media_quality_score(media_url) + len(body)
        captured_at = time.time()

        if not is_video:
            key = _media_key_from_url(media_url)
            old = harvested.get(key)
            if old and old.get("score", 0) >= score:
                return

            harvested[key] = {
                "src": html.unescape(unquote(media_url)),
                "type": "image",
                "body": body,
                "content_type": content_type,
                "content_range": content_range,
                "captured_at": captured_at,
                "score": score,
                "from": "network",
            }
            return

        key = "video::" + _normalized_media_identity(media_url)
        record = harvested.get(key)

        if not record:
            record = {
                "src": html.unescape(unquote(media_url)),
                "type": "video",
                "body": body,
                "content_type": content_type,
                "content_range": content_range,
                "captured_at": captured_at,
                "score": score,
                "from": "network",
                "parts": [],
                "_part_hashes": set(),
                "fragment_kinds": set(),
            }
            harvested[key] = record

        digest = hashlib.sha256(body).hexdigest()
        part_hashes = record.setdefault("_part_hashes", set())

        if digest not in part_hashes:
            part_hashes.add(digest)
            record.setdefault("parts", []).append({
                "body": body,
                "content_type": content_type,
                "content_range": content_range,
                "captured_at": captured_at,
                "src": html.unescape(unquote(media_url)),
                "fragment_kind": fragment_kind,
            })
            if fragment_kind:
                record.setdefault("fragment_kinds", set()).add(fragment_kind)

        if score > int(record.get("score") or 0):
            record["body"] = body
            record["content_type"] = content_type
            record["content_range"] = content_range
            record["captured_at"] = captured_at
            record["score"] = score
            record["src"] = html.unescape(unquote(media_url))

    except Exception:
        return



def _download_complete_mp4_with_ranges(context, url: str, dst: str, referer: str):
    """Download and rebuild a complete MP4 from explicit byte ranges."""
    headers_base = {
        "Referer": referer or "https://www.instagram.com/",
        "Accept": "video/mp4,video/*;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }

    chunk_size = 2 * 1024 * 1024
    assembled = bytearray()
    total_size = None
    offset = 0
    max_total = 1024 * 1024 * 1024

    for _ in range(1024):
        end = offset + chunk_size - 1
        headers = dict(headers_base)
        headers["Range"] = f"bytes={offset}-{end}"

        response = context.request.get(
            url,
            headers=headers,
            timeout=90000,
        )

        if response.status not in {200, 206}:
            raise Exception(
                f"IG MP4 range request failed: HTTP {response.status}, "
                f"range={offset}-{end}"
            )

        body = response.body()
        if not body:
            raise Exception(
                f"IG MP4 range request returned empty body: range={offset}-{end}"
            )

        content_range = ""
        try:
            content_range = (
                response.headers.get("content-range", "")
                or response.headers.get("Content-Range", "")
                or ""
            )
        except Exception:
            content_range = ""

        if content_range:
            m = re.search(
                r"bytes\s+(\d+)-(\d+)/(\d+|\*)",
                content_range,
                flags=re.I,
            )
            if m:
                start_byte = int(m.group(1))
                end_byte = int(m.group(2))

                if start_byte != offset:
                    raise Exception(
                        f"IG MP4 unexpected Content-Range start: "
                        f"expected={offset}, actual={start_byte}"
                    )

                if m.group(3) != "*":
                    total_size = int(m.group(3))
                    if total_size <= 0 or total_size > max_total:
                        raise Exception(
                            f"IG MP4 invalid total size: {total_size}"
                        )

                expected_len = end_byte - start_byte + 1
                if len(body) > expected_len:
                    body = body[:expected_len]

        if response.status == 200 and offset == 0:
            assembled.extend(body)
            break

        assembled.extend(body)
        offset += len(body)

        if total_size is not None and offset >= total_size:
            assembled = assembled[:total_size]
            break

        if len(body) < chunk_size and total_size is None:
            break

        if len(assembled) > max_total:
            raise Exception("IG MP4 exceeded 1 GiB safety cap")

    rebuilt = bytes(assembled)

    if not _is_playable_mp4_body(rebuilt):
        raise Exception(
            "IG MP4 range rebuild incomplete: missing ftyp/moov initialization boxes"
        )

    return _write_media_body(
        dst,
        rebuilt,
        media_url=url,
        content_type="video/mp4",
    )



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

    root = str(IG_PARSER_PROFILE_DIR or os.path.join(DATA_DIR, "chrome_ig_parser"))
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

    This is the preferred login workflow. The profile keeps Instagram login, 2FA,
    trust-device and age/audience confirmation state so normal downloads do not
    require manually exported cookies.txt. cookies.txt remains only as a legacy
    emergency fallback for Instaloader / yt-dlp compatibility.
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



def _try_confirm_ig_restricted_content(page) -> bool:
    """Click only explicit Instagram age/audience confirmation controls.

    Login alone is not always enough: Instagram can keep a per-post consent/
    age-confirmation overlay in the dedicated persistent profile.  This helper
    never clicks generic page buttons; it only accepts explicit localized text.
    """
    selectors = [
        'button:has-text("查看貼文")',
        'button:has-text("查看內容")',
        'button:has-text("仍要查看")',
        'button:has-text("繼續查看")',
        'button:has-text("確認觀看")',
        'button:has-text("我已年滿 18 歲")',
        'button:has-text("我已年滿18歲")',
        'button:has-text("Continue")',
        'button:has-text("View Post")',
        'button:has-text("View Content")',
        'button:has-text("See Post")',
        'button:has-text("I am 18 or older")',
        'div[role="button"]:has-text("查看貼文")',
        'div[role="button"]:has-text("查看內容")',
        'div[role="button"]:has-text("仍要查看")',
        'div[role="button"]:has-text("繼續查看")',
        'div[role="button"]:has-text("確認觀看")',
        'div[role="button"]:has-text("Continue")',
        'div[role="button"]:has-text("View Post")',
        'div[role="button"]:has-text("View Content")',
    ]

    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 8)):
                node = loc.nth(i)
                try:
                    if not node.is_visible(timeout=250):
                        continue
                    text = re.sub(r"\s+", " ", node.inner_text(timeout=500) or "").strip()
                    if not text:
                        continue
                    node.click(timeout=1800, force=True)
                    page.wait_for_timeout(1400)
                    logger.info(f"IG restricted-content confirmation clicked: {text}")
                    return True
                except Exception:
                    continue
        except Exception:
            continue

    return False



def _manual_wait_persistent_profile(page, reason: str = ""):
    """Bring the visible Chrome fallback window forward and wait for user action."""
    wait_sec = _get_persistent_manual_wait_seconds()

    try:
        page.bring_to_front()
    except Exception:
        pass

    auto_confirmed = _try_confirm_ig_restricted_content(page)
    if auto_confirmed:
        _warmup_ig_page_for_media(page, is_reel=_is_ig_reel_url(page.url or ""))
        return

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

    _try_confirm_ig_restricted_content(page)
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


def _get_fresh_persistent_target_page(context):
    """Create a brand-new tab for each IG task and close restored/stale tabs.

    The dedicated IG_Parser profile is persistent, so Chrome can restore the
    previous post's SPA/dialog DOM.  Reusing that tab can make the next task's
    first carousel item come from the previous or recommended post even when
    the URL already shows the requested shortcode.  A fresh tab keeps the
    existing login/trust cookies but discards old DOM, old dialog state, and old
    page-local performance entries.
    """
    try:
        old_pages = list(context.pages)
    except Exception:
        old_pages = []

    page = context.new_page()

    try:
        page.bring_to_front()
    except Exception:
        pass

    for old in old_pages:
        try:
            if old != page:
                old.close()
        except Exception:
            pass

    return page



_IG_PROFILE_RESERVED_PATHS = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
    "developer", "about", "legal", "terms", "privacy", "directory", "web",
    "graphql", "api", "challenge", "oauth", "emails", "settings",
}


def is_instagram_profile_url(url: str) -> str:
    """Return username when *url* is an Instagram profile/profile-tab URL.

    Supported inputs:
    - https://www.instagram.com/<username>/
    - https://www.instagram.com/<username>/reels/
    - https://www.instagram.com/<username>/tagged/

    Single content URLs such as /p/<shortcode>/, /reel/<shortcode>/ and
    /stories/<username>/... are intentionally excluded so the existing stable
    single-post / Reel downloader remains untouched.
    """
    raw = html.unescape(unquote(str(url or "").strip()))
    if not raw:
        return ""

    try:
        parsed = urlparse(raw)
    except Exception:
        return ""

    host = (parsed.netloc or "").lower()
    if host not in {"instagram.com", "www.instagram.com"}:
        return ""

    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return ""

    username = parts[0].strip()
    if not username:
        return ""

    # Top-level IG reserved routes are not usernames.  This also protects
    # /reel/<shortcode>/ and /reels/<shortcode>/ from being treated as profiles.
    if username.lower() in _IG_PROFILE_RESERVED_PATHS:
        return ""

    # Allow only the homepage itself or known profile sub-tabs.  Unknown deeper
    # paths should stay in the normal downloader/error path instead of silently
    # becoming a whole-account scan.
    if len(parts) >= 2:
        tab = (parts[1] or "").lower()
        if tab not in {"reels", "tagged"}:
            return ""
        if len(parts) > 2:
            return ""

    # Instagram usernames can contain letters, numbers, underscores and dots.
    # Be permissive enough for real accounts but strict enough to avoid paths.
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username):
        return ""

    return username

def _normalize_ig_profile_url(profile_url: str) -> str:
    username = is_instagram_profile_url(profile_url)
    if username:
        return f"https://www.instagram.com/{username}/"
    return profile_url


def _profile_scan_entry_urls(profile_url: str, username: str) -> list[str]:
    """Return profile tabs that should be scanned for post/reel links.

    Instagram can separate the normal grid and the Reels tab.  If the user pastes
    /<username>/reels/ we still scan the homepage plus the Reels tab so the
    feature means "download this account's visible posts and reels", not just the
    preview thumbnails currently visible on one tab.
    """
    base = f"https://www.instagram.com/{username}/"
    reels = f"https://www.instagram.com/{username}/reels/"

    out: list[str] = []
    raw = html.unescape(unquote(str(profile_url or "").strip()))
    try:
        parsed = urlparse(raw)
        parts = [p for p in (parsed.path or "").split("/") if p]
        if len(parts) == 2 and parts[1].lower() in {"reels", "tagged"}:
            tab_url = f"https://www.instagram.com/{username}/{parts[1].lower()}/"
            out.append(tab_url)
    except Exception:
        pass

    # Always scan both base and Reels tab.  Dedupe keeps repeated links harmless,
    # and this prevents /username/reels/ from being treated as a single unsupported
    # Reel URL while also covering accounts that split posts and reels across tabs.
    for u in [base, reels]:
        if u not in out:
            out.append(u)
    return out



def _normalize_profile_child_url(raw_url: str) -> str:
    u = html.unescape(unquote(str(raw_url or "").strip()))
    if not u:
        return ""
    m = re.search(r"(?:https?://(?:www\.)?instagram\.com)?/(p|reel|reels)/([^/?#&\"'<>\s]+)", u, flags=re.I)
    if not m:
        return ""
    kind = (m.group(1) or "").lower()
    shortcode = (m.group(2) or "").strip()
    if not shortcode:
        return ""
    if kind == "p":
        return f"https://www.instagram.com/p/{shortcode}/"
    return f"https://www.instagram.com/reel/{shortcode}/"


def _dedupe_profile_child_urls(urls: list[str]) -> list[str]:
    out = []
    seen = set()
    for raw in urls or []:
        u = _normalize_profile_child_url(raw)
        if not u:
            continue
        shortcode = _extract_shortcode(u)
        key = f"reel:{shortcode}" if _is_ig_reel_url(u) else f"p:{shortcode}"
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    return out



def _profile_media_kind_from_node(node: dict) -> str:
    """Resolve whether a structured profile media node should use /p/ or /reel/."""
    product_type = str(
        node.get("product_type")
        or node.get("media_product_type")
        or node.get("__typename")
        or ""
    ).lower()

    if any(token in product_type for token in ["clip", "reel"]):
        return "reel"

    # Instagram private API: media_type=2 is video, but not every video post is a
    # Reel. Only classify it as Reel when product_type explicitly says clips.
    return "p"


def _profile_node_owner(node: dict) -> str:
    """Read the owner username from common GraphQL/private-API node layouts."""
    candidates = []

    for key in ["owner", "user", "creator", "author"]:
        value = node.get(key)
        if isinstance(value, dict):
            candidates.extend([
                value.get("username"),
                value.get("user_name"),
                value.get("handle"),
            ])

    candidates.extend([
        node.get("username"),
        node.get("owner_username"),
        node.get("user_name"),
    ])

    for value in candidates:
        clean = _clean_ig_account(value)
        if clean:
            return clean

    return ""


def _extract_profile_urls_from_structured_payload(
    payload,
    username: str,
) -> list[str]:
    """Extract exact-owner post/reel URLs from IG GraphQL/API JSON.

    Recommendation and adjacent-account nodes are ignored because every accepted
    media node must carry owner/user.username equal to the requested profile.
    """
    target = _clean_ig_account(username).lower()
    if not target:
        return []

    out: list[str] = []
    seen = set()
    stack = [payload]

    while stack:
        value = stack.pop()

        if isinstance(value, list):
            stack.extend(reversed(value))
            continue

        if not isinstance(value, dict):
            continue

        shortcode = str(
            value.get("shortcode")
            or value.get("code")
            or value.get("media_code")
            or ""
        ).strip()

        if shortcode and re.fullmatch(r"[A-Za-z0-9_-]{5,40}", shortcode):
            owner = _profile_node_owner(value).lower()
            if owner == target:
                kind = _profile_media_kind_from_node(value)
                normalized = (
                    f"https://www.instagram.com/reel/{shortcode}/"
                    if kind == "reel"
                    else f"https://www.instagram.com/p/{shortcode}/"
                )
                key = shortcode
                if key not in seen:
                    seen.add(key)
                    out.append(normalized)

        for child in value.values():
            if isinstance(child, (dict, list)):
                stack.append(child)

    return _dedupe_profile_child_urls(out)


def _attach_profile_structured_response_harvester(
    page,
    username: str,
    harvested_urls: list[str],
    harvested_seen: set[str],
):
    """Attach a background response listener for profile GraphQL/API media URLs."""
    target = _clean_ig_account(username).lower()

    def on_response(response):
        try:
            response_url = str(response.url or "")
            low = response_url.lower()

            if not any(marker in low for marker in [
                "graphql/query",
                "/api/v1/feed/user/",
                "/api/v1/users/",
                "/api/v1/clips/",
                "web_profile_info",
            ]):
                return

            content_type = ""
            try:
                content_type = (
                    response.headers.get("content-type", "")
                    or response.headers.get("Content-Type", "")
                    or ""
                ).lower()
            except Exception:
                content_type = ""

            if content_type and "json" not in content_type:
                return

            payload = response.json()
            urls = _extract_profile_urls_from_structured_payload(payload, target)

            added = 0
            for url in urls:
                shortcode = _extract_shortcode(url) or ""
                if not shortcode or shortcode in harvested_seen:
                    continue
                harvested_seen.add(shortcode)
                harvested_urls.append(url)
                added += 1

            if added:
                logger.info(
                    f"IG 主頁背景 structured URL harvest: "
                    f"@{username}, new={added}, total={len(harvested_urls)}"
                )
        except Exception:
            return

    page.on("response", on_response)
    return on_response


def _extract_profile_urls_from_embedded_json(page, username: str) -> list[str]:
    """Parse JSON script payloads already embedded in the profile document.

    This is owner-gated and does not regex the entire HTML for arbitrary links.
    """
    try:
        payloads = page.evaluate(
            """
            () => {
              const out = [];
              const nodes = Array.from(document.querySelectorAll(
                'script[type="application/json"], script[data-sjs], script[id*="__data"]'
              ));
              for (const node of nodes) {
                const text = (node.textContent || '').trim();
                if (text && text.length >= 20 && text.length <= 12000000) {
                  out.push(text);
                }
              }
              return out.slice(0, 80);
            }
            """
        ) or []
    except Exception:
        payloads = []

    out: list[str] = []
    for raw in payloads:
        try:
            payload = __import__("json").loads(raw)
        except Exception:
            continue
        out.extend(_extract_profile_urls_from_structured_payload(payload, username))

    return _dedupe_profile_child_urls(out)

def _extract_profile_post_urls_from_page(page) -> list[str]:
    """Collect only profile-grid /p/ and /reel/ anchors under <main>.

    v11.95 removes the over-strict nested-thumbnail visibility requirement from
    v11.94. Instagram often keeps the anchor valid while the lazy thumbnail has
    zero dimensions. Broad document HTML, performance resources and click-probe
    discovery remain forbidden because they can include recommendation posts.
    """
    js = r"""
    () => {
      const main = document.querySelector('main');
      if (!main) return [];

      const out = [];
      const seen = new Set();

      function excluded(a) {
        if (a.closest(
          'div[role="dialog"], nav, footer, aside, ' +
          '[role="navigation"], [aria-modal="true"]'
        )) return true;

        let node = a;
        for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
          const text = (
            (node.getAttribute && node.getAttribute('aria-label') || '') + ' ' +
            (node.innerText || '') + ' ' +
            (node.textContent || '')
          ).toLowerCase();

          if (
            text.includes('suggested for you') ||
            text.includes('recommended') ||
            text.includes('為你推薦') ||
            text.includes('推薦帳號') ||
            text.includes('探索更多') ||
            text.includes('discover people')
          ) return true;
        }
        return false;
      }

      const anchors = Array.from(main.querySelectorAll(
        'a[href^="/p/"], a[href^="/reel/"], a[href^="/reels/"], ' +
        'a[href*="instagram.com/p/"], a[href*="instagram.com/reel/"]'
      ));

      for (const a of anchors) {
        if (excluded(a)) continue;

        const href = a.href || a.getAttribute('href') || '';
        const m = String(href).match(
          /(?:https?:\/\/(?:www\.)?instagram\.com)?\/(p|reel|reels)\/([^/?#&"'<>\\\s]+)\/?/i
        );
        if (!m) continue;

        const kind = String(m[1] || '').toLowerCase() === 'p' ? 'p' : 'reel';
        const shortcode = String(m[2] || '').trim();
        if (!shortcode) continue;

        const hasTileContent = !!a.querySelector('img, video, canvas, svg, div, span');
        const r = a.getBoundingClientRect();
        const gridSized = r.width >= 70 && r.height >= 70;
        if (!hasTileContent && !gridSized) continue;

        const key = kind + ':' + shortcode;
        if (seen.has(key)) continue;
        seen.add(key);

        out.push({
          url: 'https://www.instagram.com/' + kind + '/' + shortcode + '/',
          top: Number.isFinite(r.top) ? r.top : 0,
          left: Number.isFinite(r.left) ? r.left : 0
        });
      }

      out.sort((a, b) => {
        if (Math.abs(a.top - b.top) > 12) return a.top - b.top;
        return a.left - b.left;
      });

      return out.map(x => x.url);
    }
    """
    try:
        raw_values = page.evaluate(js) or []
    except Exception:
        raw_values = []

    return _dedupe_profile_child_urls(raw_values)


def _get_profile_declared_post_count(page, username: str = "") -> int:
    """Read the profile header's declared post count when available."""
    try:
        raw = page.evaluate(
            """
            () => {
              const main = document.querySelector('main') || document.body;
              const header = main.querySelector('header') || main;
              return (header.innerText || header.textContent || '').slice(0, 5000);
            }
            """
        ) or ""
    except Exception:
        raw = ""

    value_text = html.unescape(str(raw or ""))
    for pat in [
        r"([\d,.\s]+)\s*(?:posts?|貼文)",
        r"(?:posts?|貼文)\s*([\d,.\s]+)",
    ]:
        m = re.search(pat, value_text, flags=re.I)
        if not m:
            continue
        digits = re.sub(r"[^\d]", "", m.group(1) or "")
        if digits.isdigit():
            value = int(digits)
            if 0 <= value <= 1000000:
                return value
    return 0


def _get_profile_click_tile_records(page, limit: int = 18) -> list[dict]:
    js = r'''
    (limit) => {
      const root = document.querySelector('main') || document;
      const nodes = Array.from(root.querySelectorAll('a, div[role="button"], img, video'));
      const out = [];
      const seen = new Set();
      const W = window.innerWidth || 1600;
      const H = window.innerHeight || 1000;
      function badSrc(src) {
        const low = String(src || '').toLowerCase();
        if (!low) return true;
        if (low.includes('profile_pic') || low.includes('s150x150') || low.includes('s100x100')) return true;
        if (low.includes('s64x64') || low.includes('s50x50') || low.includes('s40x40') || low.includes('s32x32')) return true;
        if (low.includes('static.cdninstagram.com') || low.includes('/rsrc.php') || low.includes('emoji')) return true;
        return false;
      }
      for (const n of nodes) {
        let media = null;
        if (n.tagName && ['IMG','VIDEO'].includes(n.tagName.toUpperCase())) media = n;
        else media = n.querySelector && n.querySelector('img, video');
        if (!media) continue;
        const src = media.currentSrc || media.src || media.getAttribute('src') || '';
        if (badSrc(src)) continue;
        const mr = media.getBoundingClientRect();
        const r = n.getBoundingClientRect();
        const box = (mr.width * mr.height >= r.width * r.height) ? mr : r;
        const w = box.width || 0;
        const h = box.height || 0;
        if (w < 120 || h < 120) continue;
        const ratio = w / Math.max(h, 1);
        if (ratio < 0.65 || ratio > 1.45) continue;
        if (box.bottom < 180 || box.top > H + 300 || box.right < 0 || box.left > W) continue;
        const clicker = n.closest && (n.closest('a[href], div[role="button"], button') || n);
        if (!clicker) continue;
        const cr = clicker.getBoundingClientRect();
        const cx = Math.max(5, Math.min(W - 5, cr.left + cr.width / 2));
        const cy = Math.max(5, Math.min(H - 5, cr.top + cr.height / 2));
        const key = Math.round(cx / 8) + ':' + Math.round(cy / 8) + ':' + String(src).slice(0, 80);
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({x: cx, y: cy, top: box.top, left: box.left, area: w * h, src: src});
      }
      out.sort((a,b) => Math.abs(a.top-b.top) > 12 ? a.top-b.top : a.left-b.left);
      return out.slice(0, limit || 18);
    }
    '''
    try:
        return page.evaluate(js, int(limit or 18)) or []
    except Exception:
        return []


def _extract_profile_post_urls_by_click_probe(page, entry_url: str, already_seen: set[str] | None = None, max_clicks: int = 12) -> list[str]:
    already_seen = already_seen or set()
    found: list[str] = []
    records = _get_profile_click_tile_records(page, limit=max_clicks)
    if not records:
        return []
    for rec in records[:max_clicks]:
        try:
            page.mouse.click(float(rec.get('x') or 0), float(rec.get('y') or 0))
            page.wait_for_timeout(1200)
            u = _normalize_profile_child_url(page.url or '')
            if u and u not in already_seen and u not in found:
                found.append(u)
            for u2 in _extract_profile_post_urls_from_page(page):
                if u2 not in already_seen and u2 not in found:
                    found.append(u2)
            if _normalize_profile_child_url(page.url or ''):
                try:
                    page.go_back(wait_until='domcontentloaded', timeout=8000)
                    page.wait_for_timeout(900)
                except Exception:
                    try:
                        page.goto(entry_url, wait_until='domcontentloaded', timeout=12000)
                        page.wait_for_timeout(900)
                    except Exception:
                        pass
            else:
                try:
                    page.keyboard.press('Escape')
                    page.wait_for_timeout(500)
                except Exception:
                    pass
        except Exception:
            try:
                page.keyboard.press('Escape')
                page.wait_for_timeout(500)
            except Exception:
                pass
            continue
    return _dedupe_profile_child_urls(found)



def _extract_profile_post_urls_by_verified_click_probe(
    page,
    entry_url: str,
    username: str,
    already_seen: set[str] | None = None,
    max_clicks: int = 12,
) -> list[str]:
    """Discover profile tiles by clicking them and verifying the real post owner.

    Instagram currently renders some profile grids without usable /p/ or /reel/
    anchors.  In that layout the only reliable route is opening the visible tile.

    Safety rules:
    - only click grid-like media records returned from <main>;
    - accept only a normalized post/reel URL;
    - read the opened post account and require exact username equality;
    - never harvest links from page HTML, performance resources, or recommendations;
    - return to the same profile entry after every probe.
    """
    already_seen = already_seen or set()
    target_user = _clean_ig_account(username).lower()
    found: list[str] = []
    rejected = 0

    # Re-evaluate records after every navigation because React may rebuild the grid.
    for click_index in range(max(1, int(max_clicks or 1))):
        records = _get_profile_click_tile_records(page, limit=max_clicks + 8)
        if not records or click_index >= len(records):
            break

        rec = records[click_index]
        try:
            x = float(rec.get("x") or 0)
            y = float(rec.get("y") or 0)
            if x <= 0 or y <= 0:
                continue

            before_url = page.url or entry_url
            page.mouse.click(x, y)
            page.wait_for_timeout(1100)

            child_url = _normalize_profile_child_url(page.url or "")
            if not child_url:
                # Some layouts open a dialog while keeping the profile URL.
                try:
                    canonical = (
                        page.locator('link[rel="canonical"]').first.get_attribute("href")
                        or ""
                    )
                except Exception:
                    canonical = ""
                child_url = _normalize_profile_child_url(canonical)

            accepted = False
            if child_url and child_url not in already_seen and child_url not in found:
                owner = _clean_ig_account(_get_ig_post_account(page)).lower()
                if owner and owner == target_user:
                    found.append(child_url)
                    accepted = True
                    logger.info(
                        f"IG 主頁 verified tile accepted: @{username}, "
                        f"url={child_url}, owner={owner}"
                    )
                else:
                    rejected += 1
                    logger.warning(
                        f"IG 主頁 verified tile rejected: expected=@{username}, "
                        f"observed={owner or 'unknown'}, url={child_url or 'unknown'}"
                    )

            # Restore the exact profile tab and scroll position conservatively.
            restored = False
            if _normalize_profile_child_url(page.url or ""):
                try:
                    page.go_back(wait_until="domcontentloaded", timeout=12000)
                    page.wait_for_timeout(900)
                    restored = True
                except Exception:
                    restored = False
            else:
                try:
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(500)
                    restored = True
                except Exception:
                    restored = False

            if not restored or _normalize_profile_child_url(page.url or ""):
                try:
                    page.goto(entry_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(900)
                except Exception:
                    pass

            if accepted and len(found) >= max_clicks:
                break

        except Exception as e:
            logger.debug(f"IG 主頁 verified click probe skipped: {e}")
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
            except Exception:
                pass
            try:
                if _normalize_profile_child_url(page.url or ""):
                    page.goto(entry_url, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(800)
            except Exception:
                pass
            continue

    if rejected:
        logger.info(
            f"IG 主頁 verified click probe rejected={rejected}, "
            f"accepted={len(found)}, target=@{username}"
        )

    return _dedupe_profile_child_urls(found)

def _scan_profile_post_urls_once(
    profile_url: str,
    max_posts: int | None = None,
    headless_mode: bool = True,
) -> tuple[str, list[str], str]:
    """Scan an Instagram profile/reels tab and return discovered post / reel URLs.

    This function only expands the profile into individual post tasks.  It does
    not download media, so the existing stable single-post downloader remains the
    only path that writes files and creates title/caption-based folders.
    """
    username = is_instagram_profile_url(profile_url)
    if not username:
        return "FAILED", [], "不是可展開的 Instagram 主頁網址"

    scan_entry_urls = _profile_scan_entry_urls(profile_url, username)
    user_data_dir = _get_project_ig_parser_profile_root()
    profile_dir = _resolve_chrome_profile_directory()

    if max_posts is None:
        raw_max = _try_get_config_value("IG_PROFILE_SCAN_MAX_POSTS", 0)
        try:
            max_posts = int(raw_max or 0)
        except Exception:
            max_posts = 0

    try:
        max_scrolls = int(_try_get_config_value("IG_PROFILE_SCAN_MAX_SCROLLS", 250) or 250)
    except Exception:
        max_scrolls = 250
    try:
        wait_ms = int(_try_get_config_value("IG_PROFILE_SCAN_SCROLL_WAIT_MS", 1200) or 1200)
    except Exception:
        wait_ms = 1200
    try:
        stable_rounds = int(_try_get_config_value("IG_PROFILE_SCAN_STABLE_ROUNDS", 6) or 6)
    except Exception:
        stable_rounds = 6

    max_scrolls = max(1, min(2000, max_scrolls))
    wait_ms = max(300, min(10000, wait_ms))
    stable_rounds = max(2, min(50, stable_rounds))
    max_posts = max(0, int(max_posts or 0))

    logger.info(
        f"IG 主頁掃描開始: @{username}, entries={scan_entry_urls}, "
        f"max_posts={max_posts or 'unlimited'}, max_scrolls={max_scrolls}, "
        f"stable_rounds={stable_rounds}, profile={user_data_dir}, "
        f"mode={'headless' if headless_mode else 'visible-fallback'}"
    )

    context = None
    try:
        with sync_playwright() as p:
            launch_kwargs = {
                "user_data_dir": user_data_dir,
                "channel": "chrome",
                "headless": bool(headless_mode),
                "locale": "zh-TW",
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "args": [
                    f"--profile-directory={profile_dir}",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
            }

            if headless_mode:
                launch_kwargs["viewport"] = {"width": 1600, "height": 1200}
            else:
                launch_kwargs["no_viewport"] = True
                launch_kwargs["args"].append("--start-maximized")

            context = p.chromium.launch_persistent_context(**launch_kwargs)

            try:
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            except Exception:
                pass

            page = _get_fresh_persistent_target_page(context)
            combined_urls: list[str] = []
            seen_urls = set()

            structured_urls: list[str] = []
            structured_seen: set[str] = set()
            _attach_profile_structured_response_harvester(
                page,
                username,
                structured_urls,
                structured_seen,
            )

            had_missing_page = False
            had_blocked_page = False
            blocked_message = ""

            for entry_i, entry_url in enumerate(scan_entry_urls, start=1):
                if max_posts and len(combined_urls) >= max_posts:
                    break

                logger.info(f"IG 主頁掃描 @{username}: open entry {entry_i}/{len(scan_entry_urls)} {entry_url}")
                page.goto(entry_url, wait_until="domcontentloaded", timeout=60000)

                try:
                    page.wait_for_selector("main, article, body", timeout=20000)
                except Exception:
                    pass

                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                declared_post_count = _get_profile_declared_post_count(page, username)
                if declared_post_count:
                    logger.info(
                        f"IG 主頁 @{username}: header declared posts={declared_post_count}"
                    )

                if _is_missing_ig_page(page):
                    had_missing_page = True
                    logger.warning(f"IG 主頁掃描 @{username}: entry missing/unavailable {entry_url}")
                    continue

                if _is_generic_ig_page(page):
                    had_blocked_page = True
                    blocked_message = "IG Parser Profile 尚未登入、遇到 checkpoint/challenge，或需要重新信任裝置"
                    logger.warning(f"IG 主頁掃描 @{username}: entry blocked/generic {entry_url}")
                    continue

                stable = 0
                last_count = len(combined_urls)

                for round_i in range(max_scrolls + 1):
                    # v11.98 backend-first profile expansion:
                    # 1) GraphQL/private-API response URLs, exact owner-gated
                    # 2) embedded JSON payloads, exact owner-gated
                    # 3) visible profile DOM anchors
                    # 4) verified tile-click fallback only when backend/DOM expose
                    #    no new URLs in the current round
                    current_urls = list(structured_urls)
                    current_urls.extend(
                        _extract_profile_urls_from_embedded_json(page, username)
                    )
                    current_urls.extend(
                        _extract_profile_post_urls_from_page(page)
                    )
                    current_urls = _dedupe_profile_child_urls(current_urls)

                    has_new_backend_url = any(
                        (_extract_shortcode(u) or "") not in {
                            _extract_shortcode(x) or "" for x in combined_urls
                        }
                        for u in current_urls
                    )

                    if not has_new_backend_url:
                        remaining = (
                            max(1, declared_post_count - len(combined_urls))
                            if declared_post_count
                            else 12
                        )
                        current_urls.extend(
                            _extract_profile_post_urls_by_verified_click_probe(
                                page,
                                entry_url,
                                username,
                                already_seen=seen_urls,
                                max_clicks=min(12, remaining),
                            )
                        )
                        current_urls = _dedupe_profile_child_urls(current_urls)

                    added_this_round = 0

                    for u in current_urls:
                        if u in seen_urls:
                            continue
                        seen_urls.add(u)
                        combined_urls.append(u)
                        added_this_round += 1
                        if max_posts and len(combined_urls) >= max_posts:
                            break

                    logger.info(
                        f"IG 主頁掃描 @{username}: entry={entry_i}, round={round_i}, "
                        f"total={len(combined_urls)}, new={added_this_round}, "
                        f"declared={declared_post_count or 'unknown'}"
                    )

                    if max_posts and len(combined_urls) >= max_posts:
                        break

                    if declared_post_count and len(combined_urls) >= declared_post_count:
                        logger.info(
                            f"IG 主頁 @{username}: collected declared total "
                            f"{declared_post_count}; stop before recommendation area"
                        )
                        break

                    if len(combined_urls) == last_count:
                        stable += 1
                    else:
                        stable = 0
                        last_count = len(combined_urls)

                    if stable >= stable_rounds:
                        break

                    try:
                        if stable >= 2:
                            page.keyboard.press("End")
                        else:
                            page.evaluate(
                                "() => window.scrollBy(0, Math.max(700, Math.floor(window.innerHeight * 0.9)))"
                            )
                        page.wait_for_timeout(wait_ms)
                    except Exception:
                        time.sleep(wait_ms / 1000.0)

                    try:
                        if _is_generic_ig_page(page):
                            had_blocked_page = True
                            blocked_message = "掃描途中被導向登入 / checkpoint / challenge"
                            break
                    except Exception:
                        pass

                if declared_post_count and len(combined_urls) >= declared_post_count:
                    break

            if not combined_urls:
                if had_blocked_page:
                    return "BLOCKED", [], blocked_message or "IG Parser Profile 尚未登入或沒有權限查看此主頁"
                if had_missing_page:
                    return "MISSING", [], f"Instagram 主頁不存在、已移除，或目前帳號沒有權限查看：@{username}"
                return "RETRY", [], (
                    f"主頁 @{username} 在{'背景' if headless_mode else '可見'}模式未掃到貼文或 Reel；"
                    "可能是 Grid 尚未載入、需要人工驗證，或 IG 暫時限制"
                )

            combined_urls = _dedupe_profile_child_urls(combined_urls)
            if not combined_urls:
                return "RETRY", [], (
                    f"IG 主頁 @{username} 沒有取得可驗證的 Grid 貼文連結；"
                    "為避免把推薦內容當成該帳號貼文，拒絕假 SUCCESS"
                )

            declared_total = 0
            try:
                page.goto(
                    f"https://www.instagram.com/{username}/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )
                page.wait_for_timeout(1200)
                declared_total = _get_profile_declared_post_count(page, username)
            except Exception:
                declared_total = 0

            if declared_total and len(combined_urls) < declared_total:
                return "RETRY", [], (
                    f"IG 主頁 @{username} 掃描不完整：宣告 {declared_total} 筆，"
                    f"只取得 {len(combined_urls)} 筆；未收齊前不加入下載佇列"
                )

            if declared_total and len(combined_urls) > declared_total:
                return "RETRY", [], (
                    f"IG 主頁 @{username} 掃描結果超過宣告數：宣告 {declared_total} 筆，"
                    f"取得 {len(combined_urls)} 筆；疑似混入推薦內容，拒絕假 SUCCESS"
                )

            _remember_profile_child_owner(username, combined_urls)
            msg = (
                f"IG 主頁 @{username} 精確 Grid 掃描完成："
                f"發現 {len(combined_urls)} 筆貼文 / Reel"
                + (f"（header={declared_total}）" if declared_total else "")
            )
            logger.info(msg)
            return "SUCCESS", combined_urls, msg

    except PlaywrightTimeoutError as e:
        return "RETRY", [], f"IG 主頁掃描逾時：{e}"
    except Exception as e:
        raw = _clean_error_text(str(e))
        if "Target page, context or browser has been closed" in raw or "user data directory" in raw.lower():
            return "RETRY", [], f"IG Parser Profile 可能被手動 Chrome 視窗鎖定，請關閉 IG Parser Chrome 後重試：{raw}"
        status, err = _classify_error(raw)
        if status == "FAILED" and any(k in raw.lower() for k in ["timeout", "timed out", "net::err_timed_out"]):
            status = "RETRY"
        return status, [], f"IG 主頁掃描失敗：{err}"
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass



def scan_profile_post_urls(profile_url: str, max_posts: int | None = None) -> tuple[str, list[str], str]:
    """Expand an Instagram profile with headless-first persistent authentication.

    Normal path:
      logged-in IG Parser Profile + headless Chromium

    Visible Chrome is opened only when the background pass cannot safely finish
    because of login/checkpoint/challenge/audience confirmation, or because the
    current IG profile-grid layout cannot expose verifiable tiles headlessly.

    The single-post, carousel, Reel, img_index and Facebook download paths are
    intentionally untouched.
    """
    status_h, urls_h, message_h = _scan_profile_post_urls_once(
        profile_url,
        max_posts=max_posts,
        headless_mode=True,
    )

    if status_h != "BLOCKED":
        # SUCCESS / MISSING / FAILED / RETRY all stay background-only.
        # Incomplete URL collection is not a reason to open visible Chrome.
        return status_h, urls_h, message_h

    reason_low = (message_h or "").lower()
    needs_manual_auth = any(marker in reason_low for marker in [
        "登入",
        "login",
        "checkpoint",
        "challenge",
        "信任裝置",
        "驗證",
        "特定受眾",
        "年齡",
    ])

    if not needs_manual_auth:
        return status_h, urls_h, message_h

    logger.info(
        "IG 主頁需要人工登入／驗證，才啟用可見瀏覽器 fallback："
        f"status={status_h}, reason={message_h}"
    )

    return _scan_profile_post_urls_once(
        profile_url,
        max_posts=max_posts,
        headless_mode=False,
    )

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
        "() => { const r=document.querySelector('div[role=\"dialog\"]')||document.querySelector('article'); if(r){ try{r.setAttribute('tabindex','-1');r.focus({preventScroll:true});}catch(e){} } }",
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


def _validate_downloaded_media_geometry(path: str, item: dict) -> tuple[bool, str]:
    """Hard-gate downloaded still images against thumbnails/cropped variants."""
    if not path or not os.path.exists(path):
        return False, "媒體檔案不存在"

    media_type = (item.get("type") or "image").lower()
    if media_type == "video":
        return True, ""

    if Image is None:
        return False, "缺少 Pillow，無法驗證圖片解析度"

    expected = float(item.get("frameRatio") or item.get("renderRatio") or 0)
    source_w = int(item.get("sourceWidth") or 0)
    source_h = int(item.get("sourceHeight") or 0)

    try:
        with Image.open(path) as im:
            w, h = im.size

        if w <= 0 or h <= 0:
            return False, "圖片尺寸無效"

        # Normal images still require a 720px long edge. A narrow exception is
        # allowed only for an exact authenticated structured child where:
        # - Instagram itself declares this as the largest explicit candidate;
        # - the URL is not an explicit 240/320/480/640 resize preview;
        # - actual bytes closely match the declared candidate dimensions;
        # - long/short edges remain at least 640/480.
        if max(w, h) < 720:
            best_available = bool(
                item.get("_best_available_structured_image")
                and item.get("from") == "authenticated-structured-json"
            )
            declared_w = int(item.get("sourceWidth") or 0)
            declared_h = int(item.get("sourceHeight") or 0)
            declared_match = bool(
                declared_w > 0
                and declared_h > 0
                and abs(w - declared_w) <= max(4, int(declared_w * 0.03))
                and abs(h - declared_h) <= max(4, int(declared_h * 0.03))
            )
            acceptable_best_available = bool(
                best_available
                and declared_match
                and max(w, h) >= 640
                and min(w, h) >= 480
            )

            if not acceptable_best_available:
                return False, (
                    f"下載結果是低解析度縮圖：actual={w}x{h}"
                )

            logger.info(
                f"IG authenticated best-available image accepted: "
                f"actual={w}x{h}, declared={declared_w}x{declared_h}"
            )

        if min(w, h) < 360:
            return False, (
                f"下載圖片短邊過小，疑似裁切縮圖：actual={w}x{h}"
            )

        if source_w > 0 and source_h > 0:
            declared_long = max(source_w, source_h)
            actual_long = max(w, h)
            if declared_long >= 720 and actual_long < int(declared_long * 0.80):
                return False, (
                    f"下載圖片解析度低於來源宣告："
                    f"declared={source_w}x{source_h}, actual={w}x{h}"
                )

        actual = w / h

        if actual > 2.25 or actual < 0.44:
            return False, (
                f"下載圖片比例異常，疑似裁切殘片："
                f"actual={actual:.3f}, size={w}x{h}"
            )

        if expected > 0:
            delta = abs(actual - expected) / max(expected, 0.01)
            if delta > 0.14:
                return False, (
                    f"下載圖片比例與貼文畫面不符："
                    f"expected={expected:.3f}, actual={actual:.3f}, "
                    f"size={w}x{h}"
                )

        return True, ""

    except Exception as e:
        return False, f"圖片解析度驗證失敗：{e}"


def _normalized_media_identity(url: str) -> str:
    """Return strict IG media identity using host + path, ignoring query tokens."""
    try:
        cleaned = html.unescape(unquote(str(url or "").strip()))
        parsed = urlparse(cleaned)
        host = (parsed.netloc or "").lower()
        path = re.sub(r"/+", "/", parsed.path or "")
        return f"{host}{path}".lower()
    except Exception:
        return str(url or "").strip().lower()


def _normalized_exact_media_url(url: str) -> str:
    """Normalize a media URL while preserving the query string.

    Instagram often serves multiple image resolutions from the same CDN path and
    distinguishes them only through query parameters such as ``stp``.  Those
    variants must remain separate for still-image quality fallback.
    """
    try:
        cleaned = html.unescape(unquote(str(url or "").strip()))
        parsed = urlparse(cleaned)
        host = (parsed.netloc or "").lower()
        path = re.sub(r"/+", "/", parsed.path or "")
        query = parsed.query or ""
        return f"{host}{path}?{query}".lower() if query else f"{host}{path}".lower()
    except Exception:
        return str(url or "").strip().lower()


def _find_exact_harvested_media(harvested_media: dict, item: dict):
    """Return only cached media that belongs to the exact requested candidate.

    Video fragments keep path-based matching because init/media range requests
    commonly use changing query tokens.  Still images require full URL matching,
    including the query string, so a cached 679px preview cannot be reused for a
    different 1080px structured candidate sharing the same CDN path.
    """
    media_url = item.get("src", "") or ""
    media_type = (item.get("type") or "image").lower()
    wanted_identity = (
        _normalized_media_identity(media_url)
        if media_type == "video"
        else _normalized_exact_media_url(media_url)
    )

    best = None
    best_score = -1

    for candidate in (harvested_media or {}).values():
        candidate_url = candidate.get("src", "") or ""
        candidate_type = (candidate.get("type") or "image").lower()
        content_type = (candidate.get("content_type") or "").lower()

        if media_type == "video":
            if candidate_type != "video" and not content_type.startswith("video/"):
                continue
            candidate_identity = _normalized_media_identity(candidate_url)
        else:
            if candidate_type == "video" or content_type.startswith("video/"):
                continue
            candidate_identity = _normalized_exact_media_url(candidate_url)

        if candidate_identity != wanted_identity:
            continue

        score = int(candidate.get("score") or 0)
        if candidate.get("body") and score > best_score:
            best = candidate
            best_score = score

    return best




def _replace_requested_slide_with_harvested_video(
    filtered,
    harvested_media,
    original_url: str,
    target_shortcode: str,
):
    """Replace only the requested image slot when a complete video can be built."""
    requested_index = _get_requested_img_index(original_url)
    if requested_index <= 1:
        return filtered

    slot = requested_index - 1
    if slot < 0 or slot >= len(filtered):
        return filtered

    current_item = filtered[slot] or {}
    current_type = (current_item.get("type") or "image").lower()
    current_url = (current_item.get("src") or "").lower()

    if current_type == "video" or any(
        ext in current_url for ext in [".mp4", ".m4v", ".mov"]
    ):
        return filtered

    video_candidates = []
    for candidate in (harvested_media or {}).values():
        if not isinstance(candidate, dict):
            continue

        candidate_url = candidate.get("src", "") or ""
        candidate_type = (candidate.get("type") or "").lower()
        content_type = (candidate.get("content_type") or "").lower()
        body = candidate.get("body") or b""

        is_video = (
            candidate_type == "video"
            or content_type.startswith("video/")
            or any(
                ext in candidate_url.lower()
                for ext in [".mp4", ".m4v", ".mov"]
            )
            or _detect_media_format_from_bytes(
                body[:64],
                media_url=candidate_url,
                content_type=content_type,
            ) == "mp4"
        )
        if is_video:
            video_candidates.append(candidate)

    if not video_candidates:
        logger.info(
            f"IG requested-slide video replacement skipped: "
            f"img_index={requested_index}, harvested_video=0, "
            f"target={target_shortcode}"
        )
        return filtered

    init_parts = 0
    media_parts = 0
    complete_parts = 0
    for candidate in video_candidates:
        for part in list(candidate.get("parts") or []):
            kind = part.get("fragment_kind") or _classify_mp4_fragment_body(
                part.get("body") or b""
            )
            if kind == "init":
                init_parts += 1
            elif kind == "media":
                media_parts += 1
            elif kind == "complete":
                complete_parts += 1

    logger.info(
        f"IG requested-slide video fragment inventory: "
        f"img_index={requested_index}, init={init_parts}, "
        f"media={media_parts}, complete={complete_parts}, "
        f"candidates={len(video_candidates)}, target={target_shortcode}"
    )

    complete_body = _build_complete_mp4_from_harvested_candidates(
        video_candidates
    )

    video_candidates.sort(
        key=lambda item: (
            int(item.get("score") or 0),
            len(item.get("body") or b""),
        ),
        reverse=True,
    )
    chosen = video_candidates[0]
    chosen_url = chosen.get("src", "") or ""

    if not chosen_url:
        return filtered

    replacement = {
        **current_item,
        "src": chosen_url,
        "type": "video",
        "score": max(
            int(current_item.get("score") or 0),
            int(chosen.get("score") or 0),
        ),
        "requested_slide_video_replacement": True,
    }

    if complete_body:
        replacement["_complete_video_body"] = complete_body
        logger.info(
            f"IG requested-slide complete video assembled: "
            f"img_index={requested_index}, bytes={len(complete_body)}, "
            f"candidates={len(video_candidates)}, target={target_shortcode}"
        )
    else:
        logger.info(
            f"IG requested-slide video fragments captured but not complete: "
            f"img_index={requested_index}, candidates={len(video_candidates)}, "
            f"target={target_shortcode}; use range fallback"
        )

    replaced = list(filtered)
    replaced[slot] = replacement

    logger.info(
        f"IG requested-slide video replacement applied: "
        f"img_index={requested_index}, candidates={len(video_candidates)}, "
        f"target={target_shortcode}"
    )
    return replaced




def _validate_downloaded_media_type(path: str, item: dict) -> tuple[bool, str]:
    """Ensure a Reel/video is a real playable MP4 and images are not videos."""
    if not path or not os.path.exists(path):
        return False, "下載檔案不存在"

    expected_type = (item.get("type") or "image").lower()
    real_ext = _real_ext_for_file(path)

    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0

    if expected_type == "video":
        if real_ext != ".mp4":
            return False, (
                f"影片下載結果不是 MP4，而是 {real_ext or 'unknown'}"
            )
        if size < 100 * 1024:
            return False, (
                f"影片檔案過小，疑似封面或殘片：{size} bytes"
            )
        try:
            with open(path, "rb") as fh:
                body = fh.read()
            if not _is_playable_mp4_body(body):
                return False, "影片不是完整可播放 MP4"
        except Exception as e:
            return False, f"影片完整性驗證失敗：{e}"
        return True, ""

    if real_ext == ".mp4":
        return False, "圖片項目實際下載成影片，媒體類型不符"

    return True, ""



def _download_filtered_items_from_context(context, filtered, harvested_media, title: str, shortcode: str, referer: str):
    """Write only media explicitly owned by the current shortcode.

    For structured still images, try candidates in true resolution order. If a
    CDN response is smaller than declared or fails the quality gate, retry the
    next exact-child URL instead of failing the whole carousel immediately.
    """
    if not filtered:
        return "FAILED", "IG 沒有可下載的目標媒體"

    for item in filtered:
        owner = item.get("_target_shortcode")
        if owner != shortcode:
            return "FAILED", (
                f"IG 媒體 shortcode 不符：expected={shortcode}, "
                f"actual={owner or 'missing'}"
            )

    success_count = 0

    for i, item in enumerate(filtered, 1):
        media_type = (item.get("type") or "image").lower()
        primary_url = item.get("src", "")

        candidate_urls = [primary_url]
        if media_type != "video":
            candidate_urls.extend(item.get("_alternate_image_urls") or [])

        deduped_urls = []
        seen_candidates = set()
        for candidate_url in candidate_urls:
            candidate_url = str(candidate_url or "").strip()
            if not candidate_url:
                continue

            # Still-image resolution variants frequently share the same CDN path
            # and differ only in query parameters.  Preserve those exact variants
            # so the quality gate can retry 1080px/original candidates after a
            # cached preview fails.  Video keeps the existing path identity for
            # fragment/range consolidation.
            if media_type == "video":
                key = _media_key_from_url(candidate_url) or candidate_url
            else:
                key = _normalized_exact_media_url(candidate_url)

            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            deduped_urls.append(candidate_url)
        candidate_urls = deduped_urls or [primary_url]

        candidate_errors = []
        candidate_succeeded = False

        for candidate_index, media_url in enumerate(candidate_urls, 1):
            attempt_item = dict(item)
            attempt_item["src"] = media_url
            ext = (
                ".mp4"
                if media_type == "video" or any(x in media_url.lower() for x in [".mp4", ".m4v", ".mov"])
                else _ext_from_url(media_url, ".jpg")
            )
            dst = os.path.join(TEMP_DIR, f"ig_{i}{ext}")

            try:
                embedded_video_body = attempt_item.get("_complete_video_body") or b""
                harvested = _find_exact_harvested_media(harvested_media, attempt_item)
                harvested_body = harvested.get("body") if harvested else b""
                harvested_type = ((harvested.get("type") or "").lower() if harvested else "")
                harvested_content_type = (harvested.get("content_type", "") if harvested else "")
                is_video_item = (
                    media_type == "video"
                    or harvested_type == "video"
                    or harvested_content_type.lower().startswith("video/")
                    or any(x in media_url.lower() for x in [".mp4", ".m4v", ".mov"])
                )

                use_cached_body = bool(harvested_body)
                fragment_video = bool(
                    use_cached_body
                    and is_video_item
                    and not _is_playable_mp4_body(harvested_body)
                )

                if embedded_video_body:
                    _write_media_body(dst, embedded_video_body, media_url=media_url, content_type="video/mp4")
                    logger.info(f"IG 第 {i} 個影片使用 browser init/media fragments 組成完整 MP4")
                elif fragment_video:
                    logger.info(f"IG 第 {i} 個影片 cache 為 fragmented MP4 segment；從 byte 0 重新分段下載並重建完整 MP4")
                    _download_complete_mp4_with_ranges(context, media_url, dst, referer=referer)
                    logger.info(f"IG 第 {i} 個影片完整 MP4 重建完成")
                elif use_cached_body:
                    _write_media_body(dst, harvested_body, media_url=media_url, content_type=harvested_content_type)
                    logger.info(f"IG 使用 browser network cache 寫入第 {i} 個媒體")
                else:
                    if is_video_item:
                        _download_complete_mp4_with_ranges(context, media_url, dst, referer=referer)
                        logger.info(f"IG 第 {i} 個影片完整 MP4 下載完成")
                    else:
                        _download_with_playwright_request(context, media_url, dst, referer=referer)

                actual_candidates = [
                    path for path in _list_media_files(TEMP_DIR)
                    if os.path.basename(path).startswith(f"ig_{i}")
                ]
                actual_path = actual_candidates[-1] if actual_candidates else dst

                type_ok, type_reason = _validate_downloaded_media_type(actual_path, attempt_item)
                if not type_ok:
                    raise Exception(type_reason)

                geometry_ok, geometry_reason = _validate_downloaded_media_geometry(actual_path, attempt_item)
                if not geometry_ok:
                    raise Exception(geometry_reason)

                candidate_succeeded = True
                success_count += 1
                if media_type != "video" and candidate_index > 1:
                    logger.info(
                        f"IG 第 {i} 張圖片已改用第 {candidate_index} 個結構化高解析度候選並通過驗證"
                    )
                break

            except Exception as exc:
                for path in list(_list_media_files(TEMP_DIR)):
                    if os.path.basename(path).startswith(f"ig_{i}"):
                        try:
                            os.remove(path)
                        except Exception:
                            pass

                candidate_errors.append(str(exc))

                if media_type != "video" and candidate_index < len(candidate_urls):
                    logger.info(
                        f"IG 第 {i} 張圖片候選未通過品質 gate，改試下一個結構化高解析度 URL "
                        f"({candidate_index + 1}/{len(candidate_urls)}): {exc}"
                    )
                    continue

                if item.get("requested_slide_video_replacement"):
                    raise Exception(
                        f"IG requested slide video download failed at index {i}: {exc}"
                    ) from exc

                logger.warning(
                    f"IG 略過無效媒體: {media_url[:180]} | {' | '.join(candidate_errors)}"
                )
                break

        if not candidate_succeeded:
            continue

    if success_count <= 0:
        return "FAILED", "IG 媒體全部未通過影片/圖片完整性與解析度驗證"

    temp_files_after_capture = _list_media_files(TEMP_DIR)
    if success_count != len(filtered) or len(temp_files_after_capture) != len(filtered):
        return "FAILED", (
            f"IG 媒體完整性檢查失敗：expected={len(filtered)}, "
            f"written={success_count}, temp_files={len(temp_files_after_capture)}"
        )

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



def _wait_for_target_shortcode_context(page, target_shortcode: str, timeout_ms: int = 9000) -> bool:
    """Wait until Instagram SPA/canonical state belongs to the requested shortcode.

    Persistent Chrome profiles can keep old IG DOM/dialog nodes during navigation.
    If collection starts while the old DOM is still visible, the first media can
    be taken from the previous post even though the URL already changed.  This
    helper waits for the target shortcode context before harvesting media.
    """
    if not target_shortcode:
        return True
    deadline = time.time() + max(0.5, timeout_ms / 1000.0)
    while time.time() < deadline:
        if _is_target_shortcode_context(page, target_shortcode):
            return True
        try:
            page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)
    return _is_target_shortcode_context(page, target_shortcode)



def _get_first_slide_navigation_url(target_url: str) -> str:
    """Return the same IG post URL without img_index so navigation starts at slide 1.

    Route selection still uses the original URL, therefore the verified v7/v8
    restricted-carousel strategy is preserved.  Only the browser navigation URL
    is canonicalized.  This avoids relying on Instagram's locale-dependent
    Previous button, which may be absent even when an img_index share URL opens
    directly on slide 2 or later.
    """
    try:
        parsed = urlparse(str(target_url or ""))
        query = parse_qs(parsed.query, keep_blank_values=True)
        query.pop("img_index", None)

        clean_pairs = []
        for key, values in query.items():
            for value in values:
                clean_pairs.append((key, value))

        clean_query = urlencode(clean_pairs, doseq=True)
        clean_path = parsed.path
        if clean_path and not clean_path.endswith("/"):
            clean_path += "/"

        return parsed._replace(path=clean_path, query=clean_query, fragment="").geturl()
    except Exception:
        return target_url



def _goto_instagram_target_clean(page, target_url: str, target_shortcode: str = "", timeout: int = 60000):
    """Hard-reset the visible tab before opening a new IG post/reel.

    This is intentionally conservative: it does not change carousel collection,
    order logic, WEBP conversion, move_files(), or download strategy.  It only
    prevents stale DOM/network state from a previously opened post in the
    persistent IG_Parser profile from being treated as the first slide of the
    next task.
    """
    normalized = _normalize_ig_url(target_url)
    requested_img_index = _get_requested_img_index(target_url)
    navigation_url = _get_first_slide_navigation_url(normalized)

    if requested_img_index > 1 and navigation_url != normalized:
        logger.info(
            f"IG canonical first-slide navigation lock: requested_img_index={requested_img_index}, "
            f"target={target_shortcode}; remove img_index only for browser navigation"
        )

    try:
        page.goto("about:blank", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(450)
    except Exception:
        pass

    try:
        page.evaluate("() => { try { performance.clearResourceTimings(); } catch(e){} }")
    except Exception:
        pass

    page.goto(navigation_url, wait_until="domcontentloaded", timeout=timeout)
    _wait_for_target_shortcode_context(page, target_shortcode, timeout_ms=12000)

    try:
        page.wait_for_selector("article, div[role='dialog']", timeout=12000)
    except Exception:
        pass

    try:
        page.wait_for_load_state("networkidle", timeout=9000)
    except Exception:
        pass


def _get_carousel_total_count(page) -> int:
    """Detect carousel page count from dots/indicators near the main media.

    This is intentionally advisory only.  It is used to stop repeated flipping
    and to know whether network cache should fill 1 missing carousel item.
    """
    js = r"""
    () => {
      const scope = document.querySelector('div[role="dialog"]') || document.querySelector('article') || document;
      const medias = Array.from(scope.querySelectorAll('img, video'))
        .map(el => {
          const r = el.getBoundingClientRect();
          const st = getComputedStyle(el);
          return {el, r, area: Math.max(0, r.width) * Math.max(0, r.height), visible: st.display !== 'none' && st.visibility !== 'hidden' && r.width > 120 && r.height > 120};
        })
        .filter(x => x.visible)
        .sort((a,b) => b.area - a.area);
      if (!medias.length) return 0;
      const mr = medias[0].r;
      const candidates = [];
      const nodes = Array.from(document.querySelectorAll('div, span, button, svg, circle'));
      for (const el of nodes) {
        const r = el.getBoundingClientRect();
        const st = getComputedStyle(el);
        const w = r.width, h = r.height;
        if (st.display === 'none' || st.visibility === 'hidden' || parseFloat(st.opacity || '1') <= 0) continue;
        if (w < 3 || h < 3 || w > 22 || h > 22) continue;
        const cx = r.left + w / 2;
        const cy = r.top + h / 2;
        const nearX = cx >= mr.left + mr.width * 0.25 && cx <= mr.right - mr.width * 0.25;
        const nearY = cy >= mr.bottom - 90 && cy <= mr.bottom + 35;
        if (!nearX || !nearY) continue;
        candidates.push({x: Math.round(cx), y: Math.round(cy), w, h});
      }
      candidates.sort((a,b) => a.x - b.x);
      const grouped = [];
      for (const c of candidates) {
        if (!grouped.length || Math.abs(grouped[grouped.length - 1].x - c.x) > 7) grouped.push(c);
      }
      const n = grouped.length;
      return (n >= 2 && n <= 20) ? n : 0;
    }
    """
    try:
        return int(page.evaluate(js) or 0)
    except Exception:
        return 0


def _get_current_media_key(page) -> str:
    try:
        current = _get_current_slide_main_media(page)
        if current:
            return _media_key_from_url(current[0].get("src", ""))
    except Exception:
        pass
    return ""



def _is_actionable_carousel_previous(page) -> bool:
    """Return True when a visible enabled Previous control still exists."""
    selectors = [
        'button[aria-label="Previous"]',
        'button[aria-label="上一張"]',
        'button[aria-label="上一則"]',
        'button[aria-label="上一步"]',
        'button[aria-label="往前"]',
        'div[role="button"][aria-label="Previous"]',
        'div[role="button"][aria-label="上一張"]',
        'div[role="button"][aria-label="上一則"]',
        'div[role="button"][aria-label="往前"]',
        'svg[aria-label="Previous"]',
        'svg[aria-label="上一張"]',
        'svg[aria-label="往前"]',
    ]
    for sel in selectors:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 8)):
                node = loc.nth(i)
                if not node.is_visible(timeout=250):
                    continue
                disabled = False
                try:
                    disabled = bool(node.is_disabled())
                except Exception:
                    pass
                try:
                    disabled = disabled or (node.get_attribute("aria-disabled") or "").lower() == "true"
                except Exception:
                    pass
                try:
                    disabled = disabled or "disabled" in (node.get_attribute("class") or "").lower()
                except Exception:
                    pass
                if not disabled:
                    return True
        except Exception:
            continue
    return False



def _click_prev_ig(page) -> bool:
    selectors = [
        'button[aria-label="Previous"]',
        'button[aria-label="上一張"]',
        'button[aria-label="上一則"]',
        'button[aria-label="上一步"]',
        'button[aria-label="往前"]',
        'div[role="button"][aria-label="Previous"]',
        'div[role="button"][aria-label="上一張"]',
        'div[role="button"][aria-label="上一則"]',
        'div[role="button"][aria-label="往前"]',
        'svg[aria-label="Previous"]',
        'svg[aria-label="上一張"]',
        'svg[aria-label="往前"]',
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
                        page.evaluate("(el) => el.closest('button, div[role=button]')?.click()", handle)
                page.wait_for_timeout(1200)
                return True
        except Exception:
            continue
    return False


def _wait_for_ig_media_key_change(page, before_key: str, timeout_ms: int = 5200) -> str:
    """Wait for React/lazy carousel transition to expose a genuinely new media key."""
    deadline = time.time() + max(0.8, timeout_ms / 1000.0)
    last = ""
    while time.time() < deadline:
        try:
            last = _get_current_media_key(page)
        except Exception:
            last = ""
        if last and (not before_key or last != before_key):
            return last
        try:
            page.wait_for_timeout(220)
        except Exception:
            time.sleep(0.22)
    return last


def _click_prev_ig_locked(page, target_shortcode: str) -> bool:
    before_key = _get_current_media_key(page)
    before_url = ""
    try:
        before_url = page.url or ""
    except Exception:
        pass
    moved = _click_prev_ig(page)
    if not moved:
        return False
    if target_shortcode and not _is_target_shortcode_context(page, target_shortcode):
        logger.warning(
            f"IG carousel prev guard: click left target={target_shortcode}; before={before_url}; after={getattr(page, 'url', '')}; stop"
        )
        return False
    after_key = _wait_for_ig_media_key_change(page, before_key, timeout_ms=5200)
    return bool(after_key and after_key != before_key)


def _rewind_carousel_to_first(page, target_shortcode: str, max_steps: int = 8) -> int:
    moved = 0
    for _ in range(max_steps):
        if not _click_prev_ig_locked(page, target_shortcode):
            break
        moved += 1
    if moved:
        logger.info(f"IG carousel rewind to first slide: moved_previous={moved}, target={target_shortcode}")
    return moved


def _is_actionable_carousel_next(page) -> bool:
    """Return True only when a visible, enabled Next control still exists.

    IG sponsored carousels can keep hidden or disabled chevrons in the DOM.
    Those must not be treated as another slide.
    """
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
            for i in range(min(loc.count(), 8)):
                node = loc.nth(i)
                if not node.is_visible(timeout=250):
                    continue
                disabled = False
                try:
                    disabled = bool(node.is_disabled())
                except Exception:
                    pass
                try:
                    aria_disabled = (node.get_attribute("aria-disabled") or "").lower()
                    disabled = disabled or aria_disabled == "true"
                except Exception:
                    pass
                try:
                    cls = (node.get_attribute("class") or "").lower()
                    disabled = disabled or "disabled" in cls
                except Exception:
                    pass
                if not disabled:
                    return True
        except Exception:
            continue
    return False


def _wait_for_current_media_key_change(page, before_key: str, target_shortcode: str, timeout_ms: int = 6500) -> str:
    """Wait for React/lazy-load to replace the active slide media.

    The initial dot count is not trusted.  A click counts as a real carousel
    advance only after the target shortcode remains locked and the main-media
    key changes.
    """
    deadline = time.time() + max(0.8, timeout_ms / 1000.0)
    last_key = ""
    while time.time() < deadline:
        if target_shortcode and not _is_target_shortcode_context(page, target_shortcode):
            return ""
        try:
            last_key = _get_current_media_key(page) or ""
        except Exception:
            last_key = ""
        if last_key and (not before_key or last_key != before_key):
            return last_key
        try:
            page.wait_for_timeout(250)
        except Exception:
            time.sleep(0.25)
    return ""




def _get_carousel_state_signature(page) -> str:
    """Return a slide signature that survives IG reusing the same img element."""
    try:
        data = page.evaluate(
            r"""
            () => {
              const root = document.querySelector('div[role="dialog"]') || document.querySelector('article');
              if (!root) return {key:'', dot:-1, transform:''};
              const media = Array.from(root.querySelectorAll('img,video'))
                .map(el => { const r=el.getBoundingClientRect(), st=getComputedStyle(el); return {el,r,area:r.width*r.height,ok:st.display!=='none'&&st.visibility!=='hidden'&&parseFloat(st.opacity||'1')>0&&r.width>120&&r.height>120&&r.right>0&&r.bottom>0&&r.left<innerWidth&&r.top<innerHeight}; })
                .filter(x=>x.ok).sort((a,b)=>b.area-a.area)[0];
              let key='', transform='';
              if (media) {
                const el=media.el;
                key=el.currentSrc||el.src||el.getAttribute('src')||'';
                let p=el;
                for(let i=0;i<5&&p;i++,p=p.parentElement){const tr=getComputedStyle(p).transform||'';if(tr&&tr!=='none'){transform=tr;break;}}
              }
              let dot=-1;
              const dots=Array.from(root.querySelectorAll('[aria-current],button,div,span'))
                .map(el=>{const r=el.getBoundingClientRect(),st=getComputedStyle(el),aria=(el.getAttribute('aria-current')||'').toLowerCase(),cls=(el.getAttribute('class')||'').toLowerCase();return {r,active:aria==='true'||aria==='page'||cls.includes('active')||cls.includes('selected'),op:parseFloat(st.opacity||'1')};})
                .filter(x=>x.op>0&&x.r.width>=3&&x.r.height>=3&&x.r.width<=24&&x.r.height<=24&&x.r.top>innerHeight*0.45)
                .sort((a,b)=>a.r.left-b.r.left);
              const compact=[];
              for(const x of dots){const cx=x.r.left+x.r.width/2;if(!compact.length||Math.abs((compact[compact.length-1].r.left+compact[compact.length-1].r.width/2)-cx)>7)compact.push(x);}
              dot=compact.findIndex(x=>x.active);
              return {key,dot,transform};
            }
            """
        ) or {}
    except Exception:
        data={}
    raw_key=str(data.get('key') or '')
    key=_media_key_from_url(raw_key) if raw_key else ''
    return f"{key}|dot={data.get('dot',-1)}|tr={data.get('transform','')}"


def _wait_for_carousel_state_change(page, before_signature: str, target_shortcode: str, timeout_ms: int = 6500) -> bool:
    deadline=time.time()+max(0.8,timeout_ms/1000.0)
    while time.time()<deadline:
        if target_shortcode and not _is_target_shortcode_context(page,target_shortcode):
            return False
        now=_get_carousel_state_signature(page)
        if now and before_signature and now!=before_signature:
            return True
        try: page.wait_for_timeout(220)
        except Exception: time.sleep(0.22)
    return False



def _get_main_media_visual_fingerprint(page) -> str:
    """Hash the visible main-media pixels for restricted Carousel verification."""
    try:
        box = page.evaluate(
            r"""
            () => {
              const root = document.querySelector('div[role="dialog"]') ||
                           document.querySelector('article');
              if (!root) return null;
              const nodes = Array.from(root.querySelectorAll('img,video'))
                .map(el => {
                  const r = el.getBoundingClientRect();
                  const st = getComputedStyle(el);
                  const overlapX = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
                  const overlapY = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
                  return {
                    r,
                    visibleArea: overlapX * overlapY,
                    ok: st.display !== 'none' && st.visibility !== 'hidden' &&
                        parseFloat(st.opacity || '1') > 0 &&
                        r.width > 220 && r.height > 180 &&
                        overlapX > 180 && overlapY > 160
                  };
                })
                .filter(x => x.ok)
                .sort((a,b) => b.visibleArea - a.visibleArea);
              if (!nodes.length) return null;
              const r = nodes[0].r;
              return {
                x: Math.max(0, r.left + 4),
                y: Math.max(0, r.top + 4),
                width: Math.max(20, Math.min(innerWidth - Math.max(0, r.left + 4), r.width - 8)),
                height: Math.max(20, Math.min(innerHeight - Math.max(0, r.top + 4), r.height - 8))
              };
            }
            """
        )
        if not box:
            return ""
        shot = page.screenshot(
            clip={
                "x": float(box["x"]),
                "y": float(box["y"]),
                "width": float(box["width"]),
                "height": float(box["height"]),
            },
            animations="disabled",
            timeout=5000,
        )
        return hashlib.sha1(shot).hexdigest()
    except Exception:
        return ""


def _wait_for_visual_frame_change(page, before_hash: str, target_shortcode: str, timeout_ms: int = 6000) -> bool:
    if not before_hash:
        return False
    deadline = time.time() + max(1.0, timeout_ms / 1000.0)
    while time.time() < deadline:
        if target_shortcode and not _is_target_shortcode_context(page, target_shortcode):
            return False
        now_hash = _get_main_media_visual_fingerprint(page)
        if now_hash and now_hash != before_hash:
            return True
        try:
            page.wait_for_timeout(280)
        except Exception:
            time.sleep(0.28)
    return False


def _find_visible_next_arrow_point(page) -> dict:
    """Locate a visible Next control strictly beside the main media."""
    _hover_main_ig_media(page)
    try:
        return page.evaluate(
            r"""
            () => {
              const medias = Array.from(document.querySelectorAll('img,video'))
                .map(el => {
                  const r = el.getBoundingClientRect();
                  const st = getComputedStyle(el);
                  const overlapX = Math.max(
                    0, Math.min(r.right, innerWidth) - Math.max(r.left, 0)
                  );
                  const overlapY = Math.max(
                    0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0)
                  );
                  const visibleArea = overlapX * overlapY;
                  return {
                    r, visibleArea,
                    ok:
                      st.display !== 'none' &&
                      st.visibility !== 'hidden' &&
                      parseFloat(st.opacity || '1') > 0 &&
                      r.width > 220 &&
                      r.height > 180 &&
                      visibleArea > 50000
                  };
                })
                .filter(x => x.ok)
                .sort((a,b) => b.visibleArea - a.visibleArea);

              if (!medias.length) return null;

              const mr = medias[0].r;
              const my = mr.top + mr.height / 2;
              const nodes = Array.from(document.querySelectorAll(
                'button,[role="button"],svg,[aria-label],[title],.coreSpriteRightChevron'
              ));
              const candidates = [];
              const seen = new Set();

              for (const el of nodes) {
                const clicker = el.closest('button,[role="button"]') || el;
                if (seen.has(clicker)) continue;
                seen.add(clicker);

                const r = clicker.getBoundingClientRect();
                const st = getComputedStyle(clicker);
                if (
                  st.display === 'none' ||
                  st.visibility === 'hidden' ||
                  parseFloat(st.opacity || '1') <= 0
                ) continue;
                if (
                  r.width < 12 || r.height < 12 ||
                  r.width > 130 || r.height > 130
                ) continue;
                if (
                  r.right <= 0 || r.bottom <= 0 ||
                  r.left >= innerWidth || r.top >= innerHeight
                ) continue;

                const cx = r.left + r.width / 2;
                const cy = r.top + r.height / 2;
                const label = (
                  (clicker.getAttribute('aria-label') || '') + ' ' +
                  (el.getAttribute('aria-label') || '') + ' ' +
                  (clicker.getAttribute('title') || '') + ' ' +
                  (el.getAttribute('title') || '') + ' ' +
                  (clicker.getAttribute('class') || '') + ' ' +
                  (el.getAttribute('class') || '')
                ).trim().toLowerCase();

                const previous =
                  label.includes('previous') ||
                  label.includes('上一張') ||
                  label.includes('上一則') ||
                  label.includes('上一步') ||
                  label.includes('往回') ||
                  label.includes('返回') ||
                  label.includes('back') ||
                  label.includes('corespriteleftchevron');
                if (previous) continue;

                const spatiallyValid =
                  cx >= mr.right - 125 &&
                  cx <= mr.right + 90 &&
                  cx >= mr.left + mr.width * 0.58 &&
                  Math.abs(cy - my) <= Math.max(115, mr.height * 0.34);

                // Critical: even an explicit "下一步" label is rejected when it
                // is at the top-right header instead of beside the media.
                if (!spatiallyValid) continue;

                const explicitNext =
                  label.includes('next') ||
                  label.includes('下一張') ||
                  label.includes('下一則') ||
                  label.includes('下一步') ||
                  label.includes('次へ') ||
                  label.includes('corespriterightchevron');

                const score =
                  (explicitNext ? 100000 : 0) -
                  Math.abs(cx - (mr.right - 28)) * 50 -
                  Math.abs(cy - my) * 12 +
                  r.width * r.height;

                candidates.push({
                  x: Math.max(2, Math.min(innerWidth - 2, cx)),
                  y: Math.max(2, Math.min(innerHeight - 2, cy)),
                  label,
                  score,
                  rect: {
                    left:r.left, top:r.top,
                    width:r.width, height:r.height
                  },
                  media: {
                    left:mr.left, top:mr.top, right:mr.right,
                    width:mr.width, height:mr.height
                  }
                });
              }

              candidates.sort((a,b) => b.score - a.score);
              return candidates[0] || null;
            }
            """
        ) or {}
    except Exception:
        return {}



def _click_visible_next_arrow_with_mouse(
    page,
    before_signature: str,
    before_visual_hash: str,
    target_shortcode: str,
) -> bool:
    point = _find_visible_next_arrow_point(page)
    if not point:
        logger.warning(f"IG visible next-arrow point not found: target={target_shortcode}")
        return False

    try:
        x = float(point.get("x"))
        y = float(point.get("y"))
        label = str(point.get("label") or "")
        logger.info(
            f"IG visible next-arrow mouse target: x={int(x)}, y={int(y)}, "
            f"label={label or '-'}, target={target_shortcode}"
        )
        page.mouse.move(x, y)
        page.wait_for_timeout(180)
        page.mouse.down()
        page.wait_for_timeout(90)
        page.mouse.up()
    except Exception as exc:
        logger.warning(f"IG visible next-arrow mouse click failed: {exc}")
        return False

    if _wait_for_carousel_state_change(
        page, before_signature, target_shortcode, timeout_ms=3000
    ):
        logger.info(f"IG visible next-arrow state advanced: target={target_shortcode}")
        return True

    if _wait_for_visual_frame_change(
        page, before_visual_hash, target_shortcode, timeout_ms=4500
    ):
        logger.info(f"IG visible next-arrow visual frame advanced: target={target_shortcode}")
        return True

    return False



def _click_next_by_real_mouse(page, before_signature: str, target_shortcode: str) -> bool:
    """Use a genuine pointer click on the visible carousel chevron/media edge."""
    try:
        info=page.evaluate(r"""
        () => {
          const root=document.querySelector('div[role="dialog"]')||document.querySelector('article');
          if(!root)return null;
          const media=Array.from(root.querySelectorAll('img,video'))
            .map(el=>{const r=el.getBoundingClientRect(),st=getComputedStyle(el);return {r,area:r.width*r.height,ok:st.display!=='none'&&st.visibility!=='hidden'&&parseFloat(st.opacity||'1')>0&&r.width>220&&r.height>180&&r.right>0&&r.bottom>0&&r.left<innerWidth&&r.top<innerHeight};})
            .filter(x=>x.ok).sort((a,b)=>b.area-a.area)[0];
          if(!media)return null;
          const r=media.r; return {left:r.left,top:r.top,right:r.right,width:r.width,height:r.height};
        }
        """)
        if not info:return False
        y_values=[0.50,0.46,0.54]
        x_values=[info['right']-30,info['right']-18,info['right']+8]
        for yr in y_values:
            y=info['top']+info['height']*yr
            for x in x_values:
                try:
                    page.mouse.move(x,y); page.wait_for_timeout(120); page.mouse.click(x,y,delay=90)
                except Exception:
                    continue
                if _wait_for_carousel_state_change(page,before_signature,target_shortcode,timeout_ms=3600):
                    logger.info(f"IG real-mouse next-arrow advanced: x={int(x)}, y={int(y)}, target={target_shortcode}")
                    return True
        return False
    except Exception:
        return False



def _click_next_carousel_dot(page, before_key: str, target_shortcode: str) -> bool:
    """Click the first pagination dot to the right of the active dot.

    Used only after the normal Next button and ArrowRight both failed.  The dot
    search is spatially limited to the main media frame, so normal posts are
    unaffected.
    """
    try:
        clicked = page.evaluate(
            r"""
            () => {
              const root = document.querySelector('div[role="dialog"]') ||
                           document.querySelector('article');
              if (!root) return false;

              const medias = Array.from(root.querySelectorAll('img,video'))
                .map(el => {
                  const r = el.getBoundingClientRect();
                  const st = getComputedStyle(el);
                  return {el,r,area:r.width*r.height,ok:
                    st.display !== 'none' && st.visibility !== 'hidden' &&
                    parseFloat(st.opacity||'1') > 0 && r.width > 120 && r.height > 120};
                })
                .filter(x => x.ok)
                .sort((a,b)=>b.area-a.area);
              if (!medias.length) return false;
              const mr = medias[0].r;

              const raw = Array.from(root.querySelectorAll('button,div,span,svg,circle'))
                .map(el => {
                  const r = el.getBoundingClientRect();
                  const st = getComputedStyle(el);
                  const bg = st.backgroundColor || '';
                  const op = parseFloat(st.opacity || '1');
                  const cls = (el.getAttribute('class') || '').toLowerCase();
                  const aria = (el.getAttribute('aria-current') || '').toLowerCase();
                  const active = aria === 'true' || aria === 'page' ||
                    cls.includes('active') || cls.includes('selected') ||
                    (!bg.includes('rgba(0, 0, 0, 0)') && !bg.includes('transparent'));
                  return {el,r,op,active};
                })
                .filter(x =>
                  x.op > 0 &&
                  x.r.width >= 3 && x.r.height >= 3 &&
                  x.r.width <= 22 && x.r.height <= 22 &&
                  x.r.left + x.r.width/2 >= mr.left + mr.width*0.20 &&
                  x.r.left + x.r.width/2 <= mr.right - mr.width*0.20 &&
                  x.r.top + x.r.height/2 >= mr.bottom - 100 &&
                  x.r.top + x.r.height/2 <= mr.bottom + 40
                )
                .sort((a,b)=>a.r.left-b.r.left);

              const dots = [];
              for (const x of raw) {
                const cx = x.r.left + x.r.width/2;
                if (!dots.length || Math.abs((dots[dots.length-1].r.left +
                    dots[dots.length-1].r.width/2) - cx) > 7) dots.push(x);
              }
              if (dots.length < 2) return false;

              let activeIndex = dots.findIndex(x => x.active);
              if (activeIndex < 0) {
                // Active dot is usually slightly larger/darker.
                let best = -1, bestArea = -1;
                dots.forEach((x,i)=>{
                  const a=x.r.width*x.r.height;
                  if (a>bestArea){bestArea=a;best=i;}
                });
                activeIndex = best;
              }

              const nextIndex = Math.min(dots.length - 1, activeIndex + 1);
              if (nextIndex <= activeIndex) return false;
              const target = dots[nextIndex].el.closest('button,[role="button"]') || dots[nextIndex].el;
              try { target.click(); return true; } catch(e) { return false; }
            }
            """
        )
    except Exception:
        clicked = False

    if not clicked:
        return False

    after_key = _wait_for_current_media_key_change(
        page, before_key, target_shortcode, timeout_ms=5000
    )
    if after_key:
        logger.info(f"IG carousel dot fallback advanced: target={target_shortcode}")
        return True
    return False


def _swipe_ig_carousel_left(page, before_key: str, target_shortcode: str) -> bool:
    """Perform a real pointer swipe across the visible main media frame."""
    try:
        box = page.evaluate(
            r"""
            () => {
              const root = document.querySelector('div[role="dialog"]') ||
                           document.querySelector('article');
              if (!root) return null;
              const nodes = Array.from(root.querySelectorAll('img,video'))
                .map(el => {
                  const r=el.getBoundingClientRect(), st=getComputedStyle(el);
                  return {r,area:r.width*r.height,ok:
                    st.display!=='none' && st.visibility!=='hidden' &&
                    parseFloat(st.opacity||'1')>0 && r.width>220 && r.height>180 &&
                    r.right>0 && r.bottom>0 && r.left<innerWidth && r.top<innerHeight};
                })
                .filter(x=>x.ok).sort((a,b)=>b.area-a.area);
              if (!nodes.length) return null;
              const r=nodes[0].r;
              return {left:r.left,top:r.top,width:r.width,height:r.height};
            }
            """
        )
        if not box:
            return False

        y = box["top"] + box["height"] * 0.52
        x1 = box["left"] + box["width"] * 0.78
        x2 = box["left"] + box["width"] * 0.24
        page.mouse.move(x1, y)
        page.mouse.down()
        steps = 12
        for i in range(1, steps + 1):
            x = x1 + (x2 - x1) * i / steps
            page.mouse.move(x, y)
            page.wait_for_timeout(25)
        page.mouse.up()

        after_key = _wait_for_current_media_key_change(
            page, before_key, target_shortcode, timeout_ms=5500
        )
        if after_key:
            logger.info(f"IG carousel swipe fallback advanced: target={target_shortcode}")
            return True
    except Exception:
        pass
    return False




def _advance_ig_carousel_with_arrow_key(
    page,
    before_key: str,
    before_signature: str,
    before_visual_hash: str,
    target_shortcode: str,
) -> bool:
    """Focus the active target-post container and press ArrowRight without scroll."""
    media = _find_main_ig_media_geometry(page)
    if not media:
        return False

    try:
        focused = page.evaluate(
            r"""
            () => {
              const visible = el => {
                const r = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                return st.display !== 'none' &&
                       st.visibility !== 'hidden' &&
                       parseFloat(st.opacity || '1') > 0 &&
                       r.width > 100 && r.height > 100 &&
                       r.right > 0 && r.bottom > 0 &&
                       r.left < innerWidth && r.top < innerHeight;
              };

              const dialogs = Array.from(
                document.querySelectorAll('div[role="dialog"]')
              ).filter(visible);

              let root = dialogs[0] ||
                         Array.from(document.querySelectorAll('article')).find(visible) ||
                         document.body;

              try {
                root.setAttribute('tabindex', '-1');
                root.focus({preventScroll:true});
                return document.activeElement === root;
              } catch (e) {
                return false;
              }
            }
            """
        )

        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(550)
        logger.info(
            f"IG target-dialog ArrowRight fallback: focused={bool(focused)}, "
            f"root={media.get('rootKind') or 'unknown'}, target={target_shortcode}"
        )
    except Exception as exc:
        logger.debug(f"IG target-dialog ArrowRight dispatch failed: {exc}")
        return False

    after_key = _wait_for_current_media_key_change(
        page, before_key, target_shortcode, timeout_ms=3200
    )
    if after_key:
        logger.info(f"IG target-dialog ArrowRight media-key advanced: target={target_shortcode}")
        return True

    if _wait_for_carousel_state_change(
        page, before_signature, target_shortcode, timeout_ms=1800
    ):
        logger.info(f"IG target-dialog ArrowRight state advanced: target={target_shortcode}")
        return True

    if _wait_for_visual_frame_change(
        page, before_visual_hash, target_shortcode, timeout_ms=2200
    ):
        logger.info(f"IG target-dialog ArrowRight visual advanced: target={target_shortcode}")
        return True

    return False



def _click_next_ig_locked_keycheck(page, target_shortcode: str) -> bool:
    """Advance one slide and accept only a verified media/state/visual change."""
    before_key = _get_current_media_key(page)
    before_signature = _get_carousel_state_signature(page)
    before_visual_hash = _get_main_media_visual_fingerprint(page)
    before_url = ""
    try:
        before_url = page.url or ""
    except Exception:
        pass

    actionable_next = _is_actionable_carousel_next(page)

    # First use the spatially locked real carousel control when available.
    # Instagram frequently hides the visible Next control in headless mode,
    # so keyboard/dot/swipe fallbacks must still run when this is False.
    moved = _click_next_ig(page) if actionable_next else False
    if moved:
        if target_shortcode and not _is_target_shortcode_context(page, target_shortcode):
            logger.warning(
                f"IG carousel scope guard: next click left target={target_shortcode}; "
                f"before={before_url}; after={getattr(page, 'url', '')}; "
                f"stop collecting to avoid wrong post"
            )
            return False

        after_key = _wait_for_current_media_key_change(
            page, before_key, target_shortcode, timeout_ms=2600
        )
        if after_key:
            return True

        if _wait_for_carousel_state_change(
            page, before_signature, target_shortcode, timeout_ms=1400
        ):
            logger.info(
                f"IG carousel state changed without media-key change: "
                f"target={target_shortcode}"
            )
            return True

        if _wait_for_visual_frame_change(
            page, before_visual_hash, target_shortcode, timeout_ms=1800
        ):
            logger.info(
                f"IG carousel visual frame changed without media-key change: "
                f"target={target_shortcode}"
            )
            return True

    # Preferred fallback for restricted layouts: focus the actual media and use
    # Instagram's own keyboard carousel handler.
    if _advance_ig_carousel_with_arrow_key(
        page,
        before_key,
        before_signature,
        before_visual_hash,
        target_shortcode,
    ):
        return True

    # Coordinate fallbacks remain, but they now use the corrected main-media
    # geometry and reject 返回/back controls.
    if _click_visible_next_arrow_with_mouse(
        page, before_signature, before_visual_hash, target_shortcode
    ):
        return True

    if _click_next_by_real_mouse(page, before_signature, target_shortcode):
        return True

    if _click_next_carousel_dot(page, before_key, target_shortcode):
        return True

    if _swipe_ig_carousel_left(page, before_key, target_shortcode):
        return True

    return False



def _fill_filtered_from_network_cache(filtered, harvested_media, expected_count: int):
    """Fill only from the *fresh* carousel-walk network cache.

    The caller clears harvested_media immediately before walking the target
    carousel.  Therefore this function must not be used with stale page-load
    cache.  It also preserves already visible/DOM-collected media first and only
    appends fresh network candidates until expected_count is reached.
    """
    if not expected_count or expected_count <= len(filtered):
        return filtered

    out = list(filtered)
    seen = {_media_key_from_url(x.get("src", "")) for x in out}
    appended = 0

    for harvested in sorted(harvested_media.values(), key=lambda x: x.get("score", 0), reverse=True):
        src = harvested.get("src", "")
        if not _looks_like_real_ig_media_url(src):
            continue

        low = src.lower()
        # Extra physical noise guard: do not allow profile pictures, icons, or
        # tiny UI assets to fill carousel slots.
        if any(k in low for k in [
            "profile_pic", "s150x150", "s100x100", "s32x32", "s40x40",
            "s50x50", "s64x64", "emoji", "sprite", "icon", "favicon",
        ]):
            continue

        key = _media_key_from_url(src)
        if key in seen:
            continue

        out.append({
            "src": src,
            "type": harvested.get("type", "image"),
            "score": harvested.get("score", 0),
            "from": "network-fill-scoped",
        })
        seen.add(key)
        appended += 1

        if len(out) >= expected_count:
            break

    if appended:
        logger.info(
            f"IG carousel scoped network fill appended={appended}, "
            f"expected={expected_count}, final={len(out)}"
        )

    return _dedupe_media(out, preserve_order=True)[:expected_count]



def _prime_requested_video_slide(page, target_shortcode: str, slide_number: int) -> bool:
    """Trigger playback only on the explicitly requested carousel slide.

    The normal v11.66 carousel path is unchanged.  This helper runs only when the
    current collected position equals the original shared URL's img_index.  It
    stays inside the active target-post dialog/article and waits so Chrome can
    request both MP4 initialization and media fragments.
    """
    try:
        primed = page.evaluate(
            r"""
            () => {
              const vw = innerWidth || 1400;
              const vh = innerHeight || 1600;

              const visible = el => {
                const r = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                const ox = Math.max(0, Math.min(r.right, vw) - Math.max(r.left, 0));
                const oy = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0));
                return (
                  st.display !== 'none' &&
                  st.visibility !== 'hidden' &&
                  parseFloat(st.opacity || '1') > 0 &&
                  r.width >= 220 &&
                  r.height >= 180 &&
                  ox * oy >= 45000
                );
              };

              const dialogs = Array.from(
                document.querySelectorAll('div[role="dialog"]')
              ).filter(visible);

              let root = null;
              if (dialogs.length) {
                dialogs.sort((a, b) => {
                  const za = parseInt(getComputedStyle(a).zIndex || '0', 10) || 0;
                  const zb = parseInt(getComputedStyle(b).zIndex || '0', 10) || 0;
                  const ra = a.getBoundingClientRect();
                  const rb = b.getBoundingClientRect();
                  return (zb - za) || (rb.width * rb.height - ra.width * ra.height);
                });
                root = dialogs[0];
              } else {
                const articles = Array.from(
                  document.querySelectorAll('article')
                ).filter(visible);

                articles.sort((a, b) => {
                  const ra = a.getBoundingClientRect();
                  const rb = b.getBoundingClientRect();
                  return rb.width * rb.height - ra.width * ra.height;
                });
                root = articles[0] || null;
              }

              if (!root) return false;

              const videos = Array.from(root.querySelectorAll('video'))
                .filter(visible)
                .sort((a, b) => {
                  const ra = a.getBoundingClientRect();
                  const rb = b.getBoundingClientRect();
                  return rb.width * rb.height - ra.width * ra.height;
                });

              if (videos.length) {
                const video = videos[0];
                try {
                  video.muted = true;
                  video.playsInline = true;
                  video.preload = 'auto';
                  video.load();
                } catch (e) {}

                try {
                  const pending = video.play();
                  if (pending && pending.catch) pending.catch(() => {});
                } catch (e) {}
                return true;
              }

              // Some IG builds expose the current video as a poster until the
              // media area is clicked once.  Click only the largest media inside
              // the active post root; never click document-wide content.
              const media = Array.from(root.querySelectorAll('img'))
                .filter(visible)
                .sort((a, b) => {
                  const ra = a.getBoundingClientRect();
                  const rb = b.getBoundingClientRect();
                  return rb.width * rb.height - ra.width * ra.height;
                })[0];

              if (!media) return false;

              const r = media.getBoundingClientRect();
              const x = r.left + r.width / 2;
              const y = r.top + r.height / 2;
              const target = document.elementFromPoint(x, y);
              if (!target) return false;

              try {
                target.dispatchEvent(new MouseEvent('click', {
                  bubbles: true,
                  cancelable: true,
                  clientX: x,
                  clientY: y,
                  view: window
                }));
                return true;
              } catch (e) {
                return false;
              }
            }
            """
        )
    except Exception:
        primed = False

    if primed:
        logger.info(
            f"IG requested video slide primed for init/media capture: "
            f"slide={slide_number}, target={target_shortcode}"
        )
        try:
            page.wait_for_timeout(4200)
        except Exception:
            time.sleep(4.2)

    return bool(primed)



def _collect_visible_target_media(page, target_shortcode: str, include_meta: bool = True):
    """Walk a target post carousel until the real Next control can no longer advance.

    v11.50 rules:
    - dot/indicator count is advisory only; it is never a hard stop,
    - rewind to the first slide,
    - collect one visible full-frame item per successful media-key change,
    - stop only when Next is gone/disabled or cannot change the active media,
    - remain locked to the requested shortcode on every step.

    The dynamic walk is activated only for confirmed carousel posts.  Normal
    single-image posts and Reels keep their existing one-item path.
    """
    global _LAST_CAROUSEL_EXPECTED_COUNT, _LAST_CAROUSEL_TARGET, _LAST_CAROUSEL_WALK_COMPLETE
    _LAST_CAROUSEL_EXPECTED_COUNT = 0
    _LAST_CAROUSEL_TARGET = target_shortcode or ""
    _LAST_CAROUSEL_WALK_COMPLETE = True

    if not _is_target_shortcode_context(page, target_shortcode):
        try:
            logger.warning(
                f"IG shortcode scope guard: page left target shortcode={target_shortcode}; "
                f"current_url={page.url}，停止掃描帳號頁/推薦貼文"
            )
        except Exception:
            pass
        return []

    detected_hint = _get_carousel_total_count(page)
    requested_img_index = _get_requested_img_index(getattr(page, "url", ""))
    has_next_initially = _is_actionable_carousel_next(page)
    carousel_mode = bool(detected_hint >= 2 or requested_img_index > 1 or has_next_initially)

    if carousel_mode:
        expected_floor = max(2, requested_img_index, detected_hint or 0)
        logger.info(
            f"IG dynamic carousel walk start: hint={detected_hint or 0}, "
            f"expected_floor={expected_floor}, requested_img_index={requested_img_index}, "
            f"visible_next={has_next_initially}, target={target_shortcode}"
        )
        # Do not limit rewind by the unreliable dots count.  A shared URL may open
        # at any img_index, and sponsored layouts can reveal only two indicators.
        rewind_steps = _rewind_carousel_to_first(page, target_shortcode, max_steps=_MAX_CAROUSEL_ITEMS)
        # First-slide lock: after rewind, wait for the first frame to settle before
        # any Next click.  This prevents the shared img_index slide from becoming
        # item 1 and dropping the real first slide (total count -1).
        try:
            page.wait_for_timeout(900 if rewind_steps else 450)
        except Exception:
            time.sleep(0.9 if rewind_steps else 0.45)
        first_key = _get_current_media_key(page)
        prev_still_actionable = _is_actionable_carousel_previous(page)
        logger.info(
            f"IG carousel first-slide lock: rewind_steps={rewind_steps}, "
            f"first_key={'ready' if first_key else 'missing'}, "
            f"previous_actionable={prev_still_actionable}, target={target_shortcode}"
        )

        # A first-slide lock is valid only when no usable Previous control remains.
        # If IG still exposes Previous, starting collection here would silently
        # omit slide 1 and return a false SUCCESS with total_count - 1.
        if prev_still_actionable:
            logger.warning(
                f"IG carousel first-slide lock failed: Previous remains actionable after rewind; "
                f"requested_img_index={requested_img_index}, target={target_shortcode}; refuse false SUCCESS"
            )
            _LAST_CAROUSEL_WALK_COMPLETE = False
            return []
    else:
        expected_floor = 1

    collected = []
    seen_media_keys = set()
    ended_at_real_end = not carousel_mode
    stalled_with_next = False

    max_rounds = _MAX_CAROUSEL_ITEMS if carousel_mode else 1
    for round_index in range(max_rounds):
        if not _is_target_shortcode_context(page, target_shortcode):
            try:
                logger.warning(
                    f"IG shortcode scope guard: before collect round={round_index}, "
                    f"page left target={target_shortcode}; current_url={page.url}; stop before wrong post"
                )
            except Exception:
                pass
            _LAST_CAROUSEL_WALK_COMPLETE = False
            break

        requested_slide = int(
            getattr(_DOWNLOAD_CONTEXT, "requested_img_index", 1) or 1
        )
        current_slide_number = len(collected) + 1

        if requested_slide > 1 and current_slide_number == requested_slide:
            _prime_requested_video_slide(
                page,
                target_shortcode,
                current_slide_number,
            )

        current = _get_current_slide_main_media(page)
        if not current and include_meta and not collected:
            current = _get_meta_ig_media(page)[:1]
        if not current:
            if carousel_mode and _is_actionable_carousel_next(page):
                stalled_with_next = True
                _LAST_CAROUSEL_WALK_COMPLETE = False
            break

        item = current[0]
        src = item.get("src", "")
        key = _media_key_from_url(src)

        if key and key not in seen_media_keys:
            seen_media_keys.add(key)
            collected.append(item)
            if carousel_mode:
                logger.info(
                    f"IG dynamic carousel walk: slide={len(collected)}, "
                    f"round={round_index + 1}, target={target_shortcode}"
                )

        if not carousel_mode:
            break

        if not _is_actionable_carousel_next(page):
            ended_at_real_end = True
            break

        moved = _click_next_ig_locked_keycheck(page, target_shortcode)
        if not moved:
            # If an enabled Next still exists, the lazy-loaded next slide failed to
            # materialize.  Mark the traversal incomplete so the caller returns
            # RETRY instead of accepting a partial SUCCESS.
            if _is_actionable_carousel_next(page):
                stalled_with_next = True
                _LAST_CAROUSEL_WALK_COMPLETE = False
                logger.warning(
                    f"IG dynamic carousel walk stalled while Next remains actionable: "
                    f"collected={len(collected)}, target={target_shortcode}; refuse partial SUCCESS"
                )
            else:
                ended_at_real_end = True
            break
    else:
        # Reaching the safety ceiling means the true end was not proven.
        if carousel_mode and _is_actionable_carousel_next(page):
            _LAST_CAROUSEL_WALK_COMPLETE = False
            stalled_with_next = True

    true_total = len(collected)
    if carousel_mode:
        # A completed end-walk is the source of truth.  Initial dots remain only a
        # lower-bound hint for incomplete traversals.
        if ended_at_real_end and not stalled_with_next:
            _LAST_CAROUSEL_EXPECTED_COUNT = true_total
            _LAST_CAROUSEL_WALK_COMPLETE = True
            logger.info(
                f"IG dynamic carousel walk complete: true_total={true_total}, "
                f"initial_hint={detected_hint or 0}, target={target_shortcode}"
            )
        else:
            _LAST_CAROUSEL_EXPECTED_COUNT = max(expected_floor, true_total + 1)
            logger.warning(
                f"IG dynamic carousel walk incomplete: collected={true_total}, "
                f"required_at_least={_LAST_CAROUSEL_EXPECTED_COUNT}, "
                f"initial_hint={detected_hint or 0}, target={target_shortcode}"
            )
    else:
        _LAST_CAROUSEL_EXPECTED_COUNT = true_total or 1

    return _dedupe_media(collected, preserve_order=True)

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



_IG_SHORTCODE_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789-_"
)


def _shortcode_to_media_id(shortcode: str) -> str:
    """Decode an Instagram shortcode into its numeric media id."""
    value = 0
    try:
        for char in str(shortcode or "").strip():
            index = _IG_SHORTCODE_ALPHABET.find(char)
            if index < 0:
                return ""
            value = value * 64 + index
        return str(value) if value > 0 else ""
    except Exception:
        return ""


def _capture_ig_structured_response(response, payloads: list) -> None:
    """Capture authenticated IG JSON without depending on visual carousel state."""
    try:
        if response.status < 200 or response.status >= 300:
            return

        url = response.url or ""
        low_url = url.lower()
        headers = response.headers or {}
        content_type = (
            headers.get("content-type", "")
            or headers.get("Content-Type", "")
            or ""
        ).lower()

        interesting = (
            "application/json" in content_type
            or "/graphql/" in low_url
            or "/api/v1/media/" in low_url
            or "polaris" in low_url
        )
        if not interesting:
            return

        body = response.body()
        if not body or len(body) > 30 * 1024 * 1024:
            return

        text = body.decode("utf-8", errors="ignore").strip()
        if not text or text[0] not in "[{":
            return

        payload = json.loads(text)
        payloads.append({
            "url": url,
            "payload": payload,
            "captured_at": time.time(),
        })
    except Exception:
        return



def _build_ig_original_image_url_variants(
    media_url: str,
    declared_width: int = 0,
    declared_height: int = 0,
) -> list[str]:
    """Build conservative same-image CDN variants without resize/crop directives.

    Instagram sometimes returns only responsive preview candidates even though
    the structured node declares a larger original frame.  These variants are
    attempted only after the explicit structured candidates fail quality checks.

    All generated responses still pass the existing real-image, shortcode,
    dimensions, aspect-ratio and file-integrity gates before they can be saved.
    """
    raw = html.unescape(str(media_url or "").strip())
    if not _looks_like_real_ig_media_url(raw):
        return []

    try:
        dw = int(declared_width or 0)
        dh = int(declared_height or 0)
    except Exception:
        dw, dh = 0, 0

    # Do not require original_width/original_height here. Some authenticated
    # Instagram carousel nodes omit those fields even when the CDN ``stp``
    # transformation is serving only a responsive preview. Generated variants
    # are never trusted directly; the existing real-byte dimensions, aspect
    # ratio, image header, shortcode ownership and completeness gates still
    # decide whether the file may be saved.
    try:
        parsed = urlparse(raw)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except Exception:
        return []

    variants = []
    seen = {_normalized_exact_media_url(raw)}

    def emit(new_pairs):
        try:
            candidate = urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    parsed.path,
                    parsed.params,
                    urlencode(new_pairs, doseq=True),
                    parsed.fragment,
                )
            )
        except Exception:
            return

        key = _normalized_exact_media_url(candidate)
        if not key or key in seen:
            return
        seen.add(key)
        variants.append(candidate)

    stp_value = ""
    for key, value in pairs:
        if key.lower() == "stp":
            stp_value = value or ""
            break

    if stp_value:
        # Variant 1: remove only explicit size/crop tokens, retaining format flags.
        cleaned_tokens = []
        for token in stp_value.split("_"):
            low = token.lower()
            if re.fullmatch(r"s\d+x\d+", low):
                continue
            if re.fullmatch(r"c\d+(?:\.\d+){2,3}a?", low):
                continue
            if re.fullmatch(r"p\d+x\d+", low):
                continue
            cleaned_tokens.append(token)

        cleaned_stp = "_".join(t for t in cleaned_tokens if t)
        cleaned_pairs = []
        for key, value in pairs:
            if key.lower() == "stp":
                if cleaned_stp:
                    cleaned_pairs.append((key, cleaned_stp))
            else:
                cleaned_pairs.append((key, value))
        emit(cleaned_pairs)

        # Variant 2: remove the stp transformation entirely.
        emit([(key, value) for key, value in pairs if key.lower() != "stp"])

    return variants

def _all_ig_image_candidates(node: dict) -> list[dict]:
    """Return all structured image candidates ordered by true resolution.

    Pixel area is authoritative. Source family is only a tie-breaker. This
    prevents a small image_versions2 preview from outranking a larger
    display_resource merely because of a fixed source bonus.
    """
    candidates = []
    seen = set()

    def add(url, width=0, height=0, source_rank=0):
        clean_url = html.unescape(unquote(str(url or "").strip()))
        if not _looks_like_real_ig_media_url(clean_url):
            return
        if any(ext in clean_url.lower() for ext in [".mp4", ".m4v", ".mov"]):
            return
        # Still-image resolution variants frequently share the same CDN
        # host/path and differ only in query parameters such as ``stp``.
        # Keep the full normalized URL here; otherwise high-resolution
        # candidates are discarded before the download retry loop can see them.
        key = _normalized_exact_media_url(clean_url)
        if key in seen:
            return
        seen.add(key)
        try:
            w = max(0, int(width or 0))
            h = max(0, int(height or 0))
        except Exception:
            w, h = 0, 0
        candidates.append({
            "url": clean_url,
            "width": w,
            "height": h,
            "area": w * h,
            "source_rank": int(source_rank or 0),
        })

    for item in ((node.get("image_versions2") or {}).get("candidates") or []):
        if isinstance(item, dict):
            add(item.get("url"), item.get("width"), item.get("height"), 4)

    for item in (node.get("display_resources") or []):
        if isinstance(item, dict):
            add(
                item.get("src") or item.get("url"),
                item.get("config_width") or item.get("width"),
                item.get("config_height") or item.get("height"),
                3,
            )

    for item in (node.get("thumbnail_resources") or []):
        if isinstance(item, dict):
            add(
                item.get("src") or item.get("url"),
                item.get("config_width") or item.get("width"),
                item.get("config_height") or item.get("height"),
                1,
            )

    for key in ["display_url", "image_url", "thumbnail_url", "src", "url"]:
        add(node.get(key), node.get("width"), node.get("height"), 2)

    candidates.sort(
        key=lambda row: (
            row.get("area", 0),
            row.get("source_rank", 0),
            _media_quality_score(row.get("url", "")),
        ),
        reverse=True,
    )
    return candidates


def _best_ig_image_candidate(node: dict) -> str:
    candidates = _all_ig_image_candidates(node)
    return candidates[0]["url"] if candidates else ""


def _best_ig_video_candidate(node: dict) -> str:
    candidates = []

    def add(url, width=0, height=0, bitrate=0, bonus=0):
        url = html.unescape(unquote(str(url or "").strip()))
        if not url:
            return
        low = url.lower()
        if not (
            ".mp4" in low or ".m4v" in low or ".mov" in low
            or "cdninstagram.com" in low or "fbcdn.net" in low
        ):
            return
        try:
            score = (
                int(width or 0) * int(height or 0)
                + int(bitrate or 0)
                + int(bonus or 0)
            )
        except Exception:
            score = int(bonus or 0)
        candidates.append((score, url))

    for item in (node.get("video_versions") or []):
        if isinstance(item, dict):
            add(
                item.get("url"),
                item.get("width"),
                item.get("height"),
                item.get("bitrate"),
                5000000,
            )

    for item in (node.get("video_resources") or []):
        if isinstance(item, dict):
            add(
                item.get("src") or item.get("url"),
                item.get("config_width") or item.get("width"),
                item.get("config_height") or item.get("height"),
                item.get("bitrate"),
                4000000,
            )

    for key in [
        "video_url", "video_src", "playback_url",
        "dash_manifest", "src", "url",
    ]:
        value = node.get(key)
        if isinstance(value, str) and not value.lstrip().startswith("<"):
            add(value, node.get("width"), node.get("height"))

    candidates.sort(key=lambda row: row[0], reverse=True)
    return candidates[0][1] if candidates else ""


def _ig_node_shortcode(node: dict) -> str:
    for key in ["shortcode", "code"]:
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    media = node.get("media")
    if isinstance(media, dict):
        return _ig_node_shortcode(media)
    return ""


def _ig_node_children(node: dict):
    edge = node.get("edge_sidecar_to_children")
    if isinstance(edge, dict):
        edges = edge.get("edges") or []
        children = [
            item.get("node")
            for item in edges
            if isinstance(item, dict) and isinstance(item.get("node"), dict)
        ]
        if children:
            return children

    for key in ["carousel_media", "children", "carousel_children"]:
        value = node.get(key)
        if isinstance(value, list):
            children = []
            for item in value:
                if isinstance(item, dict):
                    child = item.get("node") if isinstance(item.get("node"), dict) else item
                    children.append(child)
            if children:
                return children

    return []


def _ig_structured_node_to_media(node: dict, slide_index: int):
    if not isinstance(node, dict):
        return None

    media_type_value = node.get("media_type")
    product_type = str(node.get("product_type") or "").lower()
    typename = str(node.get("__typename") or "").lower()

    is_video = bool(
        node.get("is_video") is True
        or media_type_value in {2, "2", "video"}
        or "video" in typename
        or product_type in {"clips", "video"}
        or node.get("video_versions")
        or node.get("video_resources")
        or node.get("video_url")
    )

    if is_video:
        url = _best_ig_video_candidate(node)
        if not url:
            return None
        return {
            "src": url,
            "type": "video",
            "score": 100000000 + _media_quality_score(url),
            "_carousel_slide_index": slide_index,
            "from": "authenticated-structured-json",
        }

    image_candidates = _all_ig_image_candidates(node)
    if not image_candidates:
        return None

    primary = image_candidates[0]
    url = primary.get("url", "")
    width = node.get("original_width") or node.get("width") or primary.get("width") or 0
    height = node.get("original_height") or node.get("height") or primary.get("height") or 0
    try:
        frame_ratio = float(width) / float(height) if float(height) > 0 else 0
    except Exception:
        frame_ratio = 0

    # Keep only explicit candidates returned for this exact structured child.
    # Removing/rewriting signed CDN transformation parameters produces 403 and
    # must not be applied globally to unrelated hydration nodes.
    alternate_urls = [
        row.get("url", "") for row in image_candidates[1:] if row.get("url")
    ]

    explicit_max_width = max(
        [int(row.get("width") or 0) for row in image_candidates] or [0]
    )
    explicit_max_height = max(
        [int(row.get("height") or 0) for row in image_candidates] or [0]
    )

    primary_url_low = str(url or "").lower()
    has_explicit_small_resize = bool(
        re.search(r"(?:^|[_?&=])s(?:240|320|360|480|540|640)x\d+", primary_url_low)
    )

    best_available_structured = bool(
        primary.get("width")
        and primary.get("height")
        and max(int(primary.get("width") or 0), int(primary.get("height") or 0)) >= 640
        and min(int(primary.get("width") or 0), int(primary.get("height") or 0)) >= 480
        and not has_explicit_small_resize
    )

    deduped_alternates = []
    seen_alternates = {_normalized_exact_media_url(url)}
    for candidate_url in alternate_urls:
        candidate_url = str(candidate_url or "").strip()
        key = _normalized_exact_media_url(candidate_url)
        if not key or key in seen_alternates:
            continue
        seen_alternates.add(key)
        deduped_alternates.append(candidate_url)

    return {
        "src": url,
        "type": "image",
        "score": 50000000 + _media_quality_score(url),
        "_carousel_slide_index": slide_index,
        "frameRatio": frame_ratio,
        "sourceWidth": int(primary.get("width") or width or 0),
        "sourceHeight": int(primary.get("height") or height or 0),
        "_explicitMaxWidth": explicit_max_width,
        "_explicitMaxHeight": explicit_max_height,
        "_best_available_structured_image": best_available_structured,
        "_alternate_image_urls": deduped_alternates,
        "from": "authenticated-structured-json",
    }


def _extract_structured_media_from_candidate(node: dict, shortcode: str):
    if not isinstance(node, dict):
        return []

    node_shortcode = _ig_node_shortcode(node)
    if node_shortcode and shortcode and node_shortcode != shortcode:
        return []

    children = _ig_node_children(node)
    source_nodes = children or [node]

    items = []
    for index, child in enumerate(source_nodes, 1):
        media = _ig_structured_node_to_media(child, index)
        if not media:
            return []
        items.append(media)

    return _dedupe_media(items, preserve_order=True)


def _find_structured_media_in_payload(payload, shortcode: str):
    """Find and convert only exact-shortcode structured nodes.

    Older logic attempted media conversion on every dictionary whose shortcode
    was absent. Large Instagram hydration payloads contain hundreds of unrelated
    recommendation/avatar/image nodes, causing candidate explosions and noisy
    logs. This implementation traverses broadly but converts narrowly.
    """
    best = []
    best_score = -1
    stack = [payload]
    visited = 0

    while stack and visited < 250000:
        current = stack.pop()
        visited += 1

        if isinstance(current, dict):
            current_shortcode = _ig_node_shortcode(current)

            if current_shortcode == shortcode:
                direct = _extract_structured_media_from_candidate(
                    current,
                    shortcode,
                )
                if direct:
                    children = _ig_node_children(current)
                    score = (
                        (10000 if children else 0)
                        + len(direct)
                    )
                    if score > best_score:
                        best_score = score
                        best = direct

            for value in current.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)

        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    stack.append(value)

    return best


def _extract_json_scripts_from_page(page):
    payloads = []
    try:
        texts = page.evaluate(
            r"""
            () => Array.from(document.querySelectorAll(
              'script[type="application/json"],script[data-sjs]'
            )).map(s => s.textContent || '').filter(Boolean)
            """
        ) or []
    except Exception:
        texts = []

    for text in texts:
        try:
            text = str(text or "").strip()
            if text and text[0] in "[{" and len(text) <= 30 * 1024 * 1024:
                payloads.append(json.loads(text))
        except Exception:
            continue
    return payloads


def _fetch_authenticated_media_info(page, shortcode: str):
    """Use the logged-in page session to query the post info endpoint."""
    media_id = _shortcode_to_media_id(shortcode)
    if not media_id:
        return None

    try:
        result = page.evaluate(
            r"""
            async ({mediaId}) => {
              const urls = [
                `/api/v1/media/${mediaId}/info/`,
                `/api/v1/media/${mediaId}/info/?can_support_threading=true`
              ];

              for (const url of urls) {
                try {
                  const response = await fetch(url, {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                      'Accept': 'application/json',
                      'X-IG-App-ID': '936619743392459',
                      'X-Requested-With': 'XMLHttpRequest'
                    }
                  });
                  if (!response.ok) continue;
                  const data = await response.json();
                  if (data) return data;
                } catch (e) {}
              }
              return null;
            }
            """,
            {"mediaId": media_id},
        )
        return result if isinstance(result, (dict, list)) else None
    except Exception:
        return None



def _structured_caption_text(node: dict) -> str:
    """Extract the real post caption from common IG API/GraphQL shapes."""
    if not isinstance(node, dict):
        return ""

    caption = node.get("caption")
    if isinstance(caption, dict):
        for key in ["text", "caption_text"]:
            value = caption.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    elif isinstance(caption, str) and caption.strip():
        return caption.strip()

    edge = node.get("edge_media_to_caption")
    if isinstance(edge, dict):
        for entry in edge.get("edges") or []:
            if not isinstance(entry, dict):
                continue
            child = entry.get("node")
            if isinstance(child, dict):
                value = child.get("text")
                if isinstance(value, str) and value.strip():
                    return value.strip()

    for key in [
        "caption_text",
        "description",
        "text",
    ]:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _structured_account_name(node: dict) -> str:
    """Extract the post owner username from common IG response shapes."""
    if not isinstance(node, dict):
        return ""

    for key in ["user", "owner"]:
        value = node.get(key)
        if isinstance(value, dict):
            for name_key in ["username", "user_name"]:
                account = _clean_ig_account(value.get(name_key))
                if account:
                    return account

    for key in ["username", "owner_username"]:
        account = _clean_ig_account(node.get(key))
        if account:
            return account

    return ""


def _find_structured_metadata_in_payload(payload, shortcode: str) -> tuple[str, str]:
    """Find caption/account only from the exact target-shortcode node.

    This intentionally mirrors instagram_git_ok.py naming behavior:
    real post caption first; if the post has no caption, caller falls back to
    account and then shortcode. Generic Instagram page-shell text is ignored.
    """
    exact_title = ""
    exact_account = ""
    stack = [payload]
    visited = 0

    while stack and visited < 250000:
        current = stack.pop()
        visited += 1

        if isinstance(current, dict):
            current_shortcode = _ig_node_shortcode(current)

            if current_shortcode == shortcode:
                title_raw = _structured_caption_text(current)
                clean_title = _clean_ig_caption_candidate(
                    title_raw,
                    shortcode,
                )
                if (
                    clean_title
                    and not _is_bad_ig_caption_candidate(
                        clean_title,
                        shortcode,
                    )
                ):
                    exact_title = clean_title

                account = _structured_account_name(current)
                if account:
                    exact_account = account

                # The exact post node is authoritative. Do not continue looking
                # for unrelated app-shell text elsewhere in the payload.
                if exact_title or exact_account:
                    return exact_title, exact_account

            for value in current.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)

        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    stack.append(value)

    return "", ""



def _collect_authenticated_structured_media(
    page,
    captured_payloads: list,
    shortcode: str,
):
    """Return the complete ordered post media without clicking carousel arrows."""
    try:
        _DOWNLOAD_CONTEXT._structured_best_score = -1
        _DOWNLOAD_CONTEXT.structured_title = ""
        _DOWNLOAD_CONTEXT.structured_account = ""
    except Exception:
        pass

    sources = []

    for record in captured_payloads or []:
        if isinstance(record, dict) and "payload" in record:
            sources.append(record.get("payload"))

    sources.extend(_extract_json_scripts_from_page(page))

    api_payload = _fetch_authenticated_media_info(page, shortcode)
    if api_payload is not None:
        sources.insert(0, api_payload)

    best = []
    structured_title = ""
    structured_account = ""

    for payload in sources:
        items = _find_structured_media_in_payload(payload, shortcode)
        if len(items) > len(best):
            best = items

        title_candidate, account_candidate = (
            _find_structured_metadata_in_payload(payload, shortcode)
        )
        if title_candidate and not structured_title:
            structured_title = title_candidate
        if account_candidate and not structured_account:
            structured_account = account_candidate

    try:
        _DOWNLOAD_CONTEXT.structured_title = structured_title
        _DOWNLOAD_CONTEXT.structured_account = structured_account
    except Exception:
        pass

    if structured_title:
        logger.info(
            f"IG structured caption resolved: {structured_title}"
        )
    if structured_account:
        logger.info(
            f"IG structured account resolved: {structured_account}"
        )

    if not best:
        return []

    # v11.90: structured JSON extraction is already selected from the exact
    # target-shortcode node, but the converted child media did not carry the
    # ownership marker required by the final hard gate.  Stamp it only here,
    # after exact structured selection and duplicate validation preparation, so
    # downstream download validation can prove every item belongs to this task.
    # This does not relax cross-post protection; it restores the ownership
    # metadata lost while converting carousel child nodes.
    for item in best:
        item["_target_shortcode"] = shortcode

    keys = [_media_key_from_url(item.get("src", "")) for item in best]
    if not all(keys) or len(set(keys)) != len(keys):
        logger.warning(
            f"IG authenticated structured extraction rejected duplicate/empty media: "
            f"count={len(best)}, target={shortcode}"
        )
        return []

    logger.info(
        f"IG authenticated structured extraction complete: "
        f"count={len(best)}, "
        f"types={[item.get('type') for item in best]}, "
        f"target={shortcode}; carousel flipping skipped"
    )
    return best



def _collect_ig_media_playwright_persistent_impl(
    p,
    url: str,
    reason: str = "",
    use_fresh_tab: bool = True,
    headless_mode: bool = False,
):
    """Fallback using the project-local logged-in Chrome profile.

    This version is strictly shortcode-scoped.  It never scans or finalizes media
    after IG redirects the visible page to a profile/account grid.
    """
    # Record that this task entered the authenticated browser-profile path.
    # A later Playwright/carousel exception must not fall through to anonymous
    # yt-dlp, which cannot see this profile's age/audience trust state.
    try:
        _DOWNLOAD_CONTEXT.persistent_profile_attempted = True
    except Exception:
        pass

    enabled = _try_get_config_value("IG_PERSISTENT_PROFILE_ENABLED", True)
    if str(enabled).lower() in {"0", "false", "no", "off"}:
        return "RETRY", "IG persistent Chrome profile fallback disabled"

    user_data_dir = _resolve_chrome_user_data_dir()
    profile_dir = _resolve_chrome_profile_directory()
    shortcode = _extract_shortcode(url) or ""

    try:
        _DOWNLOAD_CONTEXT.requested_img_index = _get_requested_img_index(url)
    except Exception:
        _DOWNLOAD_CONTEXT.requested_img_index = 1

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
            headless=headless_mode,
            viewport={"width": 1400, "height": 1600},
            locale="zh-TW",
            args=[
                f"--profile-directory={profile_dir}",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
                "--window-size=1400,1600",
                "--window-position=20,20",
            ],
        )

        try:
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        except Exception:
            pass

        harvested_media = {}
        structured_payloads = []
        if use_fresh_tab:
            # v8 strategy: fresh tab prevents long-carousel stale DOM/profile pollution.
            page = _get_fresh_persistent_target_page(context)
        else:
            # v7 strategy: reuse cleaned persistent page; this is safer for short
            # carousel posts whose first slide can be polluted by Chrome restored
            # tabs when opening a brand-new page too early.
            page = _get_persistent_context_page(context)
        def _on_ig_response(response):
            _capture_playwright_response(response, harvested_media)
            _capture_ig_structured_response(response, structured_payloads)

        page.on("response", _on_ig_response)

        try:
            page.bring_to_front()
        except Exception:
            pass

        try:
            _goto_instagram_target_clean(page, url, target_shortcode=shortcode, timeout=60000)
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
            if headless_mode:
                return "BLOCKED", "IG_VISIBLE_PROFILE_REQUIRED"

            _manual_wait_persistent_profile(
                page,
                reason="age/audience/login page before harvest",
            )

            if _is_missing_ig_page(page):
                return "MISSING", "Instagram 顯示：很抱歉，此頁面無法使用；連結可能故障或頁面已遭移除"
            if not _is_target_shortcode_context(page, shortcode):
                return "FAILED", (
                    f"確認後頁面離開目標貼文 {shortcode}；已停止下載。"
                )

        # Some logged-in profiles no longer show the restriction text after the
        # first render but still keep a consent overlay intercepting carousel clicks.
        _try_confirm_ig_restricted_content(page)

        # Primary authenticated path:
        # Extract the complete ordered post structure and its caption/account from
        # the logged-in browser session. No visual carousel clicking is used.
        filtered = _collect_authenticated_structured_media(
            page,
            structured_payloads,
            shortcode,
        )

        if not filtered:
            logger.warning(
                f"IG authenticated structured extraction returned 0: "
                f"target={shortcode}; visual carousel flipping is disabled"
            )
            return "RETRY", (
                "IG authenticated browser opened the post, but the complete "
                "structured media list was not available. Carousel flipping is "
                "disabled to prevent wrong-post or wrong-slide downloads."
            )

        structured_title = getattr(
            _DOWNLOAD_CONTEXT,
            "structured_title",
            "",
        ) or ""
        structured_account = getattr(
            _DOWNLOAD_CONTEXT,
            "structured_account",
            "",
        ) or ""

        prefetched_title = _get_prefetched_title(url)
        if _is_bad_ig_caption_candidate(prefetched_title, shortcode):
            prefetched_title = ""
            _cache_prefetched_title(url, "")

        dom_title = _get_ig_full_caption_title(
            page,
            fallback_shortcode=shortcode or "Instagram_Post",
        )
        if _is_bad_ig_caption_candidate(dom_title, shortcode):
            dom_title = ""

        account = (
            structured_account
            or _get_prefetched_account(url)
            or _extract_post_account_hint_from_url(url)
            or _get_ig_post_account(page)
        )

        title = structured_title or prefetched_title or dom_title
        cleaned_title = _clean_ig_caption_candidate(title, shortcode)

        if (
            cleaned_title
            and not _is_bad_ig_caption_candidate(
                cleaned_title,
                shortcode,
            )
        ):
            title = cleaned_title
        else:
            title = account or shortcode or "Instagram_Post"
            logger.info(
                f"IG caption unavailable; use safe folder fallback: {title}"
            )

        if structured_title or prefetched_title or dom_title:
            _cache_prefetched_title(url, title)
        else:
            _cache_prefetched_title(url, "")
        _cache_prefetched_account(url, account)
        _publish_ig_task_title(url, title)
        _publish_ig_task_account(url, account)

        expected_count = len(filtered)
        logger.info(
            f"IG persistent profile structured media count={expected_count}; "
            f"network harvest={len(harvested_media)}, target={shortcode}"
        )

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
        logger.exception(
            f"IG persistent profile unexpected exception: "
            f"target={shortcode}, error={msg}"
        )
        if "user data directory is already in use" in msg.lower() or "process singleton" in msg.lower():
            return "RETRY", (
                "IG_Parser Chrome Profile 目前被同一個專用 Chrome 視窗佔用。"
                "請關閉 IG_Parser 視窗後重試；日常 Chrome 不需要關閉。"
            )

        classified_status, classified_error = _classify_error(msg)
        if classified_status == "MISSING":
            return classified_status, classified_error

        # Once the authenticated persistent profile was required, unexpected
        # browser/carousel failures remain retryable.  Do not downgrade them to
        # FAILED and then invoke anonymous yt-dlp.
        return "RETRY", (
            f"IG authenticated persistent-profile flow failed: "
            f"{classified_error or msg}"
        )

    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass


def _should_use_v7_clean_persistent_page_for_url(url: str) -> bool:
    """Choose the known-good persistent-page path before opening Chrome.

    Regression guard from A/B testing:
    - v7 clean persistent page downloads IG post URLs containing img_index=...
      correctly, especially short restricted carousel posts.
    - v8 fresh tab downloads long restricted carousel posts correctly.

    The previous v10 detected the short-carousel count *after* opening a fresh
    tab, then switched to v7.  That was too late for some restricted posts
    because the fresh tab could already expose restored/recommended media and
    pollute the first collected slide.  This pre-routing keeps the already-fixed
    v7/v8 behavior without touching carousel collection, ordering, WEBP/JPEG
    conversion, network fill, or move_files().
    """
    low = (url or "").lower()
    return "img_index=" in low


def _collect_ig_media_playwright_persistent(
    p,
    url: str,
    reason: str = "",
    headless_mode: bool = False,
):
    use_v7_clean = (
        _should_use_v7_clean_persistent_page_for_url(url)
        and not headless_mode
    )

    return _collect_ig_media_playwright_persistent_impl(
        p,
        url,
        reason=reason,
        use_fresh_tab=True if headless_mode else not use_v7_clean,
        headless_mode=headless_mode,
    )



def _detect_target_carousel_count(page) -> int:
    """Detect the number of slides in the active target Post.

    Instagram commonly exposes either "1 / 4" text, localized aria-labels, or
    one small indicator dot per slide.  Search only inside the active article/
    dialog so comments and recommended posts cannot affect the result.
    """
    try:
        count = page.evaluate(
            r"""
            () => {
              const root =
                document.querySelector('div[role="dialog"] article') ||
                document.querySelector('div[role="dialog"]') ||
                document.querySelector('article');

              if (!root) return 1;

              let best = 1;
              const nodes = Array.from(
                root.querySelectorAll('[aria-label],[title],span,div,button')
              );

              for (const el of nodes) {
                const text = [
                  el.getAttribute && el.getAttribute('aria-label'),
                  el.getAttribute && el.getAttribute('title'),
                  el.textContent
                ].filter(Boolean).join(' ').trim();

                const patterns = [
                  /(?:image|photo|video|slide)\s*(\d+)\s*(?:of|\/)\s*(\d+)/i,
                  /(?:第\s*)?(\d+)\s*(?:張|則|個)?\s*[\/／]\s*(\d+)/,
                  /\b(\d+)\s*[\/／]\s*(\d+)\b/
                ];

                for (const pattern of patterns) {
                  const match = text.match(pattern);
                  if (!match) continue;
                  const total = parseInt(match[2], 10) || 0;
                  if (total > best && total <= 40) best = total;
                }
              }

              // Carousel indicator dots are generally tiny, horizontally aligned,
              // and located near the lower half of the media pane.
              const rr = root.getBoundingClientRect();
              const candidates = Array.from(root.querySelectorAll(
                'div,span,button,[role="button"]'
              )).filter(el => {
                const r = el.getBoundingClientRect();
                const st = getComputedStyle(el);
                if (
                  st.display === 'none' ||
                  st.visibility === 'hidden' ||
                  parseFloat(st.opacity || '1') <= 0
                ) return false;

                return (
                  r.width >= 3 && r.width <= 16 &&
                  r.height >= 3 && r.height <= 16 &&
                  r.left >= rr.left &&
                  r.right <= rr.right &&
                  r.top >= rr.top + rr.height * 0.55 &&
                  r.bottom <= rr.bottom
                );
              });

              const rows = new Map();
              for (const el of candidates) {
                const r = el.getBoundingClientRect();
                const cy = Math.round((r.top + r.height / 2) / 4) * 4;
                const cx = Math.round((r.left + r.width / 2) / 4) * 4;
                if (!rows.has(cy)) rows.set(cy, []);
                rows.get(cy).push(cx);
              }

              for (const xs of rows.values()) {
                const unique = [];
                for (const x of xs.sort((a,b) => a-b)) {
                  if (!unique.some(v => Math.abs(v - x) <= 5)) unique.push(x);
                }
                if (unique.length >= 2 && unique.length <= 40) {
                  best = Math.max(best, unique.length);
                }
              }

              return best;
            }
            """
        )
        return max(1, min(_MAX_CAROUSEL_ITEMS, int(count or 1)))
    except Exception:
        return 1


def _load_exact_post_slide(
    context,
    shortcode: str,
    slide_index: int,
    harvested_media: dict,
):
    """Load exactly one carousel index in a fresh hidden page."""
    page = context.new_page()
    page.on(
        "response",
        lambda response: _capture_playwright_response(
            response,
            harvested_media,
        ),
    )

    target_url = f"https://www.instagram.com/p/{shortcode}/"
    if slide_index > 1:
        target_url += f"?img_index={slide_index}"

    try:
        page.goto(
            target_url,
            wait_until="domcontentloaded",
            timeout=45000,
        )
    except PlaywrightTimeoutError:
        logger.warning(
            f"IG direct-index goto timeout: "
            f"target={shortcode}, index={slide_index}"
        )

    page.wait_for_timeout(2800)

    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass

    _warmup_ig_page_for_media(page, is_reel=False)

    if not _is_target_shortcode_context(page, shortcode):
        try:
            page.close()
        except Exception:
            pass
        return None

    current = _get_current_slide_main_media(page)
    if not current:
        current = _get_meta_ig_media(page)[:1]

    item = dict(current[0]) if current else None
    if item:
        item["_carousel_slide_index"] = slide_index
        item["_target_shortcode"] = shortcode
        item["from"] = item.get("from") or "direct-img-index"

    try:
        page.close()
    except Exception:
        pass

    return item


def _collect_complete_post_carousel(
    context,
    page,
    shortcode: str,
    harvested_media: dict,
    original_url: str = "",
):
    """Collect every slide and fail closed on any completeness uncertainty."""
    detected = _detect_target_carousel_count(page)
    requested_index = _get_requested_img_index(original_url or "")
    minimum_expected = max(1, detected, requested_index)

    if not _is_target_shortcode_context(page, shortcode):
        logger.warning(
            f"IG carousel start context mismatch: "
            f"target={shortcode}, current={page.url}"
        )
        return []

    # Always begin from the first slide, regardless of a shared img_index URL.
    _rewind_carousel_to_first(
        page,
        shortcode,
        max_steps=_MAX_CAROUSEL_ITEMS,
    )

    collected = []
    seen = set()

    first = _get_current_slide_main_media(page)
    if not first:
        first = _get_meta_ig_media(page)[:1]

    if not first:
        logger.warning(
            f"IG carousel first media unavailable: target={shortcode}"
        )
        return []

    first_item = dict(first[0])
    first_item["_carousel_slide_index"] = 1
    first_item["_target_shortcode"] = shortcode
    first_item["from"] = first_item.get("from") or "verified-carousel-walk"

    first_key = _media_key_from_url(first_item.get("src", ""))
    if not first_key:
        return []

    seen.add(first_key)
    collected.append(first_item)

    # Do not trust only the visible arrow. Probe advancement through all verified
    # methods; the keycheck function now runs keyboard/dot/swipe even when IG
    # hides the Next button.
    first_probe_attempted = False
    first_probe_moved = False

    for slide_index in range(2, _MAX_CAROUSEL_ITEMS + 1):
        first_probe_attempted = True
        moved = _click_next_ig_locked_keycheck(page, shortcode)

        if slide_index == 2:
            first_probe_moved = bool(moved)

        if not moved:
            break

        if not _is_target_shortcode_context(page, shortcode):
            logger.warning(
                f"IG carousel walk left target post: "
                f"target={shortcode}, current={page.url}"
            )
            return []

        current = _get_current_slide_main_media(page)
        if not current:
            logger.warning(
                f"IG carousel advanced but media unresolved: "
                f"index={slide_index}, target={shortcode}"
            )
            return []

        item = dict(current[0])
        item["_carousel_slide_index"] = slide_index
        item["_target_shortcode"] = shortcode
        item["from"] = item.get("from") or "verified-carousel-walk"

        key = _media_key_from_url(item.get("src", ""))
        if not key:
            return []

        if key in seen:
            logger.info(
                f"IG carousel end reached by duplicate: "
                f"index={slide_index}, target={shortcode}"
            )
            break

        seen.add(key)
        collected.append(item)

        if detected > 1 and len(collected) >= detected:
            # Still perform no extra click. The detected count is accepted only
            # after exactly that many unique media items were captured.
            break

    initial_next_available = (
        _is_actionable_carousel_next(page)
        or _has_visible_carousel_next(page)
    )

    logger.info(
        f"IG hard-gated carousel result: "
        f"collected={len(collected)}, detected={detected}, "
        f"requested_index={requested_index}, minimum={minimum_expected}, "
        f"first_probe_attempted={first_probe_attempted}, "
        f"first_probe_moved={first_probe_moved}, "
        f"next_visible_after_walk={initial_next_available}, "
        f"target={shortcode}"
    )

    # Shared URLs with img_index=N prove at least N slides exist.
    if len(collected) < minimum_expected:
        logger.warning(
            f"IG incomplete carousel rejected: "
            f"collected={len(collected)}, minimum={minimum_expected}, "
            f"target={shortcode}"
        )
        return []

    # If navigation still reports another slide after collection stopped, the
    # result is incomplete and must never be SUCCESS.
    if initial_next_available and (
        detected <= 1 or len(collected) < detected
    ):
        logger.warning(
            f"IG remaining next control proves incomplete carousel: "
            f"collected={len(collected)}, detected={detected}, "
            f"target={shortcode}"
        )
        return []

    # A Carousel probe that moved confirms multi-slide content. Never permit it
    # to collapse back to one output item.
    if first_probe_moved and len(collected) <= 1:
        logger.warning(
            f"IG one-slide false SUCCESS rejected after verified movement: "
            f"target={shortcode}"
        )
        return []

    for item in collected:
        if item.get("_target_shortcode") != shortcode:
            logger.warning(
                f"IG cross-post media rejected before download: "
                f"target={shortcode}"
            )
            return []

    return _dedupe_media(collected, preserve_order=True)


def _collect_ig_media_playwright(url: str):
    clear_temp()

    browser = None
    context = None

    original_url = url
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
                _goto_instagram_target_clean(
                    page,
                    url,
                    target_shortcode=shortcode,
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
                # Headless/cookie context can falsely show the generic unavailable page
                # for public legacy URLs such as /<username>/p/<shortcode>/.
                # Verify once with the logged-in IG_Parser profile before declaring MISSING.
                status_p, error_p = _collect_ig_media_playwright_persistent(
                    p,
                    url,
                    reason="headless context reported missing; verify with logged-in IG_Parser profile",
                    headless_mode=True,
                )
                if status_p == "SUCCESS":
                    return status_p, error_p
                if status_p == "BLOCKED" and error_p == "IG_VISIBLE_PROFILE_REQUIRED":
                    return _collect_ig_media_playwright_persistent(
                        p,
                        url,
                        reason="manual login/age/audience confirmation required after missing-page verification",
                        headless_mode=False,
                    )
                if status_p in {"RETRY", "BLOCKED"}:
                    return status_p, error_p
                return "MISSING", error_p or "Instagram 顯示貼文不存在或已移除"

            if _is_ig_audience_restricted_page(page):
                status_p, error_p = _collect_ig_media_playwright_persistent(
                    p,
                    url,
                    reason="IG age/audience restricted page in cookies.txt context",
                    headless_mode=True,
                )
                if status_p == "SUCCESS":
                    return status_p, error_p
                if status_p == "BLOCKED" and error_p == "IG_VISIBLE_PROFILE_REQUIRED":
                    return _collect_ig_media_playwright_persistent(
                        p,
                        url,
                        reason="explicit age/audience confirmation required",
                        headless_mode=False,
                    )
                return status_p, error_p

            if _is_generic_ig_page(page):
                status_p, error_p = _collect_ig_media_playwright_persistent(
                    p,
                    url,
                    reason="IG login/challenge page in cookies.txt context",
                    headless_mode=True,
                )
                if status_p == "SUCCESS":
                    return status_p, error_p
                if status_p == "BLOCKED" and error_p == "IG_VISIBLE_PROFILE_REQUIRED":
                    return _collect_ig_media_playwright_persistent(
                        p,
                        url,
                        reason="explicit login/challenge/checkpoint confirmation required",
                        headless_mode=False,
                    )
                return status_p, error_p

            title = _get_prefetched_title(url) or _get_ig_full_caption_title(
                page,
                fallback_shortcode=shortcode or "Instagram_Post",
            )
            account = _get_prefetched_account(url) or _get_ig_post_account(page)
            _cache_prefetched_account(url, account)
            _publish_ig_task_title(url, title)
            _publish_ig_task_account(url, account)

            if _is_ig_post_url(url):
                filtered = _collect_complete_post_carousel(
                    context,
                    page,
                    shortcode,
                    harvested_media,
                    original_url=original_url,
                )
            else:
                collected = []
                current = _get_current_slide_main_media(page)
                if current:
                    collected.extend(current[:1])

                filtered = [
                    item for item in _dedupe_media(collected)
                    if (item.get("type") or "").lower() == "video"
                    or any(
                        ext in (item.get("src") or "").lower()
                        for ext in [".mp4", ".m4v", ".mov"]
                    )
                ][:1]

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

                if _is_ig_reel_url(url):
                    filtered = _dedupe_media(fallback_video)[:1]
                else:
                    # Broad page resources may belong to recommendations or
                    # another post. Never use them for normal Post downloads.
                    fallback_perf = []
                    fallback_html = []
                    filtered = []

            logger.info(
                f"IG filtered media count={len(filtered)}; network harvest={len(harvested_media)}; "
                f"fallback source counts: video_current={len(fallback_video)}, "
                f"performance={len(fallback_perf)}, html={len(fallback_html)}, target={shortcode}"
            )

            if not filtered:
                status_p, error_p = _collect_ig_media_playwright_persistent(
                    p,
                    url,
                    reason="normal headless collection returned 0",
                    headless_mode=True,
                )

                if status_p == "SUCCESS":
                    return status_p, error_p

                if status_p == "BLOCKED" and error_p == "IG_VISIBLE_PROFILE_REQUIRED":
                    return _collect_ig_media_playwright_persistent(
                        p,
                        url,
                        reason="explicit login/age/audience confirmation required",
                        headless_mode=False,
                    )

                return status_p, error_p

            status_write, error_write = _download_filtered_items_from_context(
                context,
                filtered,
                harvested_media,
                title,
                shortcode,
                referer=url,
            )

            # v11.93:
            # A cookies.txt/headless page may expose only a cropped 640x640 preview
            # for an age/audience-restricted post. The media list is non-empty, so
            # older builds skipped the authenticated IG Parser Profile and later
            # let anonymous yt-dlp turn the task into a false BLOCKED result.
            #
            # Keep every existing quality/integrity gate unchanged. When all
            # headless candidates are rejected, retry the exact shortcode through
            # the logged-in persistent profile before deciding the final status.
            if status_write != "SUCCESS":
                quality_or_integrity_rejected = any(
                    marker in str(error_write or "")
                    for marker in [
                        "媒體全部未通過",
                        "媒體完整性檢查失敗",
                        "低解析度縮圖",
                        "解析度",
                        "圖片幾何",
                        "影片/圖片完整性",
                    ]
                )

                if quality_or_integrity_rejected:
                    logger.info(
                        "IG headless 媒體未通過品質/完整性 gate；"
                        "改用已登入 IG Parser Profile 重新抓取，避免匿名 yt-dlp 假 BLOCKED"
                    )
                    status_p, error_p = _collect_ig_media_playwright_persistent(
                        p,
                        url,
                        reason=(
                            "headless media existed but all candidates were rejected "
                            "by quality/integrity validation"
                        ),
                        headless_mode=True,
                    )

                    if status_p == "SUCCESS":
                        return status_p, error_p

                    if status_p == "BLOCKED" and error_p == "IG_VISIBLE_PROFILE_REQUIRED":
                        return _collect_ig_media_playwright_persistent(
                            p,
                            url,
                            reason=(
                                "visible login/age/audience confirmation required "
                                "after headless quality rejection"
                            ),
                            headless_mode=False,
                        )

                    return status_p, error_p

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


def get_login_status() -> tuple[bool, str]:
    """Check the dedicated IG Parser Profile without opening a visible browser."""
    user_data_dir = _resolve_chrome_user_data_dir()
    profile_dir = _resolve_chrome_profile_directory()

    if not user_data_dir or not os.path.exists(user_data_dir):
        return False, "未建立 Profile"

    context = None
    try:
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="chrome",
                headless=True,
                args=[
                    f"--profile-directory={profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--window-position=-32000,-32000",
                ],
            )
            cookies = context.cookies()
            names = {
                str(cookie.get("name") or "")
                for cookie in cookies
                if "instagram.com" in str(cookie.get("domain") or "").lower()
            }
            if {"sessionid", "ds_user_id"}.issubset(names):
                return True, "已登入"
            return False, "未登入"
    except Exception as e:
        text = _clean_error_text(str(e))
        if "user data directory is already in use" in text.lower():
            return False, "Profile 使用中"
        return False, "狀態無法確認"
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass


def download(url: str):
    if _L is None:
        setup()

    _set_current_profile_output_owner(_get_profile_owner_for_url(url))
    try:
        _DOWNLOAD_CONTEXT.persistent_profile_attempted = False
    except Exception:
        pass

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

            # If the logged-in persistent Chrome profile was already required,
            # do not fall through to yt-dlp.  yt-dlp runs in a separate context
            # and does not inherit the profile's age/audience confirmation state.
            persistent_attempted = False
            try:
                persistent_attempted = bool(
                    getattr(_DOWNLOAD_CONTEXT, "persistent_profile_attempted", False)
                )
            except Exception:
                persistent_attempted = False

            if persistent_attempted:
                logger.info(
                    "IG skip yt-dlp fallback: authenticated persistent profile "
                    "was already used for this restricted/carousel post"
                )
                result_box[0] = (
                    "RETRY",
                    error3 or (
                        "IG authenticated persistent-profile collection did not complete; "
                        "anonymous yt-dlp fallback was intentionally skipped"
                    ),
                )
                return

            # For ordinary public posts that never needed the persistent profile,
            # preserve the existing one-shot yt-dlp emergency fallback.
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

    _set_current_profile_output_owner("")
    return result
