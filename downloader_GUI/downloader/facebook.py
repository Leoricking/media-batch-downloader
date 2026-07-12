# v12.04 Watch Fast yt-dlp Route + Anti-Hang Timeout
# v12.03 Watch Video Route Fix: keep /watch/?v= out of photo-gallery pipeline
# v11.93 FB Scoped Manifest Expected-Count Fix
import hashlib
import html
import os
import random
import re
import shutil
import sqlite3
import subprocess
import threading
from urllib.parse import urlparse, unquote, parse_qs, parse_qsl, urlencode, urlunparse

import requests
import yt_dlp
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config import DOWNLOAD_DIR, TEMP_DIR, COOKIES_FILE

try:
    from config import FB_PARSER_PROFILE_DIR
except Exception:
    from config import DATA_DIR
    FB_PARSER_PROFILE_DIR = os.path.join(DATA_DIR, "playwright_fb_profile")

try:
    from config import FB_HEADLESS
except Exception:
    FB_HEADLESS = True

try:
    from config import FB_DEBUG_CAPTURE
except Exception:
    FB_DEBUG_CAPTURE = False

try:
    from config import FB_FILENAME_WITH_TITLE
except Exception:
    # True: multiple FB images are named 001_<post_title>.jpg for easier archive/search.
    # Set False in config.py if you prefer 1.jpg / 2.jpg.
    FB_FILENAME_WITH_TITLE = True

from utils.filename import safe_title
from utils.logger import get_logger

try:
    from PIL import Image
except Exception:
    Image = None

try:
    from opencc import OpenCC
except Exception:
    OpenCC = None

logger = get_logger("facebook")

try:
    from utils.cookie_helper import load_netscape_cookies_to_playwright
except Exception:
    load_netscape_cookies_to_playwright = None

# v11.91 Reel Exact-Scope Fix: visible active-video only + canonical reel lock + correct caption title
# v11.98 FB Full-Gallery Best-Available Source Completion
# v11.97 FB High-Resolution CDN Variant Retry + No False SUCCESS
# v11.96 Plus-N Full Gallery Count + High-Resolution Output Gate
# v11.95 Playwright Request content_type Scope Fix
# v11.94 Scoped Best-Available Small Image Guard: allow verified 18-20KB still images only
# v11.92 Reel Title-Only Fix: restore proven Reel download path + caption filename

_cc = OpenCC("s2t") if OpenCC else None

_MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".mp4", ".webp", ".m4v", ".mov"}
_DL_TIMEOUT = 900
# v12.04:
# /watch/?v= tasks should not spend 15+ minutes in Playwright deep scans or
# unbounded slow yt-dlp downloads. Use a bounded single-video guard.
_FB_WATCH_YTDLP_MAX_SECONDS = 240
_FB_WATCH_YTDLP_MIN_BYTES_PER_SEC = 8 * 1024
_MAX_FB_ITEMS = 40
_MIN_FILE_SIZE = 20 * 1024
# v11.94:
# Facebook photo posts can serve one real post-scoped PNG/JPG just under 20KB.
# Keep the normal 20KB guard, but allow only verified still-image bytes >=18KB.
_FB_BEST_AVAILABLE_IMAGE_MIN_SIZE = 18 * 1024
# v11.99:
# In exact +N full-gallery mode, one real FB CDN source can be ~15KB.  Allow it
# only after count-proven full-gallery collection, never as a normal fallback.
_FB_FULL_GALLERY_SOURCE_MIN_SIZE = 14 * 1024
# v11.96: photo-post outputs must not finalize 480px mosaic thumbnails as SUCCESS.
_FB_MIN_OUTPUT_IMAGE_LONG_EDGE = 720
_FB_MIN_OUTPUT_IMAGE_SHORT_EDGE = 400
_PREFERRED_IMAGE_SIZE = 80 * 1024


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


def clear_temp():
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
    os.makedirs(TEMP_DIR, exist_ok=True)


def _clear_temp_after_terminal_failure(status: str, reason: str = ""):
    """Clean temporary Facebook post/ residue after terminal non-success results.

    SUCCESS is intentionally excluded because move_files() owns cleanup after a
    valid move. This prevents failed / blocked / unavailable / retry tasks from
    leaving cap_*.jpg / cap_*.mp4 files in TEMP_DIR and polluting later tasks.
    """
    if (status or "").upper() == "SUCCESS":
        return

    try:
        leftovers = _list_media_files(TEMP_DIR)
    except Exception:
        leftovers = []

    if leftovers:
        logger.info(
            f"FB 清理暫存 post/：status={status}, leftover={len(leftovers)}, "
            f"reason={reason or 'n/a'}"
        )

    clear_temp()


def _is_fb_reel_url(url: str) -> bool:
    """Detect Facebook Reel / short-video share URLs without affecting normal /share/ photo posts."""
    low = (url or "").lower()
    return any(x in low for x in [
        "/share/r/",
        "/share/v/",
        "/reel/",
        "/reels/",
        "/watch/reel/",
        "fb.watch/",
    ])


def _is_fb_watch_video_url(url: str) -> bool:
    """Detect normal Facebook Watch/video URLs.

    v12.03:
    /watch/?v=<id> is a single video task, not a photo gallery.  It must not
    enter the photo-link / +N completeness pipeline, because logged-in Facebook
    pages can expose login_alert/photo links near the video and make the task
    look like a 3-photo gallery.
    """
    low = (url or "").lower()
    return bool(
        "/watch/?v=" in low
        or "/watch?v=" in low
        or "/videos/" in low
        or "video.php" in low
    )


def _is_fb_video_like_url(url: str) -> bool:
    return _is_fb_reel_url(url) or _is_fb_watch_video_url(url)


def _extract_fb_reel_or_share_id(url: str) -> str:
    """Extract a stable ID for fallback names such as Facebook_Reel_186iijKiQf."""
    u = html.unescape(unquote(str(url or "")))
    patterns = [
        r"/share/r/([^/?#&]+)",
        r"/share/v/([^/?#&]+)",
        r"/reels?/([^/?#&]+)",
        r"/watch/reel/([^/?#&]+)",
        r"[?&]v=([0-9A-Za-z_-]+)",
        r"/videos/([0-9A-Za-z_-]+)",
    ]

    for pat in patterns:
        m = re.search(pat, u, flags=re.I)
        if m:
            return safe_title(m.group(1))[:48]

    try:
        parsed = urlparse(u)
        base = os.path.basename(parsed.path.strip("/"))
        if base and base.lower() not in {"r", "v", "share", "reel", "reels", "watch"}:
            return safe_title(base)[:48]
    except Exception:
        pass

    return ""


def _fb_reel_fallback_title(url: str) -> str:
    rid = _extract_fb_reel_or_share_id(url)
    return f"Facebook_Reel_{rid}" if rid else "Facebook_Reel"


def _extract_canonical_fb_reel_id_from_page(page) -> str:
    """Resolve the reel/video identity currently displayed by the page.

    Direct /reel/<numeric-id> tasks are hard-locked to this identity.  Metadata
    fallbacks are used because Facebook can keep a share URL in location.href
    while exposing the canonical reel URL in og:url/canonical.
    """
    candidates = []
    try:
        candidates.append(page.url or "")
    except Exception:
        pass
    for sel, attr in [
        ('meta[property="og:url"]', 'content'),
        ('link[rel="canonical"]', 'href'),
    ]:
        try:
            value = page.locator(sel).first.get_attribute(attr) or ""
            if value:
                candidates.append(value)
        except Exception:
            pass
    for value in candidates:
        m = re.search(r"/(?:reel|reels|watch/reel)/(\d{6,})", html.unescape(unquote(value)), flags=re.I)
        if m:
            return m.group(1)
    return ""


def _get_fb_reel_caption_title(page, fallback: str = "Facebook_Reel") -> str:
    """Return the caption/title belonging to the active Reel only.

    Avoid document-wide feed text because logged-in Reel pages preload sibling
    reels and their captions.  Metadata is preferred, followed by text nearest
    the active video element.
    """
    candidates = []
    for sel in [
        'meta[property="og:description"]',
        'meta[name="description"]',
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
    ]:
        try:
            value = page.locator(sel).first.get_attribute('content') or ""
            if value:
                candidates.append(value)
        except Exception:
            pass

    try:
        scoped = page.evaluate(r"""
        () => {
          const videos = Array.from(document.querySelectorAll('video')).filter(v => {
            const r = v.getBoundingClientRect();
            const s = getComputedStyle(v);
            return s.display !== 'none' && s.visibility !== 'hidden' &&
                   parseFloat(s.opacity || '1') > 0 && r.width >= 220 && r.height >= 220 &&
                   r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
          });
          if (!videos.length) return [];
          videos.sort((a,b) => {
            const ra=a.getBoundingClientRect(), rb=b.getBoundingClientRect();
            return (rb.width*rb.height)-(ra.width*ra.height);
          });
          let root = videos[0].closest('div[role="dialog"], div[role="main"], article') || document;
          const out=[];
          for (const el of root.querySelectorAll('[data-ad-preview="message"], div[dir="auto"], span[dir="auto"]')) {
            const t=(el.innerText||el.textContent||'').replace(/\s+/g,' ').trim();
            if (t.length >= 4 && t.length <= 240) out.push(t);
          }
          return out.slice(0,20);
        }
        """) or []
        candidates.extend(scoped)
    except Exception:
        pass

    bad = {
        'reel', 'facebook', 'facebook reel', '讚', '留言', '分享',
        'like', 'comment', 'share', '所有人', 'public'
    }
    for raw in candidates:
        clean = _clean_fb_post_title_for_path(raw, fallback="")
        low = clean.lower().strip()
        if not clean or low in bad:
            continue
        if re.fullmatch(r"[\d,.]+", clean):
            continue
        if clean.startswith('Facebook_Reel_'):
            continue
        return clean
    return _clean_fb_post_title_for_path(fallback, fallback="Facebook_Reel")


def _get_active_fb_reel_video_candidates(page) -> list[dict]:
    """Collect only URLs attached to the largest visible active video element.

    This intentionally rejects document-wide network/performance candidates.
    Facebook Reel pages preload many sibling reels; choosing the largest file
    from that pool can download another reel while still reporting SUCCESS.
    """
    try:
        raw = page.evaluate(r"""
        () => {
          const W=innerWidth||1600, H=innerHeight||1000;
          const videos=Array.from(document.querySelectorAll('video')).map((v,i) => {
            const r=v.getBoundingClientRect();
            const s=getComputedStyle(v);
            const visible=s.display!=='none' && s.visibility!=='hidden' && parseFloat(s.opacity||'1')>0 &&
              r.width>=180 && r.height>=180 && r.right>0 && r.bottom>0 && r.left<W && r.top<H;
            const overlapX=Math.max(0,Math.min(r.right,W)-Math.max(r.left,0));
            const overlapY=Math.max(0,Math.min(r.bottom,H)-Math.max(r.top,0));
            return {v,i,r,visible,area:overlapX*overlapY};
          }).filter(x=>x.visible).sort((a,b)=>b.area-a.area);
          if (!videos.length) return [];
          const v=videos[0].v;
          const urls=[];
          const add=(u,score) => { u=(u||'').trim(); if(u && !u.startsWith('blob:')) urls.push({src:u,type:'video',score}); };
          add(v.currentSrc||'', 10000000);
          add(v.src||'', 9900000);
          add(v.getAttribute('src')||'', 9800000);
          for (const s of v.querySelectorAll('source[src]')) add(s.src||s.getAttribute('src')||'', 9700000);
          return urls;
        }
        """) or []
    except Exception:
        raw = []
    out=[]
    for item in raw:
        src=(item.get('src') or '').strip()
        if not src or not _looks_like_real_fb_media_url(src):
            continue
        if not any(x in src.lower() for x in ['.mp4','.m4v','.mov','video']):
            continue
        out.append({'type':'video','src':src,'score':int(item.get('score') or 0)+_media_quality_score(src)})
    return _dedupe_ordered(out)


def _is_fallback_fb_title(title: str) -> bool:
    clean = _clean_fb_post_title_for_path(title or "", fallback="Facebook_Post")
    return clean in {"Facebook_Post", "Facebook", "Facebook_Video", "Facebook_Watch"}


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


def _classify_error(err: str):
    e = (err or "").lower()

    if any(k in e for k in [
        "please wait a few minutes",
        "rate limit",
        "too many requests",
        "429",
        "timeout",
        "timed out",
        "net::err_timed_out",
    ]):
        return "RETRY", err

    if any(k in e for k in [
        "only available for registered users",
        "requires login",
        "sign in",
        "must log in",
        "private",
        "login",
        "registered users",
        "需要登入",
        "登入",
    ]):
        return "BLOCKED", err

    if any(k in e for k in [
        "404",
        "not found",
        "deleted",
        "this content isn't available",
        "page not found",
        "內容目前無法查看",
        "內容不存在",
    ]):
        return "UNAVAILABLE", err

    return "FAILED", err


def _resolve_share_url(url: str):
    try:
        r = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.facebook.com/",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            },
            allow_redirects=True,
            timeout=30,
        )

        if r.url:
            return r.url

    except Exception:
        pass

    return url


def _get_fb_parser_profile_root() -> str:
    """Project-local dedicated Chrome user-data directory for FB_Parser.

    This is the preferred Facebook login / trust-state storage. It replaces the
    old workflow that required exporting cookies.txt. cookies.txt is still used
    only as a legacy emergency fallback for yt-dlp or when the profile has not
    been initialized yet.
    """
    configured = (
        os.environ.get("FB_CHROME_USER_DATA_DIR")
        or str(FB_PARSER_PROFILE_DIR or "")
        or ""
    )
    configured = configured.strip().strip('"')
    if configured:
        os.makedirs(configured, exist_ok=True)
        return configured

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "playwright_fb_profile")
    os.makedirs(root, exist_ok=True)
    return root


def _resolve_fb_chrome_profile_directory() -> str:
    configured = os.environ.get("FB_CHROME_PROFILE_DIRECTORY") or "Default"
    configured = str(configured).strip().strip('"')
    return configured or "Default"



def _fb_cookie_db_paths(user_data_dir: str, profile_dir: str) -> list[str]:
    """Return Chromium cookie DB candidates for the dedicated FB profile."""
    profile_root = os.path.join(user_data_dir, profile_dir)
    candidates = [
        os.path.join(profile_root, "Network", "Cookies"),
        os.path.join(profile_root, "Cookies"),
        os.path.join(user_data_dir, "Network", "Cookies"),
        os.path.join(user_data_dir, "Cookies"),
    ]

    out = []
    seen = set()
    for path in candidates:
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.exists(path):
            out.append(path)
    return out


def _fb_cookie_names_from_db(
    user_data_dir: str,
    profile_dir: str,
) -> set[str]:
    """Read Facebook cookie names without decrypting values.

    Chromium keeps cookie names and host keys in plaintext even though values are
    encrypted. Presence of both c_user and xs for facebook.com is sufficient for
    the UI login indicator and works even while the FB Parser browser is open.
    """
    names = set()

    for db_path in _fb_cookie_db_paths(user_data_dir, profile_dir):
        temp_copy = ""

        try:
            # Copy first so an actively opened Chrome profile does not hold a
            # read lock on the file we query.
            temp_copy = os.path.join(
                TEMP_DIR,
                f"fb_login_status_{os.getpid()}_{threading.get_ident()}.sqlite",
            )
            os.makedirs(TEMP_DIR, exist_ok=True)
            shutil.copy2(db_path, temp_copy)

            conn = sqlite3.connect(f"file:{temp_copy}?mode=ro", uri=True)
            try:
                cursor = conn.execute(
                    """
                    SELECT name
                    FROM cookies
                    WHERE host_key LIKE '%facebook.com'
                      AND name IN ('c_user', 'xs')
                      AND (
                            length(value) > 0
                            OR length(encrypted_value) > 0
                          )
                    """
                )
                names.update(str(row[0] or "") for row in cursor.fetchall())
            finally:
                conn.close()

        except Exception:
            continue

        finally:
            if temp_copy and os.path.exists(temp_copy):
                try:
                    os.remove(temp_copy)
                except Exception:
                    pass

        if {"c_user", "xs"}.issubset(names):
            break

    return names


def get_login_status() -> tuple[bool, str]:
    """Check FB Parser login without opening a visible browser.

    The cookie database is checked first, so the status still works while the
    dedicated FB Parser Chrome window is open. Playwright is only a fallback.
    """
    user_data_dir = _get_fb_parser_profile_root()
    profile_dir = _resolve_fb_chrome_profile_directory()

    if not user_data_dir or not os.path.exists(user_data_dir):
        return False, "未建立 Profile"

    # Primary check: read Chromium's cookie database directly.
    cookie_names = _fb_cookie_names_from_db(
        user_data_dir,
        profile_dir,
    )
    if {"c_user", "xs"}.issubset(cookie_names):
        return True, "已登入"

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

            # Request all cookies because FB may store them on .facebook.com,
            # www.facebook.com, m.facebook.com, or locale-specific hosts.
            cookies = context.cookies()
            names = {
                str(cookie.get("name") or "")
                for cookie in cookies
                if "facebook.com" in str(cookie.get("domain") or "").lower()
            }

            if {"c_user", "xs"}.issubset(names):
                return True, "已登入"

            return False, "未登入"

    except Exception as e:
        text = re.sub(r"\s+", " ", str(e or "")).strip()

        if "user data directory is already in use" in text.lower():
            # The DB check above already ran. If no valid login cookies were
            # found, report the profile lock accurately rather than claiming
            # the account is logged out.
            return False, "Profile 使用中"

        return False, "狀態無法確認"

    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass


def open_fb_parser_profile(start_url: str = "https://www.facebook.com/") -> str:
    """Open the project-local FB_Parser Chrome profile for one-time login/trust setup."""
    user_data_dir = _get_fb_parser_profile_root()
    profile_dir = _resolve_fb_chrome_profile_directory()

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
        start_url or "https://www.facebook.com/",
    ]

    subprocess.Popen(args)
    return user_data_dir


def _get_fresh_fb_profile_page(context):
    """Open one fresh task tab while keeping FB_Parser cookies/session state."""
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


def _load_netscape_cookies(path: str, domain_keyword: str):
    """
    Load Netscape-format cookies.txt into Playwright cookie dicts.

    v11.8-cookie-ready:
    - Supports one shared cookies.txt for Instagram + Facebook.
    - Filters by domain, e.g. facebook.com.
    - Correctly handles #HttpOnly_ prefix exported by browser cookie tools.
    - Falls back to the built-in parser if utils.cookie_helper is not present.
    """
    if load_netscape_cookies_to_playwright is not None:
        try:
            return load_netscape_cookies_to_playwright(
                path,
                domain_filter=domain_keyword,
            )
        except Exception as e:
            logger.warning(f"讀取 cookies helper 失敗，改用內建 parser: {e}")

    cookies = []

    if not path or not os.path.exists(path):
        return cookies

    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for raw in f:
                line = raw.strip()

                if not line:
                    continue

                http_only = False

                if line.startswith("#HttpOnly_"):
                    line = line.replace("#HttpOnly_", "", 1)
                    http_only = True
                elif line.startswith("#"):
                    continue

                parts = line.split("\t")

                if len(parts) != 7:
                    continue

                domain, _include_subdomains, cookie_path, secure_flag, expires, name, value = parts

                if domain_keyword and domain_keyword not in domain.lstrip("."):
                    continue

                if not name:
                    continue

                cookie = {
                    "name": name,
                    "value": value,
                    "domain": domain,
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
        logger.warning(f"讀取 FB cookies 失敗: {e}")

    return cookies

def _is_bad_fb_media_url(url: str) -> bool:
    low = (url or "").lower()

    if not low:
        return True

    bad = [
        "static.xx.fbcdn.net",
        "/rsrc.php",
        "profile_pic",
        "sprite",
        "emoji",
        "icon",
        "logo",
        "s32x32",
        "p32x32",
        "s40x40",
        "s50x50",
        "s64x64",
        "p64x64",
        "favicon",
        "map_tile",
        "safe_image.php",
        "external",
        "hads-ak",
        "ads",
        "cp0_dst-jpg_p32x32",
        "dst-jpg_s200x200",
        "t39.30808-1",
        "q=40",
        "q=50",
        "q=60",
    ]

    return any(x in low for x in bad)




def _is_probable_fb_thumbnail_url(url: str) -> bool:
    """FB viewer 會先送縮圖 placeholder；這些 URL 不應優先下載。"""
    low = (url or "").lower()
    if not low:
        return True

    thumb_patterns = [
        "p32x32", "p40x40", "p50x50", "p64x64", "p75x75", "p100x100",
        "p120x120", "p160x160", "p200x200", "p320x320", "p526x296",
        "s32x32", "s40x40", "s50x50", "s64x64", "s75x75", "s100x100",
        "s120x120", "s160x160", "s200x200", "s320x320", "s526x296",
        "dst-jpg_s200x200", "cp0_dst-jpg_p32x32", "q=40", "q=50", "q=60",
    ]
    return any(x in low for x in thumb_patterns)

def _looks_like_real_fb_media_url(url: str) -> bool:
    if not url:
        return False

    url = html.unescape(unquote(url.strip()))
    low = url.lower()

    if _is_bad_fb_media_url(low):
        return False

    if low.startswith("data:"):
        return False

    if not ("scontent" in low or "fbcdn.net" in low or "video" in low):
        return False

    if any(x in low for x in [".mp4", ".m4v", ".mov"]):
        return True

    return any(x in low for x in [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".jpg?",
        ".jpeg?",
        ".png?",
        ".webp?",
        "format=jpg",
        "format=webp",
    ])


def _media_quality_score(url: str) -> int:
    low = (url or "").lower()
    score = 0

    if any(x in low for x in [".mp4", ".m4v", ".mov"]):
        score += 10000

    for n in [4096, 3000, 2048, 1920, 1440, 1280, 1080, 960, 720, 640, 480, 320, 200, 100, 64, 32]:
        if str(n) in low:
            score += n

    if "s2048x2048" in low:
        score += 5000
    if "s1440x1440" in low or "p1440x1440" in low:
        score += 4500
    if "s1080x1080" in low or "p1080x1080" in low:
        score += 3000
    if "s720x720" in low or "p720x720" in low:
        score += 1500
    if "s200x200" in low:
        score -= 5000
    if "p32x32" in low or "s32x32" in low:
        score -= 10000

    return score



def _normalized_exact_fb_media_url(src: str) -> str:
    try:
        parsed = urlparse(html.unescape(unquote(str(src or "").strip())))
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                parsed.query,
                "",
            )
        )
    except Exception:
        return str(src or "").strip()


def _build_fb_highres_image_url_variants(src: str) -> list[str]:
    """Build conservative same-image FB CDN variants for high-res retry.

    Facebook gallery/viewer can expose only a 480px transformed URL first.
    Generated variants are never trusted blindly; they must pass the existing
    real-byte resolution, type and completeness gates before final output.
    """
    raw = html.unescape(unquote(str(src or "").strip()))
    if not raw or not _looks_like_real_fb_media_url(raw):
        return []

    low = raw.lower()
    if any(x in low for x in [".mp4", ".m4v", ".mov"]):
        return []

    try:
        parsed = urlparse(raw)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
    except Exception:
        return []

    variants = []
    seen = {_normalized_exact_fb_media_url(raw)}

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

        key = _normalized_exact_fb_media_url(candidate)
        if not key or key in seen:
            return
        seen.add(key)
        variants.append(candidate)

    if any(key.lower() == "stp" for key, _value in pairs):
        for target_stp in [
            "dst-jpg_s2048x2048_tt6",
            "dst-jpg_s1440x1440_tt6",
            "dst-jpg_s1080x1080_tt6",
            "dst-jpg_p960x960_tt6",
            "dst-jpg_p720x720_tt6",
        ]:
            emit([
                (key, target_stp if key.lower() == "stp" else value)
                for key, value in pairs
            ])

        emit([(key, value) for key, value in pairs if key.lower() != "stp"])
        emit([
            (key, value)
            for key, value in pairs
            if key.lower() not in {"stp", "c"}
        ])

    return variants


def _expand_fb_candidate_highres_variants(items: list[dict]) -> list[dict]:
    expanded = []
    for item in items or []:
        if not isinstance(item, dict):
            expanded.append(item)
            continue

        expanded.append(item)
        src = item.get("src", "")
        if item.get("type") == "video" or any(x in str(src).lower() for x in [".mp4", ".m4v", ".mov"]):
            continue

        for order, variant in enumerate(_build_fb_highres_image_url_variants(src), 1):
            clone = dict(item)
            clone["src"] = variant
            clone["score"] = int(item.get("score") or 0) + 800000 - order
            clone["_variant_of"] = src
            clone["_variant_reason"] = "fb-highres-cdn-transform"
            expanded.append(clone)

    return expanded


def _dedupe_ordered(items):
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

        if not _looks_like_real_fb_media_url(src):
            continue

        is_video = item.get("type") == "video" or any(
            x in src.lower() for x in [".mp4", ".m4v", ".mov"]
        )
        if is_video:
            path = urlparse(src.split("?")[0]).path
            basename = os.path.basename(path)
            key = basename or src[:180]
        else:
            key = _normalized_exact_fb_media_url(src)

        if key in seen:
            continue

        seen.add(key)

        item["src"] = src
        item["score"] = item.get("score", 0) + _media_quality_score(src)

        out.append(item)

    return out


def _natural_key(path: str):
    base = os.path.basename(path)
    return [
        int(x) if x.isdigit() else x.lower()
        for x in re.split(r"(\d+)", base)
    ]


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



def _is_valid_fb_still_image_body(body: bytes, media_url: str = "", content_type: str = "") -> bool:
    """Return True for real JPG/PNG/WEBP image bytes only."""
    if not body:
        return False

    ct = (content_type or "").lower()
    low = (media_url or "").lower()
    head = body[:32]

    if any(x in low for x in [".mp4", ".m4v", ".mov", "video"]):
        return False
    if ct.startswith("video/"):
        return False
    if "text/html" in ct or "application/json" in ct:
        return False

    return bool(
        head.startswith(b"\xff\xd8\xff")
        or head.startswith(b"\x89PNG\r\n\x1a\n")
        or (head.startswith(b"RIFF") and b"WEBP" in head[:16])
        or ct.startswith("image/")
    )


def _is_verified_best_available_fb_image(
    body: bytes,
    media_url: str = "",
    content_type: str = "",
    *,
    size: int | None = None,
) -> bool:
    """Narrow allowance for real post-scoped still images just under 20KB."""
    actual_size = int(size if size is not None else (len(body) if body else 0))

    if actual_size < _FB_BEST_AVAILABLE_IMAGE_MIN_SIZE:
        return False
    if not _looks_like_real_fb_media_url(media_url):
        return False
    if _is_probable_fb_thumbnail_url(media_url):
        return False

    return _is_valid_fb_still_image_body(
        body,
        media_url=media_url,
        content_type=content_type,
    )


def _is_verified_best_available_fb_image_file(path: str, media_url: str = "") -> bool:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(64)
    except Exception:
        return False

    if size < _FB_BEST_AVAILABLE_IMAGE_MIN_SIZE:
        return False

    if not _is_valid_fb_still_image_body(
        head,
        media_url=media_url or path,
        content_type="image/unknown",
    ):
        return False

    low_res, _reason = _is_low_resolution_fb_output(path, media_url)
    if low_res:
        return False

    return True


def _download_with_playwright_request(
    context,
    url: str,
    dst: str,
    referer: str,
    allow_full_gallery_source: bool = False,
):
    headers = {
        "Referer": referer,
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,video/*,*/*;q=0.8",
    }

    resp = context.request.get(
        url,
        headers=headers,
        timeout=60000,
    )

    if not resp.ok:
        raise Exception(f"Playwright request failed: HTTP {resp.status}")

    headers = resp.headers or {}
    content_type = (
        headers.get("content-type", "")
        or headers.get("Content-Type", "")
        or ""
    )

    body = resp.body()

    if not body or len(body) < _MIN_FILE_SIZE:
        if _is_verified_best_available_fb_image(
            body,
            media_url=url,
            content_type=content_type,
        ):
            logger.info(
                f"FB best-available scoped image accepted below 20KB: "
                f"{len(body)} bytes | {os.path.basename(urlparse(str(url).split('?')[0]).path)}"
            )
        elif (
            allow_full_gallery_source
            and body
            and len(body) >= _FB_FULL_GALLERY_SOURCE_MIN_SIZE
            and _looks_like_real_fb_media_url(url)
            and _is_valid_fb_still_image_body(
                body,
                media_url=url,
                content_type=content_type,
            )
        ):
            # v12.00:
            # Exact +N full-gallery mode has already proven the complete post
            # sequence.  Facebook may expose the final valid source with
            # thumbnail-like transform params, so do not reject it by URL shape
            # alone. The real bytes still pass image header and dimension checks.
            logger.info(
                f"FB full-gallery exact-count source accepted after high-res failed: "
                f"{len(body)} bytes | {os.path.basename(urlparse(str(url).split('?')[0]).path)}"
            )
        else:
            raise Exception(f"Playwright request 檔案過小: {len(body) if body else 0} bytes")

    with open(dst, "wb") as f:
        f.write(body)

    return len(body)



def _get_image_dimensions(path: str) -> tuple[int, int]:
    if Image is None:
        return 0, 0
    try:
        with Image.open(path) as img:
            return int(img.width or 0), int(img.height or 0)
    except Exception:
        return 0, 0


def _is_low_resolution_fb_output(path: str, src: str = "") -> tuple[bool, str]:
    """Reject clear Facebook mosaic/thumbnail still images before final output."""
    ext = os.path.splitext(path or "")[1].lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        return False, ""

    w, h = _get_image_dimensions(path)
    if not w or not h:
        return False, ""

    if max(w, h) < _FB_MIN_OUTPUT_IMAGE_LONG_EDGE or min(w, h) < _FB_MIN_OUTPUT_IMAGE_SHORT_EDGE:
        return True, f"FB 圖片解析度過低：actual={w}x{h}"

    return False, ""


def _is_verified_fb_best_available_source_file(path: str, src: str = "") -> tuple[bool, str]:
    """Accept a real low-res file only as full-gallery best available source.

    This is intentionally NOT a general thumbnail bypass. It is used only after
    +N full-gallery collection has already proven the expected item count, while
    high-resolution CDN variants failed by 403 or by the normal resolution gate.
    """
    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0

    if size < _FB_FULL_GALLERY_SOURCE_MIN_SIZE:
        return False, f"best-available 檔案過小: {size} bytes"

    if not _looks_like_real_fb_media_url(src):
        return False, "不是可信 Facebook CDN 圖片"

    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except Exception:
        return False, "無法讀取圖片檔頭"

    if not _is_valid_fb_still_image_body(
        head,
        media_url=src or path,
        content_type="image/unknown",
    ):
        return False, "不是有效 JPG/PNG/WEBP 圖片"

    w, h = _get_image_dimensions(path)
    if not w or not h:
        return False, "無法驗證圖片尺寸"

    # Do not accept tiny UI thumbnails/icons.  480x320 is allowed only in the
    # exact full-gallery best-available path because some FB meme/anime uploads
    # are genuinely stored at that size.
    if max(w, h) < 480 or min(w, h) < 300:
        return False, f"best-available 尺寸仍過小: actual={w}x{h}"

    return True, f"actual={w}x{h}, bytes={size}"


def _download_best_candidate(context, candidates, dst_base: str, referer: str):
    candidates = _expand_fb_candidate_highres_variants(candidates)
    candidates = _dedupe_ordered(candidates)

    if not candidates:
        raise Exception("沒有候選媒體 URL")

    candidates = sorted(
        candidates,
        key=lambda x: x.get("score", 0),
        reverse=True,
    )

    best_tmp = None
    best_size = 0
    best_ext = ".jpg"
    errors = []

    for idx, item in enumerate(candidates[:18], 1):
        src = item.get("src", "")

        if not src:
            continue

        ext = (
            ".mp4"
            if item.get("type") == "video" or any(x in src.lower() for x in [".mp4", ".m4v", ".mov"])
            else _ext_from_url(src, ".jpg")
        )

        tmp = f"{dst_base}.candidate_{idx:02d}{ext}"

        try:
            # v11.3 Solid Write: 若 response handler 已經把二進位實體化，
            # 這裡直接 copy，避免視窗關閉後 request 失效或 FB 再次回縮圖。
            temp_path = item.get("temp_path") or item.get("persisted_path")
            if temp_path and os.path.exists(temp_path):
                shutil.copy2(temp_path, tmp)
                size = os.path.getsize(tmp)
                if size < _MIN_FILE_SIZE:
                    best_ok = False
                    best_reason = ""
                    if item.get("_allow_fb_best_available_source"):
                        best_ok, best_reason = _is_verified_fb_best_available_source_file(tmp, src)
                    if best_ok:
                        logger.info(
                            f"FB verified full-gallery best-available persisted source accepted: "
                            f"{best_reason} | {os.path.basename(temp_path)}"
                        )
                    elif _is_verified_best_available_fb_image_file(tmp, src):
                        logger.info(
                            f"FB best-available persisted image accepted below 20KB: "
                            f"{size} bytes | {os.path.basename(temp_path)}"
                        )
                    else:
                        raise Exception(f"persisted 檔案過小: {size} bytes")
            else:
                size = _download_with_playwright_request(
                    context,
                    src,
                    tmp,
                    referer=referer,
                    allow_full_gallery_source=bool(
                        item.get("_allow_fb_best_available_source")
                    ),
                )

            low_res, low_res_reason = _is_low_resolution_fb_output(tmp, src)
            if low_res:
                best_ok = False
                best_reason = ""
                if item.get("_allow_fb_best_available_source"):
                    best_ok, best_reason = _is_verified_fb_best_available_source_file(tmp, src)
                if best_ok:
                    logger.info(
                        f"FB verified full-gallery best-available source accepted: "
                        f"{best_reason}; high-res variants unavailable; exact-count full-gallery source"
                    )
                else:
                    raise Exception(low_res_reason)

            if size > best_size:
                if item.get("_variant_reason"):
                    w, h = _get_image_dimensions(tmp)
                    if max(w, h) >= _FB_MIN_OUTPUT_IMAGE_LONG_EDGE and min(w, h) >= _FB_MIN_OUTPUT_IMAGE_SHORT_EDGE:
                        logger.info(
                            f"FB high-res CDN variant accepted: "
                            f"{os.path.basename(urlparse(str(src).split('?')[0]).path)}, "
                            f"actual={w}x{h}, bytes={size}"
                        )
                    else:
                        logger.info(
                            f"FB CDN variant still low-res; using only if exact-count source mode allows it: "
                            f"{os.path.basename(urlparse(str(src).split('?')[0]).path)}, "
                            f"actual={w}x{h}, bytes={size}"
                        )

                if best_tmp and os.path.exists(best_tmp):
                    os.remove(best_tmp)

                best_tmp = tmp
                best_size = size
                best_ext = ext

            else:
                os.remove(tmp)

        except Exception as e:
            errors.append(str(e))

            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    if not best_tmp:
        raise Exception(f"候選全部下載失敗: {errors[-3:]}")

    final_dst = os.path.splitext(dst_base)[0] + best_ext

    if os.path.exists(final_dst):
        os.remove(final_dst)

    shutil.move(best_tmp, final_dst)

    folder = os.path.dirname(dst_base)
    prefix = os.path.basename(dst_base) + ".candidate_"

    for filename in os.listdir(folder):
        if filename.startswith(prefix):
            try:
                os.remove(os.path.join(folder, filename))
            except Exception:
                pass

    return final_dst, best_size


def _list_media_files(root_dir: str):
    out = []

    if not os.path.exists(root_dir):
        return out

    # v11.7 重要修正：
    # _fb_capture 是 response/harvest 的「內部實體化快取」，
    # 只供 _download_best_candidate() copy 使用，不能被 move_files() 當成正式成品搬出。
    # 之前正式資料夾會出現 13 張、而且 2/8、3/9... 重複，
    # 根因就是 os.walk(TEMP_DIR) 把 _fb_capture 裡的快取圖也一起搬走。
    internal_dirs = {
        "_fb_capture",
        "_fb_debug_capture",
        "__pycache__",
    }

    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if d not in internal_dirs]

        for filename in files:
            low_name = filename.lower()

            # 不搬 response/harvest/candidate/debug 快取，只搬正式輸出的 fb_0001.jpg / 下載檔。
            if (
                low_name.startswith("cap_")
                or low_name.startswith("harvest_")
                or low_name.startswith("debug_")
                or ".candidate_" in low_name
            ):
                continue

            ext = os.path.splitext(filename)[1].lower()

            if ext not in _MEDIA_EXTS:
                continue

            path = os.path.join(root, filename)

            try:
                size = os.path.getsize(path)
            except Exception:
                size = 0

            if size >= _MIN_FILE_SIZE or _is_verified_best_available_fb_image_file(path):
                out.append(path)

    out.sort(key=_natural_key)
    return out




def _clean_fb_post_title_for_path(title: str, fallback: str = "Facebook_Post") -> str:
    """Normalize FB post title/body into a Traditional-Chinese Windows-safe title."""
    raw = _to_traditional(title or "").strip()
    raw = html.unescape(raw)
    raw = raw.replace(" | Facebook", " ").replace(" - Facebook", " ")
    raw = re.sub(r"^\s*\(\d+\)\s*", "", raw)  # browser tab notification count
    raw = re.sub(r"\s+-\s+story\s+halaman\s+tv\s*$", "", raw, flags=re.I)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        raw = fallback
    clean = safe_title(raw)
    if not clean or clean.lower() in {
        "facebook", "untitled", "log in or sign up to view", "facebook watch",
        "facebook video", "facebook_post", "fb_post",
    }:
        clean = fallback
    return clean[:90].strip(" ._-，,。") or fallback



def _cleanup_fb_debug_capture() -> None:
    """v11.20: remove DOWNLOAD_DIR/_fb_debug_capture after a successful FB task."""
    try:
        debug_dir = os.path.join(DOWNLOAD_DIR, "_fb_debug_capture")
        if os.path.exists(debug_dir):
            shutil.rmtree(debug_dir, ignore_errors=True)
            logger.info("FB debug capture cleaned: _fb_debug_capture")
    except Exception as e:
        logger.warning(f"FB debug capture cleanup failed: {e}")


def move_files(title: str) -> bool:
    """
    Move validated Facebook temp outputs into DOWNLOAD_DIR.

    v11.24 safety guard:
    - 多圖 / 相簿任務不允許把一堆 .mp4 串流候選搬成 Facebook_Post/1.mp4...
    - 若 title 仍是 Facebook_Post 且暫存內有多檔，視為 metadata / scope 失敗，回傳 False。
    - 多檔中若同時有圖片與影片，優先保留圖片，丟棄影片候選，避免推薦影片污染。
    """
    files = _list_media_files(TEMP_DIR)

    if not files:
        return False

    name = _clean_fb_post_title_for_path(title, fallback="Facebook_Post")
    image_exts = {".jpg", ".jpeg", ".png", ".webp"}
    video_exts = {".mp4", ".m4v", ".mov"}

    image_files = [p for p in files if os.path.splitext(p)[1].lower() in image_exts]
    video_files = [p for p in files if os.path.splitext(p)[1].lower() in video_exts]

    # 多檔 + 預設標題 = 高風險污染，不可正式輸出 Facebook_Post/1.mp4...
    if len(files) > 1 and name == "Facebook_Post":
        logger.warning("FB move_files blocked: multi-file output still has fallback title Facebook_Post; clear temp and retry/failed")
        clear_temp()
        return False

    # 多檔全是影片時，通常代表 yt-dlp / network 捕捉到多段串流候選，不能當作相簿輸出。
    if len(files) > 1 and video_files and len(video_files) == len(files):
        logger.warning(f"FB move_files blocked: multi-video candidates detected ({len(video_files)} mp4/mov files); clear temp")
        clear_temp()
        return False

    # 多檔混合時，若圖片數量足夠，影片多半是推薦/廣告/串流污染，直接丟棄影片候選。
    if len(files) > 1 and image_files and video_files:
        logger.warning(
            f"FB move_files photo-post cleanup: drop {len(video_files)} video candidates, keep {len(image_files)} images"
        )
        for p in video_files:
            try:
                os.remove(p)
            except Exception:
                pass
        files = image_files

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    if len(files) == 1:
        src = files[0]
        ext = os.path.splitext(src)[1].lower()
        final_ext = ".mp4" if ext in video_exts else ".jpg"

        dst = os.path.join(DOWNLOAD_DIR, f"{name}{final_ext}")

        if os.path.exists(dst):
            os.remove(dst)

        shutil.move(src, dst)
        logger.info(f"FB 單檔完成: {os.path.basename(dst)}")

    else:
        folder = os.path.join(DOWNLOAD_DIR, name)

        if os.path.exists(folder):
            shutil.rmtree(folder, ignore_errors=True)

        os.makedirs(folder, exist_ok=True)

        for i, src in enumerate(files, 1):
            ext = os.path.splitext(src)[1].lower()
            final_ext = ".mp4" if ext in video_exts else ".jpg"

            if FB_FILENAME_WITH_TITLE:
                file_title = name[:70].strip(" ._-，,。") or "Facebook_Post"
                dst = os.path.join(folder, f"{i:03d}_{file_title}{final_ext}")
            else:
                dst = os.path.join(folder, f"{i}{final_ext}")

            if os.path.exists(dst):
                os.remove(dst)

            shutil.move(src, dst)

        logger.info(f"FB 多檔完成: {name}/ ({len(files)} 個)")

    _cleanup_fb_debug_capture()
    clear_temp()
    return True



def move_files_ordered(title: str, ordered_files: list[str]) -> bool:
    """Move exact full-gallery outputs in the same order they were written.

    v12.01:
    Normal move_files() re-scans TEMP_DIR and applies generic size filtering and
    natural sorting. That breaks exact +N full-gallery mode because one proven
    FB source can be around 15KB and the viewer order must be preserved.
    """
    files = []
    seen = set()
    for path in ordered_files or []:
        if not path or not os.path.exists(path):
            continue
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        try:
            if os.path.getsize(path) <= 0:
                continue
        except Exception:
            continue
        files.append(path)

    if not files:
        return False

    name = _clean_fb_post_title_for_path(title, fallback="Facebook_Post")
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    video_exts = {".mp4", ".m4v", ".mov"}

    if len(files) == 1:
        src = files[0]
        ext = os.path.splitext(src)[1].lower()
        final_ext = ".mp4" if ext in video_exts else ".jpg"
        dst = os.path.join(DOWNLOAD_DIR, f"{name}{final_ext}")
        if os.path.exists(dst):
            os.remove(dst)
        shutil.move(src, dst)
        logger.info(f"FB ordered 單檔完成: {os.path.basename(dst)}")
    else:
        folder = os.path.join(DOWNLOAD_DIR, name)
        if os.path.exists(folder):
            shutil.rmtree(folder, ignore_errors=True)
        os.makedirs(folder, exist_ok=True)

        for i, src in enumerate(files, 1):
            ext = os.path.splitext(src)[1].lower()
            final_ext = ".mp4" if ext in video_exts else ".jpg"
            if FB_FILENAME_WITH_TITLE:
                file_title = name[:70].strip(" ._-，,。") or "Facebook_Post"
                dst = os.path.join(folder, f"{i:03d}_{file_title}{final_ext}")
            else:
                dst = os.path.join(folder, f"{i}{final_ext}")
            if os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)

        logger.info(f"FB ordered 多檔完成: {name}/ ({len(files)} 個)")

    _cleanup_fb_debug_capture()
    clear_temp()
    return True

def _get_fb_title(page):
    candidates = []

    try:
        title = page.title() or ""
        title = title.replace(" | Facebook", "").strip()

        if title:
            candidates.append(title)

    except Exception:
        pass

    for sel in [
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
        'meta[property="og:description"]',
        'meta[name="description"]',
    ]:
        try:
            val = page.locator(sel).first.get_attribute("content")

            if val:
                candidates.append(val.strip())

        except Exception:
            pass

    for c in candidates:
        c = _to_traditional(c)
        clean = safe_title(c)

        if clean and clean.lower() not in {
            "facebook",
            "untitled",
            "log in or sign up to view",
            "facebook watch",
            "facebook video",
        }:
            return c.strip()

    return "Facebook_Post"



_FB_PREFETCHED_TITLES: dict[str, str] = {}
_FB_PREFETCHED_TITLES_LOCK = threading.RLock()


def _fb_task_key(url: str) -> str:
    return html.unescape(unquote(str(url or ""))).strip()


def _publish_fb_task_title(task_url: str, title: str) -> str:
    clean = _clean_fb_post_title_for_path(title, fallback="")
    if not clean:
        return ""
    key = _fb_task_key(task_url)
    if key:
        with _FB_PREFETCHED_TITLES_LOCK:
            _FB_PREFETCHED_TITLES[key] = clean
    try:
        import queue_manager
        queue_manager.update_task_title(task_url, clean)
    except Exception as e:
        logger.debug(f"FB task title publish skipped: {e}")
    return clean


def prefetch_post_title(url: str) -> tuple[str, str]:
    """Resolve FB post/Reel title before media download and publish it to GUI."""
    key = _fb_task_key(url)
    with _FB_PREFETCHED_TITLES_LOCK:
        cached = _FB_PREFETCHED_TITLES.get(key, "")
    if cached:
        _publish_fb_task_title(url, cached)
        return cached, "cached"

    context = None
    try:
        resolved = _resolve_share_url(url)
        with sync_playwright() as p:
            user_data_dir = _get_fb_parser_profile_root()
            profile_dir = _resolve_fb_chrome_profile_directory()
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="chrome",
                headless=FB_HEADLESS,
                no_viewport=True,
                locale="zh-TW",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                args=[
                    f"--profile-directory={profile_dir}",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--start-maximized",
                ],
            )
            page = _get_fresh_fb_profile_page(context)
            page.goto(resolved, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1800)

            if _is_fb_reel_url(url) or _is_fb_reel_url(resolved):
                title = _get_fb_reel_caption_title(page, fallback=_fb_reel_fallback_title(url))
            else:
                title = _get_post_folder_name(page)
                if not title or title == "Facebook_Post":
                    title = _get_fb_title(page)

            clean = _publish_fb_task_title(url, title)
            if resolved and resolved != url and clean:
                _publish_fb_task_title(resolved, clean)
            if clean:
                logger.info(f"FB title prefetch completed before download: {clean}")
                return clean, ""
            return "", "未取得有效 FB 標題"
    except Exception as e:
        logger.info(f"FB title prefetch skipped: {e}")
        return "", str(e)
    finally:
        try:
            if context:
                context.close()
        except Exception:
            pass

def _extract_media_from_html_text(text: str):
    items = []

    if not text:
        return items

    decoded = html.unescape(text)
    decoded = decoded.replace("\\u0026", "&").replace("\\/", "/")

    patterns = [
        r'https?://[^"\'<>\s]+?(?:\.mp4|\.m4v|\.mov|\.jpg|\.jpeg|\.png|\.webp)(?:\?[^"\'<>\s]*)?',
        r'https?://[^"\'<>\s]+?scontent[^"\'<>\s]+',
        r'https?://[^"\'<>\s]+?fbcdn\.net[^"\'<>\s]+',
    ]

    for pat in patterns:
        for m in re.finditer(pat, decoded, flags=re.I):
            src = html.unescape(unquote(m.group(0)))

            if _looks_like_real_fb_media_url(src):
                media_type = "video" if any(x in src.lower() for x in [".mp4", ".m4v", ".mov"]) else "image"

                items.append({
                    "type": media_type,
                    "src": src,
                    "score": 400000 + _media_quality_score(src),
                })

    return _dedupe_ordered(items)


def _get_meta_fb_media(page):
    items = []

    selectors = [
        ('meta[property="og:video"]', "video"),
        ('meta[property="og:video:url"]', "video"),
        ('meta[property="og:video:secure_url"]', "video"),
        ('meta[name="twitter:player:stream"]', "video"),
        ('meta[property="og:image"]', "image"),
        ('meta[property="og:image:secure_url"]', "image"),
        ('meta[name="twitter:image"]', "image"),
        ('link[rel="preload"][as="image"]', "image"),
    ]

    for sel, media_type in selectors:
        try:
            loc = page.locator(sel)
            count = loc.count()

            for i in range(count):
                val = loc.nth(i).get_attribute("content") or loc.nth(i).get_attribute("href")

                if val and _looks_like_real_fb_media_url(val):
                    items.append({
                        "type": media_type,
                        "src": val,
                        "score": 900000 + _media_quality_score(val),
                    })

        except Exception:
            continue

    return _dedupe_ordered(items)


def _get_visible_fb_media_candidates(page):
    js = """
    () => {
      const scopes = [];
      const dialog = document.querySelector('div[role="dialog"]');

      if (dialog) scopes.push(dialog);
      scopes.push(document);

      const keep = [];

      function bad(low) {
        const badList = [
          'static.xx.fbcdn.net',
          '/rsrc.php',
          'profile_pic',
          'sprite',
          'emoji',
          'icon',
          'logo',
          's32x32',
          'p32x32',
          's40x40',
          's50x50',
          's64x64',
          'p64x64',
          'favicon',
          'safe_image.php',
          'hads-ak',
          't39.30808-1'
        ];

        return badList.some(x => low.includes(x));
      }

      function pushCandidate(el, src) {
        if (!src) return;

        const low = src.toLowerCase();

        if (bad(low)) return;
        if (!(low.includes('scontent') || low.includes('fbcdn.net') || low.includes('video'))) return;

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
          w >= 70 &&
          h >= 70 &&
          left > -1600 &&
          top > -1600 &&
          left < window.innerWidth + 1600 &&
          top < window.innerHeight + 1600
        );

        if (!visible) return;

        const naturalW = el.naturalWidth || el.videoWidth || 0;
        const naturalH = el.naturalHeight || el.videoHeight || 0;

        const centerX = left + w / 2;
        const centerY = top + h / 2;

        const dx = Math.abs(centerX - window.innerWidth / 2);
        const dy = Math.abs(centerY - window.innerHeight / 2);

        keep.push({
          type: el.tagName.toLowerCase() === 'video' ? 'video' : 'image',
          src,
          score: (w * h) + ((naturalW || 0) * (naturalH || 0) / 2) - (dx * 2 + dy * 2),
          area: w * h,
          naturalArea: (naturalW || 0) * (naturalH || 0),
          left,
          top
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

      keep.sort((a, b) => b.score - a.score);

      return keep;
    }
    """

    try:
        items = page.evaluate(js) or []
    except Exception:
        items = []

    return _dedupe_ordered(items)


def _collect_current_page_candidates(
    page,
    network_items: list[dict] | None = None,
    *,
    include_network: bool = True,
    include_meta: bool = True,
    include_html: bool = True,
) -> list[dict]:
    """
    收集目前頁面的媒體候選。

    FB 多圖修正重點：
    - photo page 的 og:image / meta preview 很常固定指向同一張封面。
    - 若 meta 分數太高，7 個 photo link 會全部被誤判成同一張，最後只剩 1 張。
    - 所以多圖逐張處理時會 include_meta=False，優先取目前畫面可見大圖。
    """
    items = []

    visible_items = _get_visible_fb_media_candidates(page)
    for it in visible_items:
        it["score"] = int(it.get("score", 0)) + 1300000
    items.extend(visible_items)

    if include_network and network_items:
        items.extend(network_items)

    if include_meta:
        items.extend(_get_meta_fb_media(page))

    if include_html:
        try:
            content = page.content()
            html_items = _extract_media_from_html_text(content)
            for it in html_items:
                it["score"] = int(it.get("score", 0)) - 150000
            items.extend(html_items)
        except Exception:
            pass

    items = _dedupe_ordered(items)

    return sorted(
        items,
        key=lambda x: x.get("score", 0),
        reverse=True,
    )

def _click_plus_overlay_or_first_photo(page):
    try:
        plus = page.locator("text=/^\\+\\d+$/").last

        if plus.count() > 0:
            plus.click(timeout=3000, force=True)
            page.wait_for_timeout(3000)
            return True

    except Exception:
        pass

    js = """
    () => {
      const all = Array.from(document.querySelectorAll('*'));

      const overlay = all.find(el => /^\\+\\d+$/.test((el.innerText || '').trim()));

      if (overlay) {
        try { overlay.click(); return 'plus'; } catch(e) {}

        try {
          const p = overlay.closest('a,button,div[role="button"],div');

          if (p) {
            p.click();
            return 'plus-parent';
          }
        } catch(e) {}
      }

      const candidates = Array.from(document.querySelectorAll('a, img, div[role="button"]'));
      let best = null;
      let bestScore = 0;

      for (const el of candidates) {
        let img = null;

        if (el.tagName.toLowerCase() === 'img') {
          img = el;
        } else {
          img = el.querySelector && el.querySelector('img');
        }

        if (!img) continue;

        const src = (img.currentSrc || img.src || '').toLowerCase();

        if (!(src.includes('scontent') || src.includes('fbcdn.net'))) continue;
        if (src.includes('static.xx.fbcdn.net')) continue;
        if (src.includes('/rsrc.php')) continue;
        if (src.includes('profile_pic') || src.includes('sprite') || src.includes('emoji') || src.includes('icon') || src.includes('logo')) continue;
        if (src.includes('p32x32') || src.includes('s32x32') || src.includes('s200x200')) continue;

        const r = img.getBoundingClientRect();
        const w = r.width || 0;
        const h = r.height || 0;

        if (w < 150 || h < 150) continue;

        const score = w * h;

        if (score > bestScore) {
          best = el;
          bestScore = score;
        }
      }

      if (best) {
        try { best.click(); return 'best'; } catch(e) {}

        try {
          const p = best.closest('a,button,div[role="button"]');

          if (p) {
            p.click();
            return 'best-parent';
          }
        } catch(e) {}
      }

      return '';
    }
    """

    try:
        result = page.evaluate(js)

        if result:
            page.wait_for_timeout(3000)

        return bool(result)

    except Exception:
        return False




def _detect_fb_plus_overlay_count(page) -> int:
    """
    v11.8 Deep Harvest:
    偵測 FB Grid 上的 +N 覆蓋層。
    多圖貼文常見格式：畫面顯示 5 格，其中最後一格是 +12，
    實際總張數通常是 4 + 12 = 16，而不是 ordered link 的 17。
    """
    try:
        vals = page.evaluate(
            r"""
            () => {
              const out = [];
              const nodes = Array.from(document.querySelectorAll('*'));
              for (const el of nodes) {
                const t = (el.innerText || el.textContent || '').trim();
                const m = t.match(/^\+(\d+)$/);
                if (!m) continue;
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') <= 0) continue;
                if (r.width < 20 || r.height < 20) continue;
                out.push(parseInt(m[1], 10));
              }
              return out;
            }
            """
        ) or []
        vals = [int(x) for x in vals if int(x) > 0]
        return max(vals) if vals else 0
    except Exception:
        return 0



def _fb_pcb_key_from_href(href: str) -> str:
    """Extract set=pcb.<post_id> from Facebook photo URLs for same-post scoping."""
    if not href:
        return ""
    try:
        u = html.unescape(unquote(str(href)))
        m = re.search(r"[?&]set=pcb\.([0-9]{8,})", u, flags=re.I)
        if m:
            return "pcb:" + m.group(1)
        m = re.search(r"[?&]set=([^&]+)", u, flags=re.I)
        if m and m.group(1):
            return "set:" + m.group(1)[:80]
    except Exception:
        pass
    return ""


def _dominant_pcb_key_from_links(links: list[str] | None) -> str:
    """
    v11.13 First-Anchor Post Scope.

    Do NOT choose the largest set=pcb group. In logged-in Facebook pages,
    recommendations/feed/sidebar photos may outnumber the real post and become
    the "dominant" group. The user-visible target is normally represented by
    the first real photo link in DOM/visual order, so use that first valid
    set=pcb as the post anchor.
    """
    counts = {}
    order = []

    for link in links or []:
        if not link:
            continue
        try:
            if not _is_true_photo_link(link):
                continue
        except Exception:
            pass

        key = _fb_pcb_key_from_href(link)
        if not key:
            continue

        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1

        # First real photo link is the visible post anchor.
        logger.info(f"FB first-anchor pcb selected={key}")
        return key

    if not counts:
        return ""

    return order[0]


def _filter_links_and_grid_by_pcb(links: list[str], grid_items: list[dict], pcb_key: str):
    """
    Keep photo links/grid items from the same set=pcb post.
    Non-photo permalink entries are dropped here because viewer starts from the resolved post URL.
    """
    if not pcb_key:
        return links, grid_items

    filtered_links = []
    for link in links or []:
        key = _fb_pcb_key_from_href(link)
        if key == pcb_key:
            filtered_links.append(link)

    filtered_grid = []
    for item in grid_items or []:
        href = item.get("href") or ""
        key = _fb_pcb_key_from_href(href)
        if key == pcb_key:
            filtered_grid.append(item)

    # Safety fallback: never wipe everything because some FB layouts omit set=pcb in DOM.
    return (filtered_links or links or []), (filtered_grid or grid_items or [])


def _media_cluster_key_from_src(src: str) -> str:
    """
    Extract a stable media cluster from FB image filenames.
    Target post images in the same album/post usually share the second numeric token.
    Example: 678241279_122261853560161715_149780..._n.jpg -> 12226185
    """
    if not src:
        return ""
    try:
        base = os.path.basename(urlparse(str(src).split("?")[0]).path)
        nums = re.findall(r"[0-9]{8,}", base)
        if len(nums) >= 2:
            return nums[1][:8]
        if nums:
            return nums[0][:8]
    except Exception:
        pass
    return ""


def _pack_media_cluster_key(pack: dict) -> str:
    if not pack:
        return ""
    src = pack.get("src") or ""
    key = _media_cluster_key_from_src(src)
    if key:
        return key
    for cand in pack.get("candidates") or []:
        key = _media_cluster_key_from_src(cand.get("src") or "")
        if key:
            return key
    return ""




def _fb_media_numeric_id_from_src(src: str) -> int:
    """
    v11.16 Manifest-like ordering:
    FB CDN filenames often follow: prefix_mediaId_suffix_n.jpg
    Example: 678241279_122261853560161715_149780..._n.jpg
    The second long numeric token is the stable per-photo media id.
    Sorting by it fixes viewer/intercept order jumps such as 6/7/8 mis-ordering.
    """
    if not src:
        return 0
    try:
        base = os.path.basename(urlparse(str(src).split("?")[0]).path)
        nums = re.findall(r"[0-9]{8,}", base)
        if len(nums) >= 2:
            return int(nums[1])
        if nums:
            return int(nums[0])
    except Exception:
        pass
    return 0


def _sort_items_by_fb_media_id(packs: list[dict]) -> list[dict]:
    """
    v11.16 final order stabilizer.
    Keep only items with a usable FB media id first, sorted by the CDN media id.
    Items without an id are appended in original order, but in post-scoped photo mode
    they should normally have been filtered out already.
    """
    indexed = []
    tail = []
    for original_i, pack in enumerate(packs or []):
        src = pack.get("src") or ""
        mid = _fb_media_numeric_id_from_src(src)
        if mid:
            indexed.append((mid, original_i, pack))
        else:
            tail.append((original_i, pack))
    indexed.sort(key=lambda x: (x[0], x[1]))
    return [p for _, _, p in indexed] + [p for _, p in tail]




def _fb_media_numeric_id_str_from_src(src: str) -> str:
    """Return the stable FB CDN media id as string, when available."""
    try:
        n = _fb_media_numeric_id_from_src(src)
        return str(n) if n else ""
    except Exception:
        return ""


def _manifest_ids_from_packs(packs: list[dict]) -> list[str]:
    """
    Build a whitelist / order manifest from post-scoped link_items.
    This is safer than a single cluster gate: if FB shards one image to a different
    CDN numeric family, the post manifest can still let it pass.
    """
    out = []
    seen = set()
    for pack in packs or []:
        candidates = [pack.get("src", "")] + [
            (c.get("src") or "") for c in (pack.get("candidates") or [])
        ]
        for src in candidates:
            mid = _fb_media_numeric_id_str_from_src(src)
            if mid and mid not in seen:
                seen.add(mid)
                out.append(mid)
                break
    return out


def _pack_has_manifest_id(pack: dict, manifest_ids: set[str]) -> bool:
    if not pack or not manifest_ids:
        return False
    srcs = [pack.get("src", "")] + [(c.get("src") or "") for c in (pack.get("candidates") or [])]
    for src in srcs:
        mid = _fb_media_numeric_id_str_from_src(src)
        if mid and mid in manifest_ids:
            return True
    return False


def _filter_items_by_media_cluster_or_manifest(
    packs: list[dict],
    cluster_key: str,
    manifest_ids: list[str] | None = None,
) -> list[dict]:
    """
    v11.17 Manifest Whitelist:
    Keep an item if it matches the dominant cluster OR an explicit post manifest id.
    This prevents over-clean filtering from dropping a legitimate image if Facebook
    serves it from a sharded CDN id, while still rejecting recommendations/videos.
    """
    if not packs:
        return []

    manifest_set = set(manifest_ids or [])
    out = []
    seen_primary = set()

    for pack in packs or []:
        src = pack.get("src") or ""
        if pack.get("type") == "video" or _is_probably_video_url(src):
            continue

        key = _media_cluster_key_from_src(src)
        in_cluster = bool(cluster_key and key == cluster_key)
        in_manifest = _pack_has_manifest_id(pack, manifest_set)

        if cluster_key or manifest_set:
            if not (in_cluster or in_manifest):
                continue

        clean_candidates = []
        for cand in pack.get("candidates") or []:
            csrc = cand.get("src") or ""
            if _is_probably_video_url(csrc):
                continue
            ckey = _media_cluster_key_from_src(csrc)
            cmid = _fb_media_numeric_id_str_from_src(csrc)
            if (cluster_key and ckey == cluster_key) or (manifest_set and cmid in manifest_set) or (not cluster_key and not manifest_set):
                clean_candidates.append(cand)

        if not clean_candidates and src:
            clean_candidates = [{
                "type": pack.get("type") or _media_type_from_url(src),
                "src": src,
                "score": pack.get("score", 0),
            }]

        p2 = dict(pack)
        p2["candidates"] = clean_candidates
        primary = _media_key_from_src(src)
        if primary and primary in seen_primary:
            continue
        if primary:
            seen_primary.add(primary)
        out.append(p2)

    return out


def _sort_items_by_manifest_then_media_id(packs: list[dict], manifest_ids: list[str] | None = None) -> list[dict]:
    """
    v11.20 final order:
    Use the pack primary src for manifest matching. Do NOT let a stale candidate inside
    another pack make that pack occupy a manifest slot; this was why the image that
    belongs around #6 could be emitted as #2.
    """
    manifest_ids = manifest_ids or []
    manifest_pos = {mid: i for i, mid in enumerate(manifest_ids)}
    in_manifest = []
    rest = []
    for original_i, pack in enumerate(packs or []):
        primary_mid = _fb_media_numeric_id_str_from_src(pack.get("src", ""))
        if primary_mid and primary_mid in manifest_pos:
            in_manifest.append((manifest_pos[primary_mid], original_i, pack))
        else:
            rest.append((_fb_media_numeric_id_from_src(pack.get("src") or ""), original_i, pack))

    in_manifest.sort(key=lambda x: (x[0], x[1]))
    rest.sort(key=lambda x: (x[0] == 0, x[0], x[1]))
    logger.info("FB v11.21 manifest/order sort applied by primary media id only")
    return [p for _, _, p in in_manifest] + [p for _, _, p in rest]

def _get_post_folder_name(page) -> str:
    """
    v11.18 Title Safety:
    Prefer the actual FB post body as the folder title, convert Simplified -> Traditional,
    strip browser notification counts like "(11)", remove "- story halaman tv" suffixes,
    and keep a Windows-safe folder name.
    """
    selectors = [
        'div[data-ad-preview="message"]',
        'div[role="article"] div[data-ad-preview="message"]',
        'div[role="article"] div[dir="auto"]',
        'div[role="main"] div[data-ad-preview="message"]',
        'meta[property="og:description"]',
        'meta[property="og:title"]',
    ]
    candidates = []
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() <= 0:
                continue
            if sel.startswith("meta"):
                val = loc.get_attribute("content") or ""
            else:
                val = loc.inner_text(timeout=1600) or ""
            lines = [x.strip() for x in str(val).splitlines() if x.strip()]
            for line in lines[:4] or [str(val)]:
                line = " ".join(line.split()).strip()
                if len(line) >= 4:
                    candidates.append(line)
        except Exception:
            continue

    try:
        pt = page.title() or ""
        if pt:
            candidates.append(pt)
    except Exception:
        pass

    for raw in candidates:
        clean = _clean_fb_post_title_for_path(raw, fallback="")
        if clean:
            logger.info(f"FB post title folder={clean}")
            return clean

    fallback_title = _clean_fb_post_title_for_path(_get_fb_title(page), fallback="Facebook_Post")
    logger.info(f"FB post title folder fallback={fallback_title}")
    return fallback_title



def _get_post_folder_name_for_pcb(page, pcb_key: str, fallback: str = "Facebook_Post") -> str:
    """
    v11.20.1 scoped title fix:
    Anchor to the visible media grid for set=pcb.<id>, then pick the nearest meaningful
    text directly above that grid.

    Key changes vs v11.20:
    - limit vertical search window to 260px above the target media grid
    - prefer post text containing likely caption terms such as 明天 / 新娘 / 嫂子
    - avoid using stale neighboring feed article text as the folder name
    """
    pcb = ""
    try:
        m = re.search(r"pcb:?([0-9]{8,})", pcb_key or "", flags=re.I)
        pcb = m.group(1) if m else ""
    except Exception:
        pcb = ""

    candidates = []
    if pcb:
        js = r"""
        (pcb) => {
          const anchors = Array.from(document.querySelectorAll('a[href]'))
            .filter(a => (a.href || '').includes('set=pcb.' + pcb));
          if (!anchors.length) return [];

          const boxes = anchors.map(a => {
            const r = a.getBoundingClientRect();
            return {a, r, area: Math.max(0, r.width) * Math.max(0, r.height)};
          }).filter(x => x.area > 800 && x.r.top > -300 && x.r.top < window.innerHeight + 1200);
          const use = boxes.length ? boxes : anchors.map(a => ({a, r: a.getBoundingClientRect(), area: 0}));

          let mediaTop = Infinity, mediaLeft = Infinity, mediaRight = -Infinity;
          for (const x of use) {
            mediaTop = Math.min(mediaTop, x.r.top);
            mediaLeft = Math.min(mediaLeft, x.r.left);
            mediaRight = Math.max(mediaRight, x.r.right);
          }
          if (!Number.isFinite(mediaTop)) mediaTop = window.innerHeight * 0.45;
          if (!Number.isFinite(mediaLeft)) mediaLeft = 0;
          if (!Number.isFinite(mediaRight) || mediaRight < mediaLeft) mediaRight = window.innerWidth;

          const badTexts = new Set(['story halaman tv', '讚', '留言', '分享', 'like', 'comment', 'share', '查看更多']);
          function cleanText(t) {
            if (!t) return '';
            t = String(t).replace(/\s+/g, ' ').trim();
            t = t.replace(/^\(\d+\)\s*/, '').trim();
            return t;
          }
          function isBad(t) {
            const low = t.toLowerCase();
            if (badTexts.has(low)) return true;
            if (/^\d+月\d+日/.test(t)) return true;
            if (t.length < 4 || t.length > 180) return true;
            if (low.includes('story halaman tv')) return true;
            if (low.includes('facebook')) return true;
            return false;
          }

          const nodes = Array.from(document.querySelectorAll('div[data-ad-preview="message"], div[dir="auto"], span[dir="auto"]'));
          const scored = [];
          for (const el of nodes) {
            const r = el.getBoundingClientRect();
            if (r.width < 20 || r.height < 8) continue;
            if (r.bottom > mediaTop - 4) continue;
            // v11.20.1: keep only text close to the target grid.
            if (r.bottom < mediaTop - 260) continue;

            const overlap = Math.max(0, Math.min(r.right, mediaRight + 80) - Math.max(r.left, mediaLeft - 80));
            if (overlap < Math.min(120, Math.max(40, (mediaRight - mediaLeft) * 0.15))) continue;

            const txt = cleanText(el.innerText || el.textContent || '');
            if (isBad(txt)) continue;

            const dist = mediaTop - r.bottom;
            // IMPORTANT: use grouped words, not a character class like /[新娘|嫂子|明天]/.
            const hasCaptionHint = /(明天|新娘|嫂子|别人的新娘|別人的新娘|声嫂子|聲嫂子)/.test(txt);
            const captionBonus = hasCaptionHint ? -240 : (/[嗎吗？?🥹😭😂🤣❤]/.test(txt) ? -80 : 0);
            scored.push({text: txt, score: dist + captionBonus, dist, hint: hasCaptionHint});
          }
          scored.sort((a, b) => {
            if (a.hint && !b.hint) return -1;
            if (!a.hint && b.hint) return 1;
            return a.score - b.score;
          });
          return scored.map(x => x.text).slice(0, 10);
        }
        """
        try:
            raw_items = page.evaluate(js, pcb) or []
            candidates.extend(raw_items)
        except Exception:
            pass

    preferred = []
    normal = []
    for raw in candidates:
        clean = _clean_fb_post_title_for_path(raw, fallback="")
        if not clean:
            continue
        low = clean.lower()
        if low in {"story halaman tv", "facebook", "facebook_post"}:
            continue
        if len(clean) < 6:
            continue
        if re.search(r"明天|新娘|嫂子|別人的新娘|别人的新娘|聲嫂子|声嫂子", clean):
            preferred.append(clean)
        else:
            normal.append(clean)

    for clean in preferred + normal:
        logger.info(f"FB post title folder scoped={clean}")
        return clean

    fallback_clean = _clean_fb_post_title_for_path(fallback or _get_fb_title(page), fallback="Facebook_Post")
    logger.info(f"FB post title folder scoped fallback={fallback_clean}")
    return fallback_clean

def _pack_best_media_id_str(pack: dict) -> str:
    """Return stable CDN media id from a pack or its candidates."""
    if not pack:
        return ""
    for src in [pack.get("src", "")] + [(c.get("src") or "") for c in (pack.get("candidates") or [])]:
        mid = _fb_media_numeric_id_str_from_src(src)
        if mid:
            return mid
    return ""


def _dedupe_items_by_media_id(packs: list[dict]) -> list[dict]:
    """
    v11.19 pre-boundary dedupe:
    FB often serves the same image with different oh= URLs. If we crop to expected_count
    before removing these duplicate media IDs, later real photos are pushed out and the
    final output becomes 12/16. Deduplicate by the stable CDN media id before bounding.
    """
    out = []
    seen = set()
    for pack in packs or []:
        mid = _pack_best_media_id_str(pack)
        if mid:
            if mid in seen:
                logger.info(f"FB v11.19 pre-boundary duplicate media id skipped={mid}")
                continue
            seen.add(mid)
        out.append(pack)
    return out

def _drop_video_packs_for_photo_post(packs: list[dict]) -> list[dict]:
    """In a photo post target, never let video/ad/reel responses count as missing photos."""
    out = []
    for pack in packs or []:
        src = pack.get("src") or ""
        if pack.get("type") == "video" or _is_probably_video_url(src):
            logger.info(f"FB v11.16 drop video/non-photo candidate: {os.path.basename(urlparse(str(src).split('?')[0]).path)}")
            continue
        out.append(pack)
    return out

def _dominant_media_cluster(packs: list[dict] | None, *, min_count: int = 3) -> str:
    counts = {}
    order = []
    for pack in packs or []:
        key = _pack_media_cluster_key(pack)
        if not key:
            continue
        if key not in counts:
            counts[key] = 0
            order.append(key)
        counts[key] += 1
    if not counts:
        return ""
    best = sorted(order, key=lambda k: (-counts[k], order.index(k)))[0]
    return best if counts.get(best, 0) >= min_count else ""


def _filter_items_by_media_cluster(packs: list[dict], cluster_key: str) -> list[dict]:
    if not cluster_key:
        return packs or []
    out = []
    seen_primary = set()
    for pack in packs or []:
        src = pack.get("src") or ""
        key = _media_cluster_key_from_src(src)
        if key != cluster_key:
            continue
        if pack.get("type") == "video" or _is_probably_video_url(src):
            continue

        # v11.15: also sanitize candidate list, otherwise stale/off-cluster candidates can
        # still win in download stage or force duplicate hashes.
        clean_candidates = []
        for cand in pack.get("candidates") or []:
            csrc = cand.get("src") or ""
            if _media_cluster_key_from_src(csrc) == cluster_key and not _is_probably_video_url(csrc):
                clean_candidates.append(cand)

        if not clean_candidates and src:
            clean_candidates = [{
                "type": pack.get("type") or _media_type_from_url(src),
                "src": src,
                "score": pack.get("score", 0),
            }]

        p2 = dict(pack)
        p2["candidates"] = clean_candidates
        primary = _media_key_from_src(src)
        if primary and primary in seen_primary:
            continue
        if primary:
            seen_primary.add(primary)
        out.append(p2)
    return out

def _estimate_expected_photo_count(page, ordered_links: list[str] | None, ordered_grid_items: list[dict] | None, plus_count: int = 0) -> int:
    """
    推估本貼文應下載張數。
    - 優先使用 +N：grid 5 格、最後一格 +12 => 4 + 12 = 16。
    - 若沒有 +N，才退回 true photo links / ordered links 推估。
    """
    try:
        grid_count = len(ordered_grid_items or [])
        link_count = len(ordered_links or [])

        if plus_count:
            # v11.11 Sequential Harmony:
            # FB +N overlay means "visible tiles before overlay + hidden N".
            # In the common 5-grid layout, +12 means 4 visible + 12 hidden = 16.
            # Do NOT use global grid_count here because logged-in home/feed/sidebar tiles can pollute it.
            return max(1, int(plus_count) + 4)

        # 有些 FB DOM 會吐出 17 條，其中後面很多是同一個 permalink 入口；
        # ordered_links 只能當上限參考，不可直接視為真照片數。
        true_count = 0
        for link in ordered_links or []:
            try:
                if _is_true_photo_link(link):
                    true_count += 1
            except Exception:
                pass

        return max(true_count, grid_count, min(link_count, _MAX_FB_ITEMS))
    except Exception:
        return 0


def _enter_single_viewer_from_current_dialog(page, *, reason: str = "dialog") -> bool:
    """
    v11.8 Deep Harvest:
    點 +N 後，FB 常只開「多圖 grid dialog」，不是單張劇場模式。
    這裡會在目前 dialog/document 中找可點擊的主圖 tile，再點一次進入真正 viewer。
    """
    js = r"""
    () => {
      const root = document.querySelector('div[role="dialog"]') || document;
      const W = window.innerWidth || 1600;
      const H = window.innerHeight || 1000;

      function bad(src) {
        const low = (src || '').toLowerCase();
        const badList = [
          'static.xx.fbcdn.net', '/rsrc.php', 'profile_pic', 'sprite', 'emoji',
          'icon', 'logo', 'favicon', 'safe_image.php', 'hads-ak', 'ads',
          'p32x32', 's32x32', 's40x40', 's50x50', 's64x64', 'p64x64',
          'q=40', 'q=50', 'q=60'
        ];
        if (!(low.includes('scontent') || low.includes('fbcdn.net'))) return true;
        return badList.some(x => low.includes(x));
      }

      const imgs = Array.from(root.querySelectorAll('img'));
      const candidates = [];

      for (const img of imgs) {
        const src = (img.currentSrc || img.src || img.getAttribute('src') || '').trim();
        if (bad(src)) continue;

        const r = img.getBoundingClientRect();
        const style = window.getComputedStyle(img);
        if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') <= 0) continue;
        if (r.width < 90 || r.height < 90) continue;
        if (r.right < 0 || r.bottom < 0 || r.left > W || r.top > H) continue;

        const clicker = img.closest('a[href], div[role="button"], button, [tabindex]') || img;
        const cr = clicker.getBoundingClientRect();
        candidates.push({
          el: clicker,
          img,
          top: r.top,
          left: r.left,
          area: r.width * r.height,
          cx: cr.left + cr.width / 2,
          cy: cr.top + cr.height / 2
        });
      }

      if (!candidates.length) return '';

      // 若已經是單張 viewer，最大圖通常佔據大半個畫面；此時不用再點。
      candidates.sort((a, b) => b.area - a.area);
      const biggest = candidates[0];
      if (biggest.area > W * H * 0.28 && candidates.length <= 3) return 'already-single';

      // grid 模式：依照 top/left 點第一張可見圖。
      candidates.sort((a, b) => {
        if (Math.abs(a.top - b.top) > 12) return a.top - b.top;
        return a.left - b.left;
      });

      const target = candidates[0];
      try {
        target.el.click();
        return 'clicked';
      } catch (e) {
        try {
          target.img.click();
          return 'clicked-img';
        } catch (e2) {
          return '';
        }
      }
    }
    """
    try:
        result = page.evaluate(js) or ""
        if result and result != "already-single":
            logger.info(f"FB viewer {reason}: grid/dialog 轉入單張 viewer ({result})")
            page.wait_for_timeout(2500)
        return bool(result)
    except Exception:
        return False


def _is_single_viewer_open(page) -> bool:
    try:
        cands = _strict_visible_photo_candidates(page, prefer_dialog=True)
        if not cands:
            return False
        vp = page.viewport_size or {"width": 1600, "height": 1000}
        area = float(vp.get("width", 1600)) * float(vp.get("height", 1000))
        best_area = float(cands[0].get("area") or 0)
        return best_area > area * 0.20
    except Exception:
        return False


def _collect_fb_photo_links(page):
    """
    依照畫面 grid / DOM 順序收集 photo links。

    重點：
    - 先用畫面 top, left 排序，保留可見順序。
    - 沒有座標的 hidden links 放後面。
    - 不在這裡下載，這裡只負責順序。
    """
    js = """
    () => {
      const records = [];
      const anchors = Array.from(document.querySelectorAll('a[href]'));

      let index = 0;

      for (const a of anchors) {
        index += 1;

        const href = a.href || '';
        const low = href.toLowerCase();
        const img = a.querySelector('img');

        const looksPhoto =
          low.includes('/photo') ||
          low.includes('fbid=') ||
          low.includes('set=') ||
          low.includes('/photos/') ||
          low.includes('story_fbid=');

        if (!looksPhoto) continue;

        const r = a.getBoundingClientRect();
        const imgR = img ? img.getBoundingClientRect() : r;

        const width = imgR.width || r.width || 0;
        const height = imgR.height || r.height || 0;
        const area = width * height;

        records.push({
          href,
          index,
          top: imgR.top || r.top || 999999,
          left: imgR.left || r.left || 999999,
          area
        });
      }

      records.sort((a, b) => {
        const aVisible = a.area > 1000 && a.top < 999999;
        const bVisible = b.area > 1000 && b.top < 999999;

        if (aVisible && bVisible) {
          if (Math.abs(a.top - b.top) > 12) return a.top - b.top;
          return a.left - b.left;
        }

        if (aVisible && !bVisible) return -1;
        if (!aVisible && bVisible) return 1;

        return a.index - b.index;
      });

      return records.map(x => x.href);
    }
    """

    try:
        raw = page.evaluate(js) or []
    except Exception:
        raw = []

    out = []
    seen = set()

    for u in raw:
        if not u:
            continue

        clean = u.split("&__cft__")[0].split("&__tn__")[0]

        if "facebook.com" not in clean:
            continue

        if clean in seen:
            continue

        seen.add(clean)
        out.append(clean)

    return out



def _collect_fb_grid_items(page):
    """
    從原貼文 grid 直接收集「畫面順序 + 縮圖候選 + photo link」。

    作用：
    - FB 多圖有時 photo page / og:image 會回同一張封面，導致 16 張被去重成 7 或 1。
    - grid 畫面本身通常已經有正確的 16 張縮圖順序。
    - 先把 grid 的 href + img src 記下來，後面逐張開 photo link 抓高清；
      若 photo page 抓到重複圖，就用 grid 圖當 fallback，確保內容張數與順序正確。
    """
    js = """
    () => {
      const records = [];
      const anchors = Array.from(document.querySelectorAll('a[href]'));
      let index = 0;

      function bad(low) {
        const badList = [
          'static.xx.fbcdn.net', '/rsrc.php', 'profile_pic', 'sprite', 'emoji',
          'icon', 'logo', 'favicon', 'safe_image.php', 'hads-ak', 'ads',
          'p32x32', 's32x32', 's40x40', 's50x50', 's64x64', 'p64x64'
        ];
        return badList.some(x => low.includes(x));
      }

      function pushSrc(arr, src, score) {
        if (!src) return;
        const low = src.toLowerCase();
        if (bad(low)) return;
        if (!(low.includes('scontent') || low.includes('fbcdn.net') || low.includes('video'))) return;
        arr.push({
          src,
          type: low.includes('.mp4') || low.includes('video') ? 'video' : 'image',
          score
        });
      }

      for (const a of anchors) {
        index += 1;

        const href = a.href || '';
        const lowHref = href.toLowerCase();
        const img = a.querySelector('img');

        const looksPhoto =
          lowHref.includes('/photo') ||
          lowHref.includes('fbid=') ||
          lowHref.includes('set=') ||
          lowHref.includes('/photos/') ||
          lowHref.includes('story_fbid=');

        if (!looksPhoto || !img) continue;

        const r = a.getBoundingClientRect();
        const imgR = img.getBoundingClientRect();
        const width = imgR.width || r.width || 0;
        const height = imgR.height || r.height || 0;
        const area = width * height;

        const srcs = [];
        const naturalW = img.naturalWidth || 0;
        const naturalH = img.naturalHeight || 0;
        const naturalArea = naturalW * naturalH;
        const baseScore = 1600000 + area + Math.floor(naturalArea / 2);

        pushSrc(srcs, (img.currentSrc || '').trim(), baseScore + 50000);
        pushSrc(srcs, (img.src || '').trim(), baseScore + 40000);
        pushSrc(srcs, (img.getAttribute('src') || '').trim(), baseScore + 30000);

        const srcset = img.getAttribute('srcset') || '';
        if (srcset) {
          const parts = srcset.split(',').map(x => x.trim()).filter(Boolean);
          for (const part of parts) {
            const u = part.split(/\\s+/)[0];
            pushSrc(srcs, u, baseScore + 60000);
          }
        }

        if (!srcs.length) continue;

        records.push({
          href,
          index,
          top: imgR.top || r.top || 999999,
          left: imgR.left || r.left || 999999,
          area,
          srcs
        });
      }

      records.sort((a, b) => {
        const aVisible = a.area > 1000 && a.top < 999999;
        const bVisible = b.area > 1000 && b.top < 999999;

        if (aVisible && bVisible) {
          if (Math.abs(a.top - b.top) > 12) return a.top - b.top;
          return a.left - b.left;
        }

        if (aVisible && !bVisible) return -1;
        if (!aVisible && bVisible) return 1;
        return a.index - b.index;
      });

      return records;
    }
    """

    try:
        raw = page.evaluate(js) or []
    except Exception:
        raw = []

    out = []
    seen_href = set()

    for rec in raw:
        href = rec.get("href") or ""

        if not href or "facebook.com" not in href:
            continue

        clean_href = (
            href.split("&__cft__")[0]
            .split("&__tn__")[0]
            .split("&comment_id=")[0]
            .split("?locale=")[0]
        )

        if clean_href in seen_href:
            continue

        candidates = []
        for item in rec.get("srcs") or []:
            src = item.get("src") or ""
            if _looks_like_real_fb_media_url(src):
                candidates.append({
                    "type": item.get("type") or _media_type_from_url(src),
                    "src": src,
                    "score": int(item.get("score") or 0) + _media_quality_score(src),
                })

        candidates = _dedupe_ordered(candidates)

        if not candidates:
            continue

        seen_href.add(clean_href)
        out.append({
            "href": clean_href,
            "candidates": candidates,
        })

    return out


def _collect_grid_tile_records_spatial(page, pcb_key=None, expected_count=None):
    """
    v11.22 Grid Tile Mode:
    Extract visible/photo tile links from the +N grid/dialog and sort by physical position.
    This avoids Facebook Theater Viewer skipping one tile around index 10.
    """
    pcb = ""
    try:
        m = re.search(r"pcb:?([0-9]{8,})", pcb_key or "", flags=re.I)
        pcb = m.group(1) if m else ""
    except Exception:
        pcb = ""

    js = """
    ({pcb, expected}) => {
      const out = [];
      const seen = new Set();

      function badUrl(low) {
        return low.includes('profile.php') || low.includes('/profile/') ||
               low.includes('comment_id=') || low.includes('reply_comment_id=') ||
               low.includes('static.xx.fbcdn.net') || low.includes('/rsrc.php') ||
               low.includes('emoji') || low.includes('safe_image.php');
      }
      function pushSrc(arr, src, score) {
        if (!src) return;
        const low = String(src).toLowerCase();
        if (!(low.includes('scontent') || low.includes('fbcdn.net'))) return;
        if (badUrl(low)) return;
        arr.push({src, type: 'image', score});
      }
      function closestScroller(el) {
        let cur = el;
        while (cur && cur !== document.body) {
          try {
            if (cur.scrollHeight && cur.clientHeight && cur.scrollHeight > cur.clientHeight + 80) return cur;
          } catch(e) {}
          cur = cur.parentElement;
        }
        return document.scrollingElement || document.documentElement;
      }
      function collectAt(pass) {
        const anchors = Array.from(document.querySelectorAll('a[href]'));
        for (let i = 0; i < anchors.length; i++) {
          const a = anchors[i];
          const href = a.href || '';
          const low = href.toLowerCase();
          if (!(low.includes('/photo') || low.includes('fbid=') || low.includes('set=') || low.includes('/photos/'))) continue;
          if (badUrl(low)) continue;

          const img = a.querySelector('img');
          const ar = a.getBoundingClientRect();
          const ir = img ? img.getBoundingClientRect() : ar;
          const w = ir.width || ar.width || 0;
          const h = ir.height || ar.height || 0;
          const area = w * h;
          if (area < 900) continue;
          if (ir.bottom < -80 || ir.top > window.innerHeight + 1400) continue;

          let pcbPenalty = 0;
          if (pcb && !href.includes('set=pcb.' + pcb)) pcbPenalty = 1000000;

          const scroller = closestScroller(a);
          let scrollTop = 0;
          try { scrollTop = scroller ? scroller.scrollTop : 0; } catch(e) {}
          const virtualY = (ir.top || ar.top || 0) + scrollTop;
          const x = ir.left || ar.left || 0;
          const key = href.split('&__cft__')[0].split('&__tn__')[0].split('&comment_id=')[0] + '|' + Math.round(virtualY) + '|' + Math.round(x);
          if (seen.has(key)) continue;
          seen.add(key);

          const srcs = [];
          const naturalArea = img ? ((img.naturalWidth || 0) * (img.naturalHeight || 0)) : 0;
          const baseScore = 1200000 + area + Math.floor(naturalArea / 2);
          if (img) {
            pushSrc(srcs, img.currentSrc || '', baseScore + 50000);
            pushSrc(srcs, img.src || '', baseScore + 40000);
            pushSrc(srcs, img.getAttribute('src') || '', baseScore + 30000);
            const srcset = img.getAttribute('srcset') || '';
            for (const part of srcset.split(',').map(x => x.trim()).filter(Boolean)) {
              pushSrc(srcs, part.split(/\\s+/)[0], baseScore + 60000);
            }
          }

          out.push({href, x, y: virtualY, area, pass, pcbPenalty, srcs});
        }
      }

      collectAt(0);

      const scrollers = Array.from(document.querySelectorAll('div[role="dialog"], div[aria-modal="true"], div'))
        .filter(el => {
          try { return el.scrollHeight > el.clientHeight + 120 && el.clientHeight > 150; } catch(e) { return false; }
        })
        .sort((a, b) => (b.clientHeight * b.clientWidth) - (a.clientHeight * a.clientWidth));

      const targets = scrollers.slice(0, 3);
      for (let s = 0; s < targets.length; s++) {
        const el = targets[s];
        const max = Math.min(el.scrollHeight - el.clientHeight, 5000);
        const step = Math.max(220, Math.floor(el.clientHeight * 0.75));
        for (let pos = 0; pos <= max; pos += step) {
          try { el.scrollTop = pos; } catch(e) {}
          collectAt(10 + s);
          if (expected && out.length >= expected + 4) break;
        }
        try { el.scrollTop = 0; } catch(e) {}
      }

      out.sort((a,b) => {
        if (a.pcbPenalty !== b.pcbPenalty) return a.pcbPenalty - b.pcbPenalty;
        const ay = Math.round(a.y / 18) * 18;
        const by = Math.round(b.y / 18) * 18;
        if (Math.abs(ay - by) > 18) return ay - by;
        return a.x - b.x;
      });

      return out.slice(0, expected ? Math.max(expected + 6, 24) : 40);
    }
    """
    try:
        raw = page.evaluate(js, {"pcb": pcb, "expected": expected_count or 0}) or []
    except Exception as e:
        logger.warning(f"FB v11.22 grid tile JS collect failed: {e}")
        raw = []

    records = []
    seen_href = set()
    seen_img = set()
    for rec in raw:
        href = (rec.get("href") or "").split("&__cft__")[0].split("&__tn__")[0].split("&comment_id=")[0]
        if not href or "facebook.com" not in href:
            continue
        candidates = []
        for item in rec.get("srcs") or []:
            src = item.get("src") or ""
            if not _looks_like_real_fb_media_url(src):
                continue
            candidates.append({
                "type": item.get("type") or _media_type_from_url(src),
                "src": src,
                "score": int(item.get("score") or 0) + _media_quality_score(src) - 20000,
            })
        candidates = _dedupe_ordered(candidates)

        img_key = ""
        if candidates:
            img_key = _media_key_from_src(candidates[0].get("src", ""))
        dedupe_key = href if href not in seen_href else img_key
        if dedupe_key and (dedupe_key in seen_href or dedupe_key in seen_img):
            continue
        if href:
            seen_href.add(href)
        if img_key:
            seen_img.add(img_key)
        records.append({
            "href": href,
            "x": rec.get("x") or 0,
            "y": rec.get("y") or 0,
            "candidates": candidates,
        })
    logger.info(f"FB v11.22 grid tile spatial records={len(records)}")
    return records


def _capture_single_grid_tile_page(context, href, index, allowed_cluster=None):
    """Open one photo link in an isolated page and capture its best high-res image candidate."""
    if not href:
        return None
    p = context.new_page()
    bucket = []
    try:
        def on_resp(resp):
            try:
                u = resp.url
                if not _looks_like_real_fb_media_url(u):
                    return
                if _media_type_from_url(u) != "image":
                    return
                ctype = ""
                try:
                    ctype = resp.headers.get("content-type", "") or ""
                except Exception:
                    ctype = ""
                if ctype and "image" not in ctype.lower():
                    return
                clen = 0
                try:
                    raw_len = resp.headers.get("content-length", "") or "0"
                    clen = int(raw_len) if str(raw_len).isdigit() else 0
                except Exception:
                    clen = 0
                if 0 < clen < _MIN_FILE_SIZE:
                    return
                score = 1700000 + _media_quality_score(u) + min(clen, 800000)
                bucket.append({"type": "image", "src": u, "score": score})
            except Exception:
                pass

        p.on("response", on_resp)
        try:
            p.goto(href, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightTimeoutError:
            pass
        p.wait_for_timeout(2500)
        try:
            p.wait_for_load_state("networkidle", timeout=3500)
        except Exception:
            pass

        candidates = _collect_current_page_candidates(
            p,
            network_items=bucket,
            include_network=True,
            include_meta=False,
            include_html=True,
        )
        if allowed_cluster:
            scoped = []
            for cand in candidates:
                src = cand.get("src") or ""
                ck = _media_cluster_key_from_src(src)
                if not ck or ck == allowed_cluster:
                    scoped.append(cand)
            if scoped:
                candidates = scoped
        candidates = _dedupe_ordered(candidates)
        if not candidates:
            logger.warning(f"FB v11.22 tile {index} no candidate: {href[:100]}")
            return None
        chosen = candidates[0]
        return {
            "order": index,
            "candidates": candidates,
            "src": chosen.get("src", ""),
            "type": chosen.get("type", "image"),
            "score": chosen.get("score", 0),
        }
    except Exception as e:
        logger.warning(f"FB v11.22 tile {index} failed: {e}")
        return None
    finally:
        try:
            p.close()
        except Exception:
            pass


def _collect_grid_tile_mode_items(context, page, pcb_key=None, expected_count=None, allowed_cluster=None):
    """
    v11.22 Grid Tile Mode:
    Use tile physical order as the source of truth. It is intentionally used only
    when Theater Viewer misses images, to avoid recommendation pollution.
    """
    records = _collect_grid_tile_records_spatial(page, pcb_key=pcb_key, expected_count=expected_count)
    if expected_count and len(records) > expected_count:
        records = records[:expected_count]
    if not records:
        return []

    out = []
    used = set()
    logger.info(f"FB v11.22 grid tile mode start: tiles={len(records)} target={expected_count or '-'}")
    for i, rec in enumerate(records, 1):
        href = rec.get("href") or ""
        pack = _capture_single_grid_tile_page(context, href, i, allowed_cluster=allowed_cluster)
        if not pack:
            cands = rec.get("candidates") or []
            if cands:
                best = cands[0]
                pack = {"order": i, "candidates": cands, "src": best.get("src", ""), "type": best.get("type", "image"), "score": best.get("score", 0) - 100000}
        if not pack:
            continue
        key = _media_key_from_src(pack.get("src", "")) or _fb_media_numeric_id_str_from_src(pack.get("src", ""))
        if key and key in used:
            logger.info(f"FB v11.22 grid tile duplicate skipped index={i}: {key}")
            continue
        if key:
            used.add(key)
        out.append(pack)
        logger.info(f"FB v11.22 grid tile captured {len(out)}/{expected_count or len(records)}: index={i}")
    return out


def _click_next_fb(page):
    """v8：強化 FB 劇場模式下一張。

    回傳 True 只代表已送出下一張動作；是否真的變圖由 caller 檢查。
    """
    try:
        page.mouse.move(900, 540)
        page.mouse.click(900, 540)
        page.wait_for_timeout(120)
    except Exception:
        pass

    selectors = [
        'div[aria-label="Next photo"]',
        'button[aria-label="Next photo"]',
        'div[aria-label="Next"]',
        'button[aria-label="Next"]',
        'div[aria-label="下一張相片"]',
        'button[aria-label="下一張相片"]',
        'div[aria-label="下一張"]',
        'button[aria-label="下一張"]',
        'div[aria-label="下一張照片"]',
        'button[aria-label="下一張照片"]',
    ]

    for sel in selectors:
        try:
            loc = page.locator(sel)
            n = loc.count()
            if n <= 0:
                continue

            best = None
            best_x = -1
            for i in range(min(n, 12)):
                try:
                    item = loc.nth(i)
                    if not item.is_visible(timeout=400):
                        continue
                    box = item.bounding_box(timeout=400)
                    if not box:
                        continue
                    if box.get("x", 0) > best_x:
                        best = item
                        best_x = box.get("x", 0)
                except Exception:
                    continue
            if best:
                best.click(timeout=2500, force=True)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            continue

    js = r"""
    () => {
      const dialog = document.querySelector('div[role="dialog"]') || document;
      const nodes = Array.from(dialog.querySelectorAll('div[role="button"], button, a[role="button"], a, [aria-label]'));
      const W = window.innerWidth || 1600;
      const H = window.innerHeight || 1000;
      let best = null;
      let bestScore = -Infinity;
      for (const b of nodes) {
        const r = b.getBoundingClientRect();
        const style = window.getComputedStyle(b);
        if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') <= 0) continue;
        if (r.width < 12 || r.height < 12) continue;
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        if (cx < W * 0.58) continue;
        if (cy < H * 0.12 || cy > H * 0.88) continue;
        const text = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
        if (text.includes('close') || text.includes('關閉') || text.includes('comment') || text.includes('留言') || text.includes('讚') || text.includes('like')) continue;
        const score = cx - Math.abs(cy - H / 2) * 2 + Math.min(r.width * r.height, 5000) / 100;
        if (score > bestScore) { best = b; bestScore = score; }
      }
      if (best) { best.click(); return true; }
      return false;
    }
    """
    try:
        if page.evaluate(js):
            page.wait_for_timeout(1000)
            return True
    except Exception:
        pass

    try:
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(1100)
        return True
    except Exception:
        pass

    for x in (1500, 1560, 1460, 1380):
        try:
            page.mouse.click(x, 540)
            page.wait_for_timeout(800)
            return True
        except Exception:
            continue

    return False

def _current_best_key(candidates):
    if not candidates:
        return ""

    src = candidates[0].get("src", "")

    if not src:
        return ""

    path = urlparse(src.split("?")[0]).path
    basename = os.path.basename(path)

    return basename or src[:180]





def _main_viewer_key(page) -> str:
    candidates = _strict_visible_photo_candidates(page, prefer_dialog=True)
    if not candidates:
        candidates = _strict_visible_photo_candidates(page, prefer_dialog=False)
    if not candidates:
        return ""
    return _media_key_from_src(candidates[0].get("src", ""))


def _wait_viewer_change(page, before_key: str, *, timeout_ms: int = 8500) -> bool:
    loops = max(1, timeout_ms // 500)
    for _ in range(loops):
        page.wait_for_timeout(500)
        now_key = _main_viewer_key(page)
        if now_key and now_key != before_key:
            return True
    return False


def _open_viewer_from_post(page) -> bool:
    """從原貼文盡量打開劇場模式。"""
    if _click_plus_overlay_or_first_photo(page):
        page.wait_for_timeout(1200)
        # v11.8：+N 常先打開 grid dialog；必須再點一次 tile 才會進單張 viewer。
        _enter_single_viewer_from_current_dialog(page, reason="post")
        return True

    js = r"""
    () => {
      const candidates = Array.from(document.querySelectorAll('a[href*="photo"], a[href*="fbid"], img'));
      let best = null;
      let bestScore = -Infinity;
      for (const el of candidates) {
        const img = el.tagName.toLowerCase() === 'img' ? el : el.querySelector('img');
        if (!img) continue;
        const src = (img.currentSrc || img.src || '').toLowerCase();
        if (!(src.includes('scontent') || src.includes('fbcdn.net'))) continue;
        const r = img.getBoundingClientRect();
        if (r.width < 120 || r.height < 120) continue;
        const score = r.width * r.height - Math.abs(r.top) * 5;
        if (score > bestScore) { best = el; bestScore = score; }
      }
      if (best) { best.click(); return true; }
      return false;
    }
    """
    try:
        if page.evaluate(js):
            page.wait_for_timeout(2500)
            return True
    except Exception:
        pass
    return False


def _open_viewer_from_photo_page(page) -> bool:
    """photo/?fbid= 頁有時已在 viewer，有時要點一次主圖。"""
    try:
        if page.locator('div[role="dialog"]').count() > 0:
            return True
    except Exception:
        pass

    js = r"""
    () => {
      const imgs = Array.from(document.querySelectorAll('img'));
      let best = null;
      let bestScore = -Infinity;
      const W = window.innerWidth || 1600;
      const H = window.innerHeight || 1000;
      for (const img of imgs) {
        const src = (img.currentSrc || img.src || '').toLowerCase();
        if (!(src.includes('scontent') || src.includes('fbcdn.net'))) continue;
        if (src.includes('profile') || src.includes('p32x32') || src.includes('s32x32') || src.includes('s200x200')) continue;
        const r = img.getBoundingClientRect();
        if (r.width < 150 || r.height < 150) continue;
        const cx = r.left + r.width / 2;
        const cy = r.top + r.height / 2;
        const score = r.width * r.height - Math.abs(cx - W/2) - Math.abs(cy - H/2);
        if (score > bestScore) { best = img; bestScore = score; }
      }
      if (best) { best.click(); return true; }
      return false;
    }
    """
    try:
        if page.evaluate(js):
            page.wait_for_timeout(2500)
            return True
    except Exception:
        pass
    return False


def _aggregate_unique_items(*seqs: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for seq in seqs:
        for pack in seq or []:
            key = _media_key_from_src(pack.get("src", ""))
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(pack)
    return out




def _fb_network_image_candidates_from_page(page, bucket: list[dict]) -> list[dict]:
    """把 viewer 翻頁期間攔截到的 FB 圖片 request/response 轉成候選。"""
    out = []
    for item in bucket or []:
        src = (item.get("src") or "").strip()
        if not src or not _looks_like_real_fb_media_url(src):
            continue
        low = src.lower()
        if _is_bad_fb_media_url(low) or _is_probable_fb_thumbnail_url(low):
            continue
        # response 攔截到的圖比 DOM 初始 src 更可信：FB 第一秒常先塞縮圖，稍後才換高清。
        score = int(item.get("score") or 0) + 2600000 + _media_quality_score(src)
        out.append({
            "type": _media_type_from_url(src),
            "src": src,
            "score": score,
            "temp_path": item.get("temp_path") or item.get("persisted_path"),
            "body_size": item.get("body_size", 0),
        })
    return _dedupe_ordered(out)


def _best_current_viewer_candidates(page, network_bucket: list[dict] | None = None) -> list[dict]:
    """
    目前 viewer 的最可信候選。

    v11 調整：response/intercept 抓到的圖優先於 DOM visible。
    原因是 FB Viewer 第一瞬間 DOM 常保留 50~80KB placeholder，
    但 network response 會較晚出現真正的大圖。
    """
    visible = _strict_visible_photo_candidates(page, prefer_dialog=True)
    if not visible:
        visible = _strict_visible_photo_candidates(page, prefer_dialog=False)

    net = _fb_network_image_candidates_from_page(page, network_bucket or [])

    # 關鍵：net 在前，visible 只當 fallback，避免第一張縮圖蓋掉高清攔截圖。
    merged = _merge_unique_candidates(net, visible)
    return sorted(merged, key=lambda x: x.get("score", 0), reverse=True)




def _viewer_media_point(page, *, side: str = "center") -> tuple[float, float]:
    """
    回傳 FB viewer 主圖片附近的安全點擊座標。

    v11.2 重點：不要固定點 viewport 0.90/0.95，因為很容易點到右側留言欄；
    也不要固定 0.66/0.72，因為不同 viewport / FB layout 下可能還在圖片中央。
    這裡先找 dialog 中最大張的 img/video，再以它的邊界計算：
    - center：圖片中心，用於搶回焦點
    - right：圖片右緣外側一點點，用於觸發 next 熱區
    """
    try:
        pt = page.evaluate(
            """
            (side) => {
              const W = window.innerWidth || 1600;
              const H = window.innerHeight || 1000;
              const root = document.querySelector('div[role="dialog"]') || document;
              const nodes = Array.from(root.querySelectorAll('img, video'));
              let best = null;
              let bestArea = 0;

              for (const el of nodes) {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const w = r.width || 0;
                const h = r.height || 0;
                if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') <= 0) continue;
                if (w < 120 || h < 120) continue;
                if (r.right < 0 || r.bottom < 0 || r.left > W || r.top > H) continue;
                const area = w * h;
                if (area > bestArea) { best = r; bestArea = area; }
              }

              if (best) {
                const cy = Math.max(H * 0.18, Math.min(H * 0.82, best.top + best.height / 2));
                if (side === 'right') {
                  // 右緣外側 35px，最多不超過 86% viewport，避免掉進留言區。
                  const x = Math.max(W * 0.56, Math.min(W * 0.86, best.right + 35));
                  return {x, y: cy, src: 'media-right'};
                }
                const x = Math.max(W * 0.22, Math.min(W * 0.72, best.left + best.width / 2));
                return {x, y: cy, src: 'media-center'};
              }

              if (side === 'right') return {x: W * 0.85, y: H * 0.50, src: 'fallback-right'};
              return {x: W * 0.45, y: H * 0.50, src: 'fallback-center'};
            }
            """,
            side,
        ) or {}
        return float(pt.get("x", 800)), float(pt.get("y", 500))
    except Exception:
        vp = page.viewport_size or {"width": 1600, "height": 1000}
        if side == "right":
            return float(vp.get("width", 1600)) * 0.85, float(vp.get("height", 1000)) * 0.5
        return float(vp.get("width", 1600)) * 0.45, float(vp.get("height", 1000)) * 0.5


def _focus_viewer(page) -> None:
    """讓鍵盤 ArrowRight/ArrowLeft 落在 viewer 主媒體上，避免焦點在留言區或背景頁。"""
    try:
        x, y = _viewer_media_point(page, side="center")
        page.mouse.move(x, y)
        page.mouse.click(x, y)
        page.wait_for_timeout(180)
    except Exception:
        pass


def _fb_jitter_wait(page, base_ms: int = 850, jitter_ms: int = 650) -> None:
    """v11.15: jitter wait to let FB Ajax/high-res image swap settle."""
    try:
        extra = random.randint(0, max(0, int(jitter_ms)))
        page.wait_for_timeout(max(50, int(base_ms) + extra))
    except Exception:
        pass


def _dispatch_arrow_right_js(page) -> None:
    """v11.15: synthesize keyboard events when Playwright key press is swallowed by comment pane."""
    try:
        page.evaluate(
            """
            () => {
              const opts = {key: 'ArrowRight', code: 'ArrowRight', keyCode: 39, which: 39, bubbles: true, cancelable: true};
              for (const type of ['keydown', 'keypress', 'keyup']) {
                document.dispatchEvent(new KeyboardEvent(type, opts));
                window.dispatchEvent(new KeyboardEvent(type, opts));
                const dlg = document.querySelector('div[role=\"dialog\"]');
                if (dlg) dlg.dispatchEvent(new KeyboardEvent(type, opts));
              }
            }
            """
        )
    except Exception:
        pass


def _click_dom_next_strong(page) -> bool:
    """v11.15 DOM-first next: prefer visible right-side Next controls over coordinates."""
    patterns = [
        re.compile(r"下一張|下一張相片|下一張照片|Next|Next photo", re.I),
    ]
    for pat in patterns:
        try:
            loc = page.get_by_label(pat)
            n = loc.count()
            best = None
            best_x = -1
            for i in range(min(n, 20)):
                try:
                    item = loc.nth(i)
                    if not item.is_visible(timeout=250):
                        continue
                    box = item.bounding_box(timeout=250)
                    if not box:
                        continue
                    if box.get('x', 0) > best_x:
                        best = item
                        best_x = box.get('x', 0)
                except Exception:
                    continue
            if best is not None:
                best.click(timeout=1200, force=True)
                _fb_jitter_wait(page, 800, 500)
                return True
        except Exception:
            pass
    return False


def _physical_drive_next(page, *, reason: str = "drive") -> None:
    """
    v11.4 Physical Drive：
    - 先嘗試 aria / DOM 下一張。
    - 再用主圖右緣、多段 viewport 熱區、鍵盤補償。
    - 加入 Y 軸多點偏移，避免固定 y=600 落在透明遮罩或無效黑邊。
    """
    try:
        # v11.15: DOM/aria 優先，成功時最穩；失敗才座標。
        try:
            if _click_dom_next_strong(page):
                return
        except Exception:
            pass

        vp = page.viewport_size or {"width": 1600, "height": 1000}
        W = float(vp.get("width", 1600))
        H = float(vp.get("height", 1000))
        base_x, base_y = _viewer_media_point(page, side="right")

        # stale 越高，越往左/右/上下試不同熱區，避開透明遮罩或留言欄。
        # v11.4：Y 不再固定 H*0.50；FB Viewer 的可點區有時跟圖片高度/黑邊有關。
        y_mid = base_y or H * 0.50
        y_low = max(H * 0.28, min(H * 0.72, y_mid + H * 0.075))
        y_high = max(H * 0.28, min(H * 0.72, y_mid - H * 0.075))

        if "secondary" in reason:
            points = [
                (W * 0.78, y_mid),
                (W * 0.84, y_low),
                (W * 0.70, y_high),
            ]
        elif "stale" in reason:
            points = [
                (W * 0.82, y_mid),
                (W * 0.76, y_low),
                (base_x, y_high),
            ]
        else:
            points = [
                (base_x, y_mid),
                (W * 0.80, y_low),
            ]

        for x, y in points[:2]:
            # v11.15: add tiny coordinate jitter to avoid hitting the same stale overlay pixel forever.
            jx = random.randint(-18, 18)
            jy = random.randint(-14, 14)
            xx = max(1, min(W - 2, x + jx))
            yy = max(1, min(H - 2, y + jy))
            logger.info(f"FB viewer physical drive next: {reason} x={int(xx)} y={int(yy)}")
            page.mouse.move(xx, yy)
            page.mouse.click(xx, yy)
            _fb_jitter_wait(page, 420, 360)

        page.keyboard.press("ArrowRight")
        _dispatch_arrow_right_js(page)
        _fb_jitter_wait(page, 1050, 750)
    except Exception:
        try:
            page.keyboard.press("ArrowRight")
            _dispatch_arrow_right_js(page)
            _fb_jitter_wait(page, 850, 450)
        except Exception:
            pass


def _warmup_viewer_highres(page, *, label: str = "viewer") -> None:
    """
    FB viewer 第一張常先顯示 50~80KB placeholder。
    進 viewer 後先等待、微動滑鼠，必要時右翻再左翻，迫使 FB 重新渲染/請求高清圖。
    """
    try:
        page.wait_for_selector('div[role="dialog"] img, img[style*="object-fit"]', timeout=6000)
    except Exception:
        pass

    _focus_viewer(page)
    try:
        logger.info(f"FB viewer {label}: high-res warmup start")
    except Exception:
        pass

    try:
        # v11.2：不等太久，避免 response bucket 被舊圖污染；先讓 UI 穩定，再做一次右/左刷新。
        page.wait_for_timeout(800)
        before = _main_viewer_key(page)

        _physical_drive_next(page, reason=f"warmup-right {label}")
        _wait_viewer_change(page, before, timeout_ms=2800)
        page.wait_for_timeout(1100)

        mid = _main_viewer_key(page)
        _focus_viewer(page)
        page.keyboard.press("ArrowLeft")
        _wait_viewer_change(page, mid, timeout_ms=2800)
        page.wait_for_timeout(750)
        _focus_viewer(page)
    except Exception:
        try:
            page.wait_for_timeout(1000)
        except Exception:
            pass


def _force_viewer_next(page, attempt: int) -> None:
    """v11.15 強力翻頁：DOM Next -> keyboard -> JS keyboard -> jitter coordinate fallback."""
    try:
        _focus_viewer(page)
        if attempt == 0:
            if not _click_dom_next_strong(page):
                page.keyboard.press("ArrowRight")
                _dispatch_arrow_right_js(page)
                _fb_jitter_wait(page, 950, 550)
        elif attempt == 1:
            page.keyboard.press("ArrowRight")
            _dispatch_arrow_right_js(page)
            _fb_jitter_wait(page, 1050, 650)
        elif attempt == 2:
            _physical_drive_next(page, reason="attempt2")
        elif attempt == 3:
            _focus_viewer(page)
            page.keyboard.press("ArrowRight")
            _fb_jitter_wait(page, 450, 250)
            _physical_drive_next(page, reason="attempt3")
        elif attempt == 4:
            _physical_drive_next(page, reason="attempt4-a")
            _fb_jitter_wait(page, 550, 450)
            _physical_drive_next(page, reason="attempt4-b")
        else:
            _focus_viewer(page)
            _dispatch_arrow_right_js(page)
            _fb_jitter_wait(page, 650, 500)
            _physical_drive_next(page, reason=f"attempt{attempt}-last")
    except Exception:
        pass

def _collect_viewer_sequence_intercept(page, *, label: str = "viewer", target_count: int | None = None, stale_threshold: int = 3, max_turns: int | None = None, allowed_cluster: str | None = None) -> list[dict]:
    """
    v11：Viewer 物理翻頁 + response 快速收割模式。

    v10 的問題是太依賴 current_src / viewer key 是否變化；FB 有時圖片已載入，
    但 URL/key 不變或 focus 被留言區吃掉，造成 stale 循環。

    v11 原則：
    - 只要 response/intercept bucket 或可見主圖出現未收過的候選，就先收。
    - 後續下載階段會用 _download_best_candidate 以實際檔案大小挑最大圖。
    - stale 只用來決定何時停止，不再阻擋收割。
    """
    collected: list[dict] = []
    seen_keys: set[str] = set()
    first_key = ""
    stale = 0
    network_bucket: list[dict] = []

    def on_response(resp):
        try:
            u = resp.url or ""
            low = u.lower()

            if not _looks_like_real_fb_media_url(u):
                return
            if _is_bad_fb_media_url(low) or _is_probable_fb_thumbnail_url(low):
                return

            ctype = ""
            clen = 0
            try:
                ctype = resp.headers.get("content-type", "") or ""
                raw_len = resp.headers.get("content-length", "") or "0"
                clen = int(raw_len) if str(raw_len).isdigit() else 0
            except Exception:
                pass

            # v11.10：明確過小的 response 直接略過，避免 994 bytes placeholder / broken image 污染候選。
            if 0 < clen < _MIN_FILE_SIZE:
                logger.debug(f"FB ignore tiny image response: {clen} bytes | {u[:100]}")
                return

            # 明確圖片 response 才大幅加權；content-length 太小不直接丟，避免 header 缺失，
            # 但降低權重，最後仍會由實際下載大小決定。
            score = 2800000 + _media_quality_score(u)
            if "image" in ctype:
                score += 280000
            if clen >= 80 * 1024:
                score += min(clen, 900000)
            elif 0 < clen < 50 * 1024:
                score -= 600000

            if any(x in low for x in ["s1080", "s1440", "s2048", "p1080", "p1440", "p2048"]):
                score += 350000

            # v11.3 Solid Write：response 一到就盡量落盤，後續下載階段可直接 copy。
            persisted_path = None
            body_size = 0
            try:
                body = resp.body()
                body_size = len(body) if body else 0
                if body_size >= _MIN_FILE_SIZE:
                    capture_dir = os.path.join(TEMP_DIR, "_fb_capture")
                    os.makedirs(capture_dir, exist_ok=True)
                    h = hashlib.md5(body).hexdigest()[:16]
                    ext = _ext_from_url(u, ".jpg")
                    persisted_path = os.path.join(capture_dir, f"cap_{h}{ext}")
                    if not os.path.exists(persisted_path) or os.path.getsize(persisted_path) < body_size:
                        with open(persisted_path, "wb") as f:
                            f.write(body)
                            f.flush()
                            try:
                                os.fsync(f.fileno())
                            except Exception:
                                pass
                    score += min(body_size, 1200000)
                    logger.info(
                        f"FB response 實體落盤: {os.path.basename(persisted_path)} "
                        f"({body_size // 1024} KB)"
                    )
            except Exception:
                pass

            network_bucket.append({
                "type": _media_type_from_url(u),
                "src": u,
                "score": score,
                "content_length": clen,
                "body_size": body_size,
                "temp_path": persisted_path,
            })
            if len(network_bucket) > 180:
                del network_bucket[:90]
        except Exception:
            pass

    try:
        page.on("response", on_response)
    except Exception:
        pass

    def _force_persist_harvest_candidate(cand: dict, *, reason: str, order_no: int) -> None:
        """
        v11.7 Force Persist:
        harvest_once 一旦宣告「收集」，立刻把該候選實體化。
        - 優先沿用 response 已落盤 temp_path。
        - 若只有 DOM src，立即用 Playwright request 下載一次到 TEMP_DIR/_fb_capture。
        - 同步鏡像一份到 DOWNLOAD_DIR/_fb_debug_capture，方便確認「有抓到但搬運失敗」或「根本沒抓到」。
        主流程仍會走 _download_best_candidate() + move_files(title)，不破壞正式命名。
        """
        try:
            existing = cand.get("temp_path") or cand.get("persisted_path")
            if existing and os.path.exists(existing) and os.path.getsize(existing) >= _MIN_FILE_SIZE:
                if FB_DEBUG_CAPTURE:
                    try:
                        debug_dir = os.path.join(DOWNLOAD_DIR, "_fb_debug_capture")
                        os.makedirs(debug_dir, exist_ok=True)
                        ext0 = os.path.splitext(existing)[1] or ".jpg"
                        debug_name = f"debug_{safe_title(label)[:24]}_{order_no:04d}_{hashlib.md5(existing.encode('utf-8')).hexdigest()[:8]}{ext0}"
                        shutil.copy2(existing, os.path.join(debug_dir, debug_name))
                        logger.info(f"FB direct debug mirror: {debug_name} ({os.path.getsize(existing) // 1024} KB)")
                    except Exception:
                        pass
                return

            src = (cand.get("src") or "").strip()
            if not src or not _looks_like_real_fb_media_url(src):
                return
            if _is_bad_fb_media_url(src.lower()) or _is_probable_fb_thumbnail_url(src.lower()):
                return

            resp = page.context.request.get(
                src,
                headers={
                    "Referer": page.url,
                    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
                timeout=60000,
            )
            if not resp.ok:
                logger.warning(f"FB harvest 實體化失敗 HTTP {resp.status}: {src[:120]}")
                return

            body = resp.body()
            body_size = len(body) if body else 0
            if body_size < _MIN_FILE_SIZE:
                logger.warning(f"FB harvest 實體化檔案過小: {body_size} bytes | {src[:120]}")
                return

            h = hashlib.md5(body).hexdigest()[:16]
            ext = _ext_from_url(src, ".jpg")
            capture_dir = os.path.join(TEMP_DIR, "_fb_capture")
            os.makedirs(capture_dir, exist_ok=True)
            persisted_path = os.path.join(capture_dir, f"harvest_{safe_title(label)[:24]}_{order_no:04d}_{h}{ext}")
            with open(persisted_path, "wb") as f:
                f.write(body)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass

            cand["temp_path"] = persisted_path
            cand["persisted_path"] = persisted_path
            cand["body_size"] = body_size
            cand["content_length"] = max(int(cand.get("content_length") or 0), body_size)
            cand["score"] = int(cand.get("score") or 0) + min(body_size, 1200000)

            logger.info(f"FB harvest 實體化: {os.path.basename(persisted_path)} ({body_size // 1024} KB) reason={reason}")

            if FB_DEBUG_CAPTURE:
                try:
                    debug_dir = os.path.join(DOWNLOAD_DIR, "_fb_debug_capture")
                    os.makedirs(debug_dir, exist_ok=True)
                    debug_name = f"debug_{safe_title(label)[:24]}_{order_no:04d}_{h}{ext}"
                    shutil.copy2(persisted_path, os.path.join(debug_dir, debug_name))
                    logger.info(f"FB direct debug mirror: {debug_name} ({body_size // 1024} KB)")
                except Exception as e:
                    logger.warning(f"FB direct debug mirror 失敗: {e}")

        except Exception as e:
            logger.warning(f"FB harvest 實體化例外: {e}")

    def harvest_once(reason: str) -> bool:
        nonlocal first_key, stale
        candidates = _best_current_viewer_candidates(page, network_bucket)
        if not candidates:
            return False

        # 這裡不要只看 candidates[0]；FB 可能第一候選是舊 placeholder，
        # 往後找第一個未收過的真圖。
        chosen = None
        chosen_key = ""
        for cand in candidates[:10]:
            key = _media_key_from_src(cand.get("src", ""))
            if key and key not in seen_keys:
                chosen = cand
                chosen_key = key
                break

        if not chosen or not chosen_key:
            return False

        # v11.15: Do not count off-post recommendation/media as harvested.
        # This prevents polluted media from satisfying target_count and ending the main viewer early.
        if allowed_cluster:
            chosen_cluster = _media_cluster_key_from_src(chosen.get("src", ""))
            if chosen_cluster and chosen_cluster != allowed_cluster:
                logger.info(
                    f"FB viewer-intercept {label} 跳過異質 cluster: "
                    f"{chosen_cluster} != {allowed_cluster} | "
                    f"{os.path.basename(urlparse(chosen.get('src', '').split('?')[0]).path)}"
                )
                seen_keys.add(chosen_key)
                return False

        # v11.15: Post photo mode should not accept videos as completing image targets.
        if allowed_cluster and (chosen.get("type") == "video" or _is_probably_video_url(chosen.get("src", ""))):
            logger.info(f"FB viewer-intercept {label} 跳過影片候選，不計入照片目標: {chosen.get('src','')[:120]}")
            seen_keys.add(chosen_key)
            return False

        if not first_key:
            first_key = chosen_key

        seen_keys.add(chosen_key)

        # v11.5：一旦判定收集成功，立即實體化，避免「Log collected 但最後沒圖」。
        _force_persist_harvest_candidate(chosen, reason=reason, order_no=len(collected) + 1)

        # 把 chosen 拉到候選第一順位，其餘候選保留給下載階段挑最大檔。
        reordered = [chosen]
        for cand in candidates:
            if cand is chosen:
                continue
            k = _media_key_from_src(cand.get("src", ""))
            if k and k != chosen_key:
                reordered.append(cand)

        collected.append({
            "order": len(collected) + 1,
            "candidates": reordered,
            "src": chosen.get("src", ""),
            "type": chosen.get("type", "image"),
            "score": chosen.get("score", 0),
        })

        logger.info(
            f"FB viewer-intercept {label} 收集第 {len(collected)} 張: "
            f"{os.path.basename(urlparse(chosen.get('src', '').split('?')[0]).path)} "
            f"reason={reason}"
        )
        stale = 0
        return True

    _focus_viewer(page)

    # 第一張：warmup 後先快速收割一次，不再等 key 變化。
    for _ in range(8):
        page.wait_for_timeout(520)
        if harvest_once("initial"):
            break

    max_turns = int(max_turns or _MAX_FB_ITEMS)
    hard_stale_stop = max(5, int(stale_threshold) + 2)

    for turn in range(max_turns):
        if target_count and len(collected) >= target_count:
            logger.info(f"FB viewer-intercept {label}: 已達目標張數 target={target_count}")
            break
        before_count = len(collected)
        before_key = _main_viewer_key(page)

        # 翻頁前先聚焦；先用 ArrowRight，再配合熱區/按鈕。不要把 wait_viewer_change 當唯一成功條件。
        _focus_viewer(page)
        moved = False
        for attempt in range(6):
            _force_viewer_next(page, attempt)

            # 快速收割：只要 bucket 或 DOM 產生新圖就收，不要求 URL/key 一定變化後才收。
            for wait_i in range(8):
                _fb_jitter_wait(page, 420, 280)
                if harvest_once(f"turn={turn},attempt={attempt},wait={wait_i}"):
                    moved = True
                    break
            if moved:
                break

            # 沒收割到才檢查視覺 key 是否變化；若有變化，再補抓目前可見圖。
            if _wait_viewer_change(page, before_key, timeout_ms=1200):
                for wait_i in range(5):
                    page.wait_for_timeout(350)
                    if harvest_once(f"changed turn={turn},attempt={attempt},wait={wait_i}"):
                        moved = True
                        break
                if moved:
                    break

        if len(collected) == before_count:
            stale += 1
            logger.info(f"FB viewer-intercept {label} 未收割新圖 stale={stale}, turn={turn}")

            # v11.2 補償：stale=1 就啟動物理強制驅動，不等到 stale=4。
            # 先點 viewer 主圖右緣，再 ArrowRight；避免鍵盤焦點被留言區吃掉。
            try:
                _physical_drive_next(page, reason=f"stale{stale}-turn{turn}-primary")
                for wait_i in range(6):
                    page.wait_for_timeout(360)
                    if harvest_once(f"stale-physical turn={turn},wait={wait_i}"):
                        break

                if len(collected) == before_count and stale >= 2:
                    _physical_drive_next(page, reason=f"stale{stale}-turn{turn}-secondary")
                    for wait_i in range(7):
                        page.wait_for_timeout(360)
                        if harvest_once(f"stale-physical2 turn={turn},wait={wait_i}"):
                            break
            except Exception:
                pass
        else:
            stale = 0

        # v11.8 Deep Harvest：
        # 大型多圖貼文在非活動狀態會延遲噴出後段高清 URL。
        # 不再 stale=1 就跳出；至少容忍 stale_threshold，若有 target_count 則繼續深挖到 hard_stale_stop。
        if len(collected) >= 1:
            if target_count and len(collected) < target_count:
                if stale >= hard_stale_stop:
                    logger.info(
                        f"FB viewer-intercept {label}: deep harvest stale={stale}，"
                        f"仍未達 target={target_count}，停止此入口"
                    )
                    break
            else:
                if stale >= int(stale_threshold):
                    logger.info(f"FB viewer-intercept {label}: stale={stale} 達門檻，停止此入口")
                    break

        # 清除過舊 network 候選，避免很久以前的 response 被下一輪當新圖。
        if len(network_bucket) > 60:
            del network_bucket[:30]

        # 如果已經很久沒有新圖，停止這個 viewer 起點；其他起點還會繼續補。
        if stale >= hard_stale_stop:
            break

        # 偵測循環回第一張，但不要太早停；至少收 4 張後才啟用。
        after_candidates = _best_current_viewer_candidates(page, network_bucket)
        after_key = _media_key_from_src(after_candidates[0].get("src", "")) if after_candidates else ""
        if (not target_count or len(collected) >= target_count) and len(collected) >= 4 and first_key and after_key == first_key:
            logger.info(f"FB viewer-intercept {label} 偵測回到第一張，停止")
            break

    logger.info(f"FB viewer-intercept {label}: collected={len(collected)}")
    return collected

def _collect_viewer_sequence_from_url(context, start_url: str, *, label: str, is_photo_page: bool = False, target_count: int | None = None, stale_threshold: int = 3, max_turns: int | None = None, allowed_cluster: str | None = None) -> list[dict]:
    p = None
    try:
        p = context.new_page()
        p.goto(start_url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(2800)
        try:
            p.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        opened = _open_viewer_from_photo_page(p) if is_photo_page else _open_viewer_from_post(p)
        if not opened:
            logger.info(f"FB viewer start {label}: 無法開啟 viewer")
            return []

        _warmup_viewer_highres(p, label=label)
        seq = _collect_viewer_sequence_intercept(
            p,
            label=label,
            target_count=target_count,
            stale_threshold=stale_threshold,
            max_turns=max_turns,
            allowed_cluster=allowed_cluster,
        )
        logger.info(f"FB viewer start {label}: collected={len(seq)}")
        return seq
    except Exception as e:
        logger.warning(f"FB viewer start {label} 失敗: {e}")
        return []
    finally:
        try:
            if p:
                p.close()
        except Exception:
            pass

def _collect_viewer_sequence(page, network_items: list[dict] | None = None):
    """
    v8：純 viewer 物理翻頁收集。

    - 只採目前肉眼可見大圖。
    - 每次翻頁都確認主圖 key 是否改變。
    - 若按一次沒變，會再用座標/鍵盤做多次補點。
    """
    collected = []
    seen = set()
    stale_turns = 0

    for turn in range(_MAX_FB_ITEMS):
        if network_items is not None:
            network_items.clear()

        candidates = _strict_visible_photo_candidates(page, prefer_dialog=True)
        if not candidates:
            candidates = _strict_visible_photo_candidates(page, prefer_dialog=False)

        before_key = ""
        if candidates:
            before_key = _media_key_from_src(candidates[0].get("src", ""))
            if before_key and before_key not in seen:
                seen.add(before_key)
                collected.append({
                    "order": len(collected) + 1,
                    "candidates": candidates,
                    "src": candidates[0].get("src", ""),
                    "type": candidates[0].get("type", "image"),
                    "score": candidates[0].get("score", 0),
                })
                logger.info(
                    f"FB viewer 收集第 {len(collected)} 張: "
                    f"{os.path.basename(urlparse(candidates[0].get('src', '').split('?')[0]).path)}"
                )
                stale_turns = 0
            else:
                stale_turns += 1
                logger.info(f"FB viewer stale turn={stale_turns}, key={before_key}")

        changed = False
        for attempt in range(4):
            moved = _click_next_fb(page)
            if not moved:
                continue
            if _wait_viewer_change(page, before_key, timeout_ms=4500):
                changed = True
                break
            try:
                if attempt % 2 == 0:
                    page.keyboard.press("ArrowRight")
                else:
                    page.mouse.click(1530, 540)
                page.wait_for_timeout(900)
            except Exception:
                pass
            if _wait_viewer_change(page, before_key, timeout_ms=3000):
                changed = True
                break

        if not changed:
            stale_turns += 1
            logger.info(f"FB viewer 圖片未變更，stale={stale_turns}, turn={turn}")

        if stale_turns >= 5:
            break

    return collected

def _file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _pack_primary_candidates(pack: dict) -> list[dict]:
    """
    v11.14 Candidate Pinning:
    A viewer pack already has a decided primary media in pack["src"].  Older builds kept
    stale high-score network candidates in the same pack; _download_best_candidate then
    sorted by score and could download the previous image again.  This helper pins each
    pack to candidates that match the primary src media key.  If none match, it keeps only
    the first candidate as a safe fallback.
    """
    candidates = pack.get("candidates") or []
    primary_src = (pack.get("src") or "").strip()
    primary_key = _media_key_from_src(primary_src)

    if not candidates:
        if primary_src:
            return [{
                "type": pack.get("type") or _media_type_from_url(primary_src),
                "src": primary_src,
                "score": int(pack.get("score") or 0),
                "_allow_fb_best_available_source": bool(pack.get("_allow_fb_best_available_source")),
            }]
        return []

    if not primary_key:
        return candidates[:1]

    pinned = []
    seen = set()
    for cand in candidates:
        src = (cand.get("src") or "").strip()
        if not src:
            continue
        key = _media_key_from_src(src)
        if key != primary_key:
            continue
        if cand.get("type") == "video" or any(x in src.lower() for x in [".mp4", ".m4v", ".mov"]):
            dedupe_key = src.split("?")[0]
        else:
            dedupe_key = _normalized_exact_fb_media_url(src)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        pinned.append(cand)

    if pinned:
        # Keep the primary candidate order.  Do not bring unrelated stale candidates back.
        return pinned[:4]

    # Fallback: if the packed candidates were polluted, synthesize one candidate from pack src.
    if primary_src:
        return [{
            "type": pack.get("type") or _media_type_from_url(primary_src),
            "src": primary_src,
            "score": int(pack.get("score") or 0),
            "_allow_fb_best_available_source": bool(pack.get("_allow_fb_best_available_source")),
        }]

    return candidates[:1]


def _download_viewer_items(context, viewer_items, referer: str):
    """
    v11.14 stable:
    - final file numbers are based on accepted unique images, not candidate index.
    - each collected pack is pinned to its own primary media key before download.
    - this prevents stale high-score network responses from making many packs download
      the same fb_0008/fb_0011 image again.
    """
    success_count = 0
    ordered_output_files = []
    seen_hashes = set()
    seen_media_keys = set()

    for attempt_i, pack in enumerate(viewer_items, 1):
        primary_key = _media_key_from_src(pack.get("src", ""))

        if primary_key and primary_key in seen_media_keys:
            logger.warning(
                f"FB 候選第 {attempt_i} 張 primary media key 已重複，略過: {primary_key}"
            )
            continue

        candidates = _pack_primary_candidates(pack)
        dst_base = os.path.join(TEMP_DIR, f"fb_{success_count + 1:04d}")

        try:
            final_dst, size = _download_best_candidate(
                context,
                candidates,
                dst_base,
                referer=referer,
            )

            digest = _file_md5(final_dst)
            if digest in seen_hashes:
                logger.warning(
                    f"FB 候選第 {attempt_i} 張下載後判定重複，刪除暫存: "
                    f"{os.path.basename(final_dst)} ({size} bytes) primary={primary_key}"
                )
                try:
                    os.remove(final_dst)
                except Exception:
                    pass
                continue

            seen_hashes.add(digest)
            if primary_key:
                seen_media_keys.add(primary_key)

            if size < 80 * 1024 and len(viewer_items) >= 8:
                logger.warning(
                    f"FB 輸出第 {success_count + 1} 張檔案偏小，可能是縮圖/placeholder: "
                    f"{os.path.basename(final_dst)} ({size} bytes)"
                )

            success_count += 1
            ordered_output_files.append(final_dst)
            logger.info(
                f"FB 已下載輸出第 {success_count} 張: {os.path.basename(final_dst)} "
                f"({size} bytes) primary={primary_key}"
            )

        except Exception as e:
            logger.warning(f"FB 候選第 {attempt_i} 張下載失敗: {e}")

    logger.info(f"FB unique output media count={success_count}")
    return success_count, ordered_output_files

def _media_key_from_src(src: str) -> str:
    if not src:
        return ""

    path = urlparse(src.split("?")[0]).path
    basename = os.path.basename(path)

    if not basename:
        return src[:180]

    m = re.search(r"oh=([^&]+)", src)
    if m:
        return basename + "_oh_" + m.group(1)[:20]

    return basename


def _merge_unique_candidates(primary: list[dict], fallback: list[dict]) -> list[dict]:
    merged = []
    seen = set()

    for item in (primary or []) + (fallback or []):
        src = (item.get("src") or "").strip()

        if not src:
            continue

        key = _media_key_from_src(src)

        if key in seen:
            continue

        seen.add(key)
        merged.append(item)

    return _dedupe_ordered(merged)



def _strict_visible_photo_candidates(page, *, prefer_dialog: bool = True) -> list[dict]:
    """
    只抓目前頁面肉眼可見的大圖，不吃 meta/html/network，避免上一頁殘留 URL 污染。
    """
    js = r"""
    (preferDialog) => {
      const root = (preferDialog && document.querySelector('div[role="dialog"]')) || document;
      const out = [];
      const W = window.innerWidth || 1600;
      const H = window.innerHeight || 1000;

      function bad(low) {
        const badList = [
          'static.xx.fbcdn.net', '/rsrc.php', 'profile_pic', 'sprite', 'emoji',
          'icon', 'logo', 'favicon', 'safe_image.php', 'hads-ak', 'ads',
          'p32x32', 's32x32', 's40x40', 's50x50', 's64x64', 'p64x64',
          'q=40', 'q=50', 'q=60', 'dst-jpg_s200x200', 'cp0_dst-jpg_p32x32'
        ];
        return badList.some(x => low.includes(x));
      }

      function push(el, src, bonus) {
        if (!src) return;
        const low = src.toLowerCase();
        if (bad(low)) return;
        if (!(low.includes('scontent') || low.includes('fbcdn.net') || low.includes('video'))) return;
        const r = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        const w = r.width || 0;
        const h = r.height || 0;
        if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') <= 0) return;
        if (w < 120 || h < 120) return;
        if (r.right < 0 || r.bottom < 0 || r.left > W || r.top > H) return;

        const cx = r.left + w / 2;
        const cy = r.top + h / 2;
        const centerPenalty = Math.abs(cx - W / 2) + Math.abs(cy - H / 2);
        const naturalW = el.naturalWidth || el.videoWidth || 0;
        const naturalH = el.naturalHeight || el.videoHeight || 0;
        const visibleArea = w * h;
        const naturalArea = naturalW * naturalH;

        out.push({
          type: el.tagName.toLowerCase() === 'video' ? 'video' : 'image',
          src,
          score: Math.floor(2000000 + visibleArea + naturalArea / 2 - centerPenalty * 2 + (bonus || 0)),
          area: visibleArea,
          naturalArea
        });
      }

      const nodes = Array.from(root.querySelectorAll('img, video'));
      for (const el of nodes) {
        push(el, (el.currentSrc || '').trim(), 60000);
        push(el, (el.src || '').trim(), 50000);
        push(el, (el.getAttribute('src') || '').trim(), 40000);
        const srcset = el.getAttribute('srcset') || '';
        if (srcset) {
          for (const part of srcset.split(',').map(x => x.trim()).filter(Boolean)) {
            push(el, part.split(/\s+/)[0], 70000);
          }
        }
      }

      out.sort((a, b) => b.score - a.score);
      return out;
    }
    """
    try:
        items = page.evaluate(js, prefer_dialog) or []
    except Exception:
        items = []

    items = _dedupe_ordered(items)
    return sorted(items, key=lambda x: x.get("score", 0), reverse=True)


def _open_photo_link_collect_fresh(context, link: str, *, idx: int) -> list[dict]:
    """
    每個 photo link 用新分頁開，避免同一 page 的 DOM/network cache 把上一張圖混進來。
    """
    p = None
    try:
        p = context.new_page()
        p.goto(link, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(2300)
        try:
            p.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass

        candidates = _strict_visible_photo_candidates(p, prefer_dialog=True)
        if not candidates:
            candidates = _strict_visible_photo_candidates(p, prefer_dialog=False)

        # 有些 photo page 仍是貼文頁，點一下最大圖進 viewer 再抓一次。
        if not candidates or candidates[0].get("score", 0) < 500000:
            try:
                _click_plus_overlay_or_first_photo(p)
                p.wait_for_timeout(2300)
                candidates2 = _strict_visible_photo_candidates(p, prefer_dialog=True)
                if candidates2:
                    candidates = candidates2
            except Exception:
                pass

        return candidates
    except Exception as e:
        logger.warning(f"FB fresh photo page 第 {idx} 張開啟失敗: {e}")
        return []
    finally:
        try:
            if p:
                p.close()
        except Exception:
            pass



def _media_type_from_url(url: str) -> str:
    low = (url or "").lower()
    if any(x in low for x in [".mp4", ".m4v", ".mov", "video"]):
        return "video"
    return "image"

def _is_probably_video_url(url: str) -> bool:
    low = (url or "").lower()
    return any(x in low for x in ["/watch", "/videos/", "video.php", "reel", "/reels/"])


def _stable_photo_link_key(url: str) -> str:
    """
    v7：只把「真正單張照片」當成 photo item。

    v6 的問題是把 permalink.php?story_fbid=... 複製成 #dup2/#dup3，
    看起來 normalized=17，但其實第 6~16 都是同一篇貼文入口，
    最後全抓到同一張 52KB placeholder。

    規則：
    - photo/?fbid= / story_fbid 為純數字 / photo_id 才視為單張照片 key。
    - permalink.php?story_fbid=pfbid... 這種是「貼文入口」，不是照片入口，交給 viewer 處理。
    """
    if not url:
        return ""

    u = html.unescape(unquote(str(url)))

    # 真正的單張照片 id：優先使用 fbid/photo_id。
    for pat in [
        r"[?&](?:fbid|photo_id)=([0-9]{8,})",
        r"/photos/(?:[^/]+/)?([0-9]{8,})",
        r"/photo(?:\.php)?/?.*?[?&]fbid=([0-9]{8,})",
    ]:
        m = re.search(pat, u, flags=re.I)
        if m:
            return "fbid:" + m.group(1)

    # 有些 URL 會用數字 story_fbid 指到單張照片；pfbid 通常是整篇貼文，不拿來當照片 key。
    m = re.search(r"[?&]story_fbid=([0-9]{8,})", u, flags=re.I)
    if m:
        return "story_fbid:" + m.group(1)

    return ""


def _is_true_photo_link(url: str) -> bool:
    """只接受可定位到單張照片的 URL；permalink/post 入口不在這裡展開。"""
    if not url:
        return False
    low = html.unescape(unquote(str(url))).lower()
    if "facebook.com" not in low:
        return False
    if "permalink.php" in low and "fbid=" not in low and "photo_id=" not in low:
        return False
    return bool(_stable_photo_link_key(url))


def _make_photo_records(links: list[str], grid_items: list[dict] | None = None) -> list[dict]:
    """
    v7：只把真正單張照片 link 建成 records。

    ordered_links 裡常混有多個完全相同的 permalink.php?story_fbid=pfbid...
    那是貼文入口，不是照片入口。把它們當照片會造成 17 張裡 11 張重複縮圖。
    """
    grid_by_key = {}
    grid_by_index = []

    for g in (grid_items or [])[:_MAX_FB_ITEMS]:
        href = g.get("href") or ""
        key = _stable_photo_link_key(href)
        rec = {
            "href": href,
            "key": key,
            "grid_candidates": g.get("candidates") or [],
        }
        if key:
            grid_by_key[key] = rec
        grid_by_index.append(rec)

    out = []
    seen = set()

    for idx, link in enumerate((links or [])[:_MAX_FB_ITEMS], 1):
        if not _is_true_photo_link(link):
            logger.info(f"FB skip non-photo link {idx}: {link[:120]}")
            continue

        key = _stable_photo_link_key(link)
        if not key or key in seen:
            continue
        seen.add(key)

        grid_rec = grid_by_key.get(key)
        if not grid_rec and idx <= len(grid_by_index):
            # 前幾張通常 grid 與 links 同序；只拿來當 fallback，不拿來增加數量。
            grid_rec = grid_by_index[idx - 1]

        out.append({
            "href": link,
            "key": key,
            "grid_candidates": (grid_rec or {}).get("grid_candidates") or [],
            "source": "grid+link" if grid_rec else "link",
        })

    # links 拿不到真正 photo link 時，才退回 grid。
    if not out:
        seen_grid = set()
        for g in grid_by_index:
            key = g.get("key") or _stable_photo_link_key(g.get("href") or "")
            if key and key in seen_grid:
                continue
            if key:
                seen_grid.add(key)
            out.append({
                "href": g.get("href") or "",
                "key": key,
                "grid_candidates": g.get("grid_candidates") or [],
                "source": "grid",
            })

    logger.info(f"FB normalized true photo item count={len(out)}")
    for i, rec in enumerate(out[:40], 1):
        logger.info(
            f"FB true item {i}: key={rec.get('key')} source={rec.get('source')} "
            f"href={(rec.get('href') or '')[:160]}"
        )
    return out

def _build_photo_items_from_links(page, links, network_items: list[dict] | None = None, grid_items: list[dict] | None = None):
    """
    v4：以 ordered photo links 為主，grid 只當 fallback。

    修正重點：
    - 不再讓 grid item count=5 限制總張數。
    - 每個 photo link 用新分頁抓「目前可見主圖」，不吃 HTML/meta/network 殘留。
    - 已用過的媒體 key 會降權/略過，避免 1/6、5/7 這種重複下載。
    """
    viewer_items = []
    used_media_keys = set()

    records = _make_photo_records(links or [], grid_items or [])

    context = page.context

    for idx, rec in enumerate(records[:_MAX_FB_ITEMS], 1):
        link = rec.get("href") or ""
        grid_candidates = rec.get("grid_candidates") or []
        source = rec.get("source") or "link"
        rec_key = rec.get("key") or ""

        try:
            fresh_candidates = []
            if link:
                fresh_candidates = _open_photo_link_collect_fresh(context, link, idx=idx)

            # links 抓到的可見主圖優先，grid 只補候選，不搶第一順位。
            candidates = _merge_unique_candidates(fresh_candidates, grid_candidates)
            if not candidates and grid_candidates:
                candidates = _merge_unique_candidates(grid_candidates, [])
                source = "grid-fallback"

            if not candidates:
                logger.warning(f"FB photo link 第 {idx} 張沒有有效候選")
                continue

            # 把沒用過的 candidate 拉到第一順位。
            moved_unique = False
            for cand_i, cand in enumerate(candidates):
                cand_key = _media_key_from_src(cand.get("src", ""))
                if cand_key and cand_key not in used_media_keys:
                    if cand_i != 0:
                        candidates.insert(0, candidates.pop(cand_i))
                    moved_unique = True
                    break

            chosen_key = _media_key_from_src(candidates[0].get("src", ""))

            if not moved_unique or chosen_key in used_media_keys:
                logger.warning(
                    f"FB photo link 第 {idx} 張候選都是重複圖，略過: "
                    f"{os.path.basename(urlparse(candidates[0].get('src', '').split('?')[0]).path)}"
                )
                continue

            used_media_keys.add(chosen_key)

            viewer_items.append({
                "order": len(viewer_items) + 1,
                "candidates": candidates,
                "src": candidates[0].get("src", ""),
                "type": candidates[0].get("type", "image"),
                "score": candidates[0].get("score", 0),
            })

            logger.info(
                f"FB photo link 收集第 {len(viewer_items)} 張: "
                f"來源={source} key={rec_key} 候選 {len(candidates)} 個，best="
                f"{os.path.basename(urlparse(candidates[0].get('src', '').split('?')[0]).path)}"
            )

        except Exception as e:
            logger.warning(f"FB photo page 略過: {link[:120]} | {e}")
            continue

    return viewer_items


def _collect_fb_media_playwright(url: str):
    clear_temp()

    browser = None
    context = None

    try:
        resolved = _resolve_share_url(url)
        network_items = []

        with sync_playwright() as p:
            user_data_dir = _get_fb_parser_profile_root()
            profile_dir = _resolve_fb_chrome_profile_directory()
            logger.info(
                f"FB 啟用 FB_Parser persistent profile: user_data_dir={user_data_dir}, "
                f"profile={profile_dir}, cookies.txt=legacy-fallback"
            )

            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="chrome",
                headless=FB_HEADLESS,
                no_viewport=True,
                locale="zh-TW",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/123.0.0.0 Safari/537.36"
                ),
                args=[
                    f"--profile-directory={profile_dir}",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--start-maximized",
                ],
            )

            try:
                context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            except Exception:
                pass

            # Legacy emergency fallback only: if a cookies.txt exists, merge it into
            # the persistent profile context without making cookies.txt the primary workflow.
            cookies = _load_netscape_cookies(COOKIES_FILE, "facebook.com")

            if cookies:
                try:
                    context.add_cookies(cookies)
                    logger.info(f"已從 cookies.txt 載入 FB cookies 到 Playwright 備援 context: {len(cookies)}")

                except Exception as e:
                    logger.warning(f"FB add_cookies 備援失敗: {e}")

            page = _get_fresh_fb_profile_page(context)

            def on_response(resp):
                try:
                    u = resp.url

                    if not _looks_like_real_fb_media_url(u):
                        return

                    score = 1100000 + _media_quality_score(u)

                    ctype = ""
                    try:
                        ctype = resp.headers.get("content-type", "")
                    except Exception:
                        ctype = ""

                    clen = 0
                    try:
                        raw_len = resp.headers.get("content-length", "") or "0"
                        clen = int(raw_len) if str(raw_len).isdigit() else 0
                    except Exception:
                        clen = 0

                    if 0 < clen < _MIN_FILE_SIZE:
                        logger.debug(f"FB ignore tiny page response: {clen} bytes | {u[:100]}")
                        return

                    if "image" in ctype:
                        score += 120000
                    if "video" in ctype:
                        score += 120000
                    if clen >= _PREFERRED_IMAGE_SIZE:
                        score += min(clen, 900000)

                    network_items.append({
                        "type": _media_type_from_url(u),
                        "src": u,
                        "score": score,
                    })

                    if len(network_items) > 500:
                        del network_items[:200]

                except Exception:
                    pass

            page.on("response", on_response)

            try:
                page.goto(
                    resolved,
                    wait_until="domcontentloaded",
                    timeout=60000,
                )

            except PlaywrightTimeoutError:
                logger.warning("FB goto 超時，改用目前頁面")

            page.wait_for_timeout(4500)

            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            current_url = page.url.lower()

            if "login" in current_url and "facebook.com/login" in current_url:
                return "BLOCKED", "Facebook Playwright 偵測需登入"

            title = _clean_fb_post_title_for_path(_get_fb_title(page), fallback="Facebook_Post")

            # v11.92 Reel title-only fix:
            # Restore the previously working Reel media candidate pipeline. Facebook often
            # renders the active <video> with a blob: URL, so requiring a direct URL from the
            # visible video element causes false RETRY even though network/meta candidates are
            # downloadable. The only behavioral change here is naming: prefer the active
            # Reel caption/title instead of the share URL token.
            if _is_fb_video_like_url(url) or _is_fb_video_like_url(resolved) or _is_fb_video_like_url(page.url):
                observed_reel_id = _extract_canonical_fb_reel_id_from_page(page)
                reel_fallback = _fb_reel_fallback_title(
                    f"https://www.facebook.com/reel/{observed_reel_id}/"
                    if observed_reel_id else (resolved or url)
                )
                reel_title = _get_fb_reel_caption_title(page, fallback=reel_fallback)
                if not reel_title or _is_fallback_fb_title(reel_title):
                    reel_title = reel_fallback

                # Publish the resolved caption to the GUI before media download.
                _publish_fb_task_title(url, reel_title)
                if resolved and resolved != url:
                    _publish_fb_task_title(resolved, reel_title)

                try:
                    try:
                        page.mouse.click(960, 600)
                    except Exception:
                        pass

                    page.wait_for_timeout(2500)

                    try:
                        page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass

                    # Keep the proven pre-v11.91 collection path. It can use the browser
                    # network/meta/html candidates even when the visible video src is blob:.
                    reel_candidates = _collect_current_page_candidates(
                        page,
                        network_items=network_items,
                        include_network=True,
                        include_meta=True,
                        include_html=True,
                    )

                    video_candidates = []
                    for cand in reel_candidates:
                        src = cand.get("src") or ""
                        if cand.get("type") == "video" or _is_probably_video_url(src) or any(
                            x in src.lower() for x in [".mp4", ".m4v", ".mov"]
                        ):
                            c2 = dict(cand)
                            c2["type"] = "video"
                            c2["score"] = int(c2.get("score") or 0) + 3000000
                            video_candidates.append(c2)

                    video_candidates = _dedupe_ordered(video_candidates)
                    logger.info(
                        f"FB Reel video candidate count={len(video_candidates)} "
                        f"from total={len(reel_candidates)}; title={reel_title}"
                    )

                    if not video_candidates:
                        clear_temp()
                        return "RETRY", "Facebook Reel 未擷取到有效影片候選，避免誤存封面圖為 jpg"

                    final_dst, size = _download_best_candidate(
                        context,
                        video_candidates,
                        os.path.join(TEMP_DIR, "fb_0001"),
                        referer=resolved,
                    )
                    logger.info(
                        f"FB Reel 主影片已下載: {os.path.basename(final_dst)} "
                        f"({size // 1024} KB)"
                    )

                    if move_files(reel_title):
                        return "SUCCESS", ""

                    return "FAILED", "Facebook Reel 影片已下載，但搬移檔案失敗"

                except Exception as e:
                    clear_temp()
                    return _classify_error(f"Facebook Reel 影片下載失敗: {e}")

            # 第一階段：先從原貼文收集 photo links / grid items，這裡最接近畫面順序
            grid_before = _collect_fb_grid_items(page)
            links_before = _collect_fb_photo_links(page)
            plus_count_before = _detect_fb_plus_overlay_count(page)
            logger.info(f"FB grid item count before +N={len(grid_before)}")
            logger.info(f"FB photo link count before +N={len(links_before)}")
            logger.info(f"FB +N overlay count before={plus_count_before}")

            # 第二階段：點 +12 / 更多照片，再收集完整列表
            try:
                _click_plus_overlay_or_first_photo(page)
                page.wait_for_timeout(3000)
            except Exception:
                pass

            grid_after = _collect_fb_grid_items(page)
            links_after = _collect_fb_photo_links(page)
            logger.info(f"FB grid item count after +N={len(grid_after)}")
            logger.info(f"FB photo link count after +N={len(links_after)}")

            # 合併順序：
            # 先保留 before 的可見順序，再補 after 的新增連結。
            ordered_links = []
            seen_link = set()

            for link in links_before + links_after:
                if link and link not in seen_link:
                    seen_link.add(link)
                    ordered_links.append(link)

            ordered_grid_items = []
            seen_grid_link = set()

            for item in grid_before + grid_after:
                href = item.get("href") or ""
                if href and href not in seen_grid_link:
                    seen_grid_link.add(href)
                    ordered_grid_items.append(item)

            logger.info(f"FB ordered photo link count={len(ordered_links)}")
            logger.info(f"FB ordered grid item count={len(ordered_grid_items)}")

            dominant_pcb_key = _dominant_pcb_key_from_links(ordered_links)
            if dominant_pcb_key:
                before_links_n = len(ordered_links)
                before_grid_n = len(ordered_grid_items)
                ordered_links, ordered_grid_items = _filter_links_and_grid_by_pcb(
                    ordered_links,
                    ordered_grid_items,
                    dominant_pcb_key,
                )
                logger.info(
                    f"FB post scope pcb={dominant_pcb_key}: "
                    f"links {before_links_n}->{len(ordered_links)}, "
                    f"grid {before_grid_n}->{len(ordered_grid_items)}"
                )

            # v11.19: title must be scoped to the article containing the selected PCB photo links.
            # Generic page selectors can grab neighboring/recommended posts in logged-in feeds.
            if dominant_pcb_key:
                title = _get_post_folder_name_for_pcb(page, dominant_pcb_key, fallback=title)
            else:
                title = _get_post_folder_name(page)

            # v11.40: publish the resolved caption/folder title to the GUI immediately.
            _publish_fb_task_title(url, title)
            if resolved and resolved != url:
                _publish_fb_task_title(resolved, title)

            expected_photo_count = _estimate_expected_photo_count(
                page,
                ordered_links,
                ordered_grid_items,
                plus_count=plus_count_before,
            )
            logger.info(f"FB expected photo target={expected_photo_count}")

            # v11.46 safety:
            # This uploaded full version does not include the later large-album
            # helper stack from the previous v11.40-v11.45 branch.  Keep this flag
            # explicit so the narrow single-photo duplicate-link correction below
            # does not raise NameError and does not alter normal gallery behavior.
            large_album_mode = False

            if expected_photo_count and len(ordered_links) > expected_photo_count + 3:
                logger.warning(
                    f"FB bounded scope: ordered_links={len(ordered_links)} > expected={expected_photo_count}，"
                    "後段多半是首頁/推薦連結，先裁切"
                )
                ordered_links = ordered_links[:expected_photo_count + 3]

            link_items = []
            viewer_items = []

            if ordered_grid_items or ordered_links:
                # 主路徑：只處理真正 photo links；permalink/post 入口交給 viewer。
                link_items = _build_photo_items_from_links(
                    page,
                    ordered_links,
                    network_items=network_items,
                    grid_items=ordered_grid_items,
                )

            logger.info(f"FB photo-link sequence count={len(link_items)}")
            manifest_ids = _manifest_ids_from_packs(link_items)
            if manifest_ids:
                logger.info(f"FB v11.17 manifest whitelist ids={len(manifest_ids)}")

            # v11.93 scoped manifest target correction:
            # In logged-in /share/ photo posts Facebook may show a +N overlay from
            # a mixed viewer/recommendation context, while exact set=pcb scoped
            # links/grid/manifest all prove the real target post contains fewer
            # photos.  In that narrow case, use the exact post manifest count as
            # the completeness target instead of retrying forever at +N.
            try:
                scoped_manifest_count = len(manifest_ids or [])
                scoped_link_count = len(link_items or [])
                if (
                    expected_photo_count
                    and plus_count_before
                    and int(plus_count_before) <= 1
                    and dominant_pcb_key
                    and scoped_manifest_count >= 3
                    and scoped_manifest_count == scoped_link_count
                    and int(expected_photo_count) > scoped_manifest_count
                    and len(ordered_links or []) <= scoped_manifest_count + 1
                    and len(ordered_grid_items or []) <= scoped_manifest_count + 2
                ):
                    logger.info(
                        "FB v11.93 corrected +N mixed-context target by scoped manifest: "
                        f"expected {expected_photo_count}->{scoped_manifest_count}, "
                        f"plus={plus_count_before}, links={len(ordered_links or [])}, "
                        f"grid={len(ordered_grid_items or [])}, manifest={scoped_manifest_count}, "
                        f"pcb={dominant_pcb_key}"
                    )
                    expected_photo_count = scoped_manifest_count
            except Exception as _e:
                logger.debug(f"FB v11.93 scoped manifest target correction skipped: {_e}")

            # v11.47 scoped ghost-link correction:
            # Some /share/ posts expose one extra set=pcb link that points to the post
            # container rather than a fourth photo.  In the failing 18uZbg4XsQ case,
            # scoped links reported 4 while grid, normalized photo records and manifest
            # all independently proved there are exactly 3 real photos.  Correct only
            # this narrow one-extra-link case; +N and real larger galleries stay strict.
            try:
                normalized_count = len(link_items or [])
                if (
                    expected_photo_count
                    and not plus_count_before
                    and normalized_count >= 2
                    and int(expected_photo_count) == normalized_count + 1
                    and len(ordered_links or []) == int(expected_photo_count)
                    and len(ordered_grid_items or []) <= normalized_count
                    and len(manifest_ids or []) == normalized_count
                    and not large_album_mode
                ):
                    logger.info(
                        "FB v11.47 corrected one ghost photo link target: "
                        f"expected {expected_photo_count}->{normalized_count}, "
                        f"ordered_links={len(ordered_links or [])}, "
                        f"grid={len(ordered_grid_items or [])}, "
                        f"normalized={normalized_count}, manifest={len(manifest_ids or [])}"
                    )
                    expected_photo_count = normalized_count
            except Exception as _e:
                logger.debug(f"FB v11.47 ghost-link correction skipped: {_e}")

            # v11.46 Single-photo duplicate-link target correction:
            # Some Facebook share/p posts expose two ordered photo links even though
            # every reliable post-scoped signal points to one real photo:
            #   - no +N overlay
            #   - one visible grid tile
            #   - one normalized true photo/link item
            #   - one manifest/media id
            # In that case ordered_links=2 is a duplicate/ghost link, not a real 2-photo
            # gallery.  Correct only this narrow case so normal 2-photo, 16-photo and
            # large-album completeness guards remain strict.
            try:
                if (
                    expected_photo_count
                    and int(expected_photo_count) == 2
                    and not plus_count_before
                    and len(ordered_grid_items or []) <= 1
                    and len(ordered_links or []) <= 2
                    and len(link_items or []) == 1
                    and len(manifest_ids or []) == 1
                    and not large_album_mode
                ):
                    logger.info(
                        "FB v11.46 corrected duplicate single-photo target: "
                        f"expected {expected_photo_count}->1, "
                        f"ordered_links={len(ordered_links or [])}, "
                        f"grid={len(ordered_grid_items or [])}, "
                        f"link_items={len(link_items or [])}, "
                        f"manifest={len(manifest_ids or [])}"
                    )
                    expected_photo_count = 1
            except Exception as _e:
                logger.debug(f"FB v11.46 duplicate single-photo target correction skipped: {_e}")

            if ordered_links and len(link_items) < max(8, int(len(ordered_links) * 0.7)):
                logger.warning(f"FB photo-link sequence 明顯不足: link_items={len(link_items)} / ordered_links={len(ordered_links)}，代表部分 photo link 開頁後仍回同一張或只給縮圖")

            # v11.15: Determine post media cluster BEFORE opening the viewer, so off-post
            # recommendations never count toward expected_photo_count during harvesting.
            pre_viewer_cluster = _dominant_media_cluster(link_items, min_count=2)
            if pre_viewer_cluster:
                logger.info(f"FB v11.15 pre-viewer media cluster scope={pre_viewer_cluster}")

            # 多圖完整性補強 v9：
            # 從多個入口啟動 viewer。FB 有時從 +N 入口只能翻 3 張，
            # 但從 photo/?fbid= 或原貼文入口可走到不同區段，所以要合併多段 viewer sequence。
            grid_tile_items = []
            try:
                network_items.clear()

                viewer_sequences = []
                post_sequence = _collect_viewer_sequence_from_url(
                    context,
                    resolved,
                    label="post",
                    is_photo_page=False,
                    target_count=expected_photo_count or None,
                    stale_threshold=4,
                    max_turns=max(24, (expected_photo_count or 16) + 10),
                    allowed_cluster=pre_viewer_cluster or None,
                )
                viewer_sequences.append(post_sequence)

                # v11.13: In post-scoped mode, never open individual photo pages
                # after post viewer. Logged-in Facebook often redirects those photo pages
                # to feed/recommendation contexts and pollutes the output.
                if expected_photo_count:
                    logger.info(
                        "FB v11.13 post-scoped mode: 略過 photo1/photo2 補挖，"
                        "只保留主貼文 viewer + scoped link candidates"
                    )
                else:
                    true_photo_links = []
                    for link in ordered_links:
                        if _is_true_photo_link(link):
                            true_photo_links.append(link)
                        if len(true_photo_links) >= 5:
                            break

                    for i, link in enumerate(true_photo_links, 1):
                        viewer_sequences.append(_collect_viewer_sequence_from_url(
                            context,
                            link,
                            label=f"photo{i}",
                            is_photo_page=True,
                            target_count=2,
                            stale_threshold=3,
                            max_turns=6,
                            allowed_cluster=pre_viewer_cluster or None,
                        ))

                viewer_items = _aggregate_unique_items(*viewer_sequences)
                logger.info(f"FB viewer sequence count={len(viewer_items)}")

                # v11.22.1 Fast Retry Guard:
                # The old v11.21 slow recovery can run for many minutes and may keep logging after
                # the worker has timed out. If the primary Theater pass is short, immediately ask
                # the worker for a clean browser-context retry instead of looping stale frames.
                if expected_photo_count:
                    try:
                        before_retry_n = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                    except Exception:
                        before_retry_n = len(_aggregate_unique_items(link_items, viewer_items))
                    if before_retry_n < expected_photo_count:
                        logger.info(
                            f"FB v11.22.1 fast retry guard: incomplete={before_retry_n}/target={expected_photo_count}; "
                            "skip slow recovery and retry fresh context"
                        )
                        return "RETRY", f"Facebook viewer incomplete {before_retry_n}/{expected_photo_count}; retry fresh context"

            except Exception as e:
                logger.warning(f"FB viewer fallback 失敗: {e}")

            # v11.22 Grid Tile Mode: if Theater Viewer still misses one or more tiles,
            # extract by physical grid order from the +N dialog/current page.
            if expected_photo_count:
                try:
                    current_unique_n = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                except Exception:
                    current_unique_n = len(_aggregate_unique_items(link_items, viewer_items))
                if current_unique_n < expected_photo_count:
                    logger.info(
                        f"FB v11.22 grid tile mode trigger: unique={current_unique_n}/target={expected_photo_count}"
                    )
                    grid_tile_items = _collect_grid_tile_mode_items(
                        context,
                        page,
                        pcb_key=dominant_pcb_key,
                        expected_count=expected_photo_count,
                        allowed_cluster=pre_viewer_cluster or None,
                    )
                    if grid_tile_items:
                        before_grid_merge = current_unique_n
                        viewer_items = _aggregate_unique_items(viewer_items, grid_tile_items)
                        try:
                            after_grid_merge = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                        except Exception:
                            after_grid_merge = len(_aggregate_unique_items(link_items, viewer_items))
                        logger.info(
                            f"FB v11.22 grid tile mode merged: {before_grid_merge}->{after_grid_merge}"
                        )

            # v7 合併策略：
            # - true photo links 通常只提供前 5 張正確入口。
            # - viewer 可能從第 1 張或 +N 附近開始。
            # - 最穩定方式是保留 link_items 順序，再 append viewer 裡沒出現過的可見主圖。
            final_items = []
            used_keys = set()

            for pack in link_items:
                key = _media_key_from_src(pack.get("src", ""))
                if key and key in used_keys:
                    continue
                if key:
                    used_keys.add(key)
                final_items.append(pack)

            for pack in viewer_items:
                key = _media_key_from_src(pack.get("src", ""))
                if key and key in used_keys:
                    continue
                if key:
                    used_keys.add(key)
                final_items.append(pack)

            # 如果 viewer 本身比合併結果更完整，代表它是從第一張完整跑完，直接採用 viewer。
            if len(viewer_items) >= max(len(final_items), len(link_items) + 4):
                final_items = viewer_items

            # v11.22: when grid tile mode found a full/near-full ordered set, use its physical order.
            # This is the only reliable way to solve the missing visual 10.jpg and 6/7 swaps.
            try:
                if expected_photo_count and grid_tile_items and len(_dedupe_items_by_media_id(grid_tile_items)) >= expected_photo_count - 1:
                    logger.info(
                        f"FB v11.22 grid tile mode order preferred: {len(grid_tile_items)} items"
                    )
                    final_items = grid_tile_items
            except Exception:
                pass

            viewer_items = final_items

            # v11.13: Determine media cluster from scoped true photo links first.
            # Viewer/network candidates may already be polluted by recommendations,
            # so they must not decide the cluster unless link_items are insufficient.
            dominant_cluster = pre_viewer_cluster or _dominant_media_cluster(link_items, min_count=2)
            if not dominant_cluster:
                dominant_cluster = _dominant_media_cluster(viewer_items, min_count=3)

            manifest_ids_for_filter = list(manifest_ids or [])
            if (
                plus_count_before
                and expected_photo_count
                and len(manifest_ids_for_filter) < int(expected_photo_count)
            ):
                logger.info(
                    f"FB v11.96 +N full-gallery mode: keep viewer items beyond manifest "
                    f"manifest={len(manifest_ids_for_filter)}, expected={expected_photo_count}"
                )
                manifest_ids_for_filter = []

            if dominant_cluster or manifest_ids_for_filter:
                before_cluster_n = len(viewer_items)
                filtered_by_cluster = _filter_items_by_media_cluster_or_manifest(
                    viewer_items,
                    dominant_cluster,
                    manifest_ids=manifest_ids_for_filter,
                )
                # In post-scoped mode, prefer correctness over quantity.
                # It is better to output 15 correct images than 16 with one recommendation/ad.
                if filtered_by_cluster:
                    viewer_items = filtered_by_cluster
                    logger.info(
                        f"FB media scope cluster={dominant_cluster or '-'} manifest={len(manifest_ids)}: "
                        f"items {before_cluster_n}->{len(viewer_items)}"
                    )

            # v11.16: final cleanup before bounding.
            # 1) For photo posts, MP4/ad/reel responses must not count toward the photo target.
            # 2) Sort by FB CDN media id, not by interception time. This fixes 6/7/8 order jumps.
            if expected_photo_count:
                before_photo_clean_n = len(viewer_items)
                viewer_items = _drop_video_packs_for_photo_post(viewer_items)
                if len(viewer_items) != before_photo_clean_n:
                    logger.info(
                        f"FB v11.16 photo-only cleanup: items {before_photo_clean_n}->{len(viewer_items)}"
                    )

                if (
                    plus_count_before
                    and expected_photo_count
                    and 'manifest_ids_for_filter' in locals()
                    and not manifest_ids_for_filter
                ):
                    logger.info(
                        "FB v12.01 +N full-gallery mode: preserve viewer sequence order; "
                        "skip manifest/media-id sort because manifest is incomplete"
                    )
                else:
                    before_sort_keys = [
                        _fb_media_numeric_id_from_src(p.get("src") or "") for p in viewer_items
                    ]
                    viewer_items = _sort_items_by_manifest_then_media_id(
                        viewer_items,
                        manifest_ids=manifest_ids_for_filter if 'manifest_ids_for_filter' in locals() else manifest_ids,
                    )
                    after_sort_keys = [
                        _fb_media_numeric_id_from_src(p.get("src") or "") for p in viewer_items
                    ]
                    if before_sort_keys != after_sort_keys:
                        logger.info("FB v11.17 manifest/order sort applied by post manifest + media id")

                before_dedupe_n = len(viewer_items)
                viewer_items = _dedupe_items_by_media_id(viewer_items)
                if len(viewer_items) != before_dedupe_n:
                    logger.info(
                        f"FB v11.19 pre-boundary media-id dedupe: items {before_dedupe_n}->{len(viewer_items)}"
                    )

            if expected_photo_count and len(viewer_items) > expected_photo_count:
                logger.warning(
                    f"FB bounded scope: candidate count={len(viewer_items)} > expected={expected_photo_count}，"
                    "裁切到目標張數，避免側邊欄/推薦貼文混入"
                )
                viewer_items = viewer_items[:expected_photo_count]

            logger.info(f"FB merged candidate media count={len(viewer_items)}")
            if ordered_links and len(viewer_items) < len(link_items):
                logger.warning("FB merged candidate media count 少於 true photo links；建議設定 FB_HEADLESS=False 觀察 viewer")

            # 單圖 / 特殊貼文 fallback
            if not viewer_items:
                candidates = _collect_current_page_candidates(
                    page,
                    network_items=network_items,
                    include_network=True,
                    include_meta=True,
                    include_html=True,
                )

                if candidates:
                    viewer_items = [{
                        "order": 1,
                        "candidates": candidates,
                        "src": candidates[0].get("src", ""),
                        "type": candidates[0].get("type", "image"),
                        "score": candidates[0].get("score", 0),
                    }]

            logger.info(f"FB filtered media count={len(viewer_items)}")

            if (
                plus_count_before
                and expected_photo_count
                and int(expected_photo_count) > 1
                and len(viewer_items) >= int(expected_photo_count)
            ):
                logger.info(
                    f"FB v11.98 full-gallery best-available source mode enabled: "
                    f"items={len(viewer_items)}, expected={expected_photo_count}; "
                    "high-res variants are still tried first"
                )
                for _pack in viewer_items:
                    try:
                        _pack["_allow_fb_best_available_source"] = True
                        for _cand in (_pack.get("candidates") or []):
                            if isinstance(_cand, dict):
                                _cand["_allow_fb_best_available_source"] = True
                    except Exception:
                        pass

            if not viewer_items:
                return "FAILED", "Facebook Playwright 頁面已開啟，但未抓到有效貼文主媒體"

            success_count, ordered_output_files = _download_viewer_items(
                context,
                viewer_items,
                referer=resolved,
            )

            if success_count <= 0:
                if expected_photo_count and expected_photo_count > 1:
                    return "RETRY", (
                        "Facebook gallery candidates were collected but all failed "
                        "resolution/download validation; retry fresh context"
                    )
                return "FAILED", "Facebook Playwright 有抓到媒體 URL，但全部下載失敗"

            # v11.23.2 Strict Completeness Guard:
            # For multi-photo posts, incomplete Playwright output must be RETRY.
            # Never move partial files and never let the outer download() fall back to yt-dlp,
            # otherwise Facebook share/photo posts can be incorrectly finalized as a .mp4 video.
            if expected_photo_count and expected_photo_count > 1 and success_count < expected_photo_count:
                logger.warning(
                    f"FB strict completeness guard: output={success_count}/target={expected_photo_count}; "
                    "return RETRY and block yt-dlp fallback"
                )
                clear_temp()
                return "RETRY", f"Facebook gallery incomplete {success_count}/{expected_photo_count}; retry fresh context"

            if expected_photo_count and success_count >= expected_photo_count and ordered_output_files:
                logger.info(
                    f"FB v12.02 ordered move exact-count completion: "
                    f"outputs={len(ordered_output_files)}, expected={expected_photo_count}"
                )
                if move_files_ordered(title, ordered_output_files):
                    return "SUCCESS", ""

            if move_files(title):
                return "SUCCESS", ""

            return "FAILED", "Facebook Playwright 抓到的內容不是有效貼文媒體"

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


def _download_via_ytdlp(url: str, *, watch_fast: bool = False):
    """Download FB video through yt-dlp with an anti-hang watchdog.

    v12.04:
    Facebook Watch pages are single-video tasks.  A Watch download must not sit
    in the queue for many minutes at very low throughput.  The watchdog returns
    RETRY instead of appearing frozen.
    """
    clear_temp()
    resolved = _resolve_share_url(url)

    ffmpeg_path = _find_ffmpeg()

    if ffmpeg_path:
        formats = [
            "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "best",
        ]
    else:
        logger.warning("未找到 ffmpeg，FB yt-dlp 將使用單檔格式，避免合併失敗")
        formats = [
            "best[ext=mp4][height<=720]/best[protocol^=http][height<=720]/best",
        ]

    if watch_fast:
        formats = formats[:1]

    variants = []
    if os.path.exists(COOKIES_FILE):
        variants.append({
            "cookiefile": os.path.abspath(COOKIES_FILE),
        })
    if not watch_fast:
        variants.append({})
    if not variants:
        variants.append({})

    last_error = "未知錯誤"

    import time as _time
    started_at = _time.time()
    last_bytes = 0
    last_hook_at = started_at

    def _anti_hang_hook(d):
        nonlocal last_bytes, last_hook_at
        if not watch_fast:
            return

        now = _time.time()
        elapsed = now - started_at
        downloaded = int(d.get("downloaded_bytes") or 0)

        if downloaded > last_bytes:
            last_bytes = downloaded
            last_hook_at = now

        if elapsed > _FB_WATCH_YTDLP_MAX_SECONDS:
            raise Exception(
                f"FB watch yt-dlp download timeout after {int(elapsed)}s; "
                f"downloaded={downloaded} bytes"
            )

        if elapsed > 45 and downloaded > 0:
            avg = downloaded / max(1.0, elapsed)
            if avg < _FB_WATCH_YTDLP_MIN_BYTES_PER_SEC:
                raise Exception(
                    f"FB watch yt-dlp too slow: avg={int(avg)} B/s, "
                    f"downloaded={downloaded} bytes"
                )

        if elapsed > 75 and now - last_hook_at > 35:
            raise Exception(
                f"FB watch yt-dlp stalled: no progress for {int(now - last_hook_at)}s"
            )

    for extra in variants:
        for fmt in formats:
            try:
                ydl_opts = {
                    "quiet": False if watch_fast else True,
                    "no_warnings": True,
                    "outtmpl": os.path.join(TEMP_DIR, "%(title).120s.%(ext)s"),
                    "overwrites": True,
                    "noplaylist": True if watch_fast else False,
                    "format": fmt,
                    "socket_timeout": 20,
                    "retries": 1 if watch_fast else 3,
                    "fragment_retries": 1 if watch_fast else 3,
                    "file_access_retries": 1,
                    "continuedl": False if watch_fast else True,
                    "nopart": False,
                    "progress_hooks": [_anti_hang_hook],
                    "http_headers": {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/123.0.0.0 Safari/537.36"
                        ),
                        "Referer": "https://www.facebook.com/",
                        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                    },
                }

                if ffmpeg_path:
                    ydl_opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)
                    ydl_opts["merge_output_format"] = "mp4"

                ydl_opts.update(extra)

                logger.info(
                    f"FB yt-dlp start: watch_fast={watch_fast}, fmt={fmt}, "
                    f"resolved={resolved}"
                )

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(
                        resolved,
                        download=True,
                    )

                    fallback_title = (
                        _fb_reel_fallback_title(resolved)
                        if (_is_fb_video_like_url(url) or _is_fb_video_like_url(resolved))
                        else "Facebook_Post"
                    )
                    title = (
                        info.get("description")
                        or info.get("title")
                        or fallback_title
                    )

                if move_files(title):
                    return "SUCCESS", ""

                last_error = "yt-dlp 已執行，但沒有有效媒體檔案"

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Facebook yt-dlp 失敗: {last_error}")

                if watch_fast:
                    return "RETRY", last_error

    return _classify_error(last_error)



def download(url: str):
    result_box = [(None, None)]

    def _run():
        original_low = (url or "").lower()
        resolved = _resolve_share_url(url)
        resolved_low = (resolved or "").lower()

        # v11.23.2 full, non-crippled strict routing:
        # - Keep the whole Playwright gallery pipeline intact.
        # - For share/posts/photo/permalink URLs, Playwright is the source of truth.
        # - If Playwright says RETRY/BLOCKED/UNAVAILABLE, return that status directly.
        # - Do NOT fall back to yt-dlp after an incomplete gallery RETRY, because that can
        #   incorrectly download an unrelated/sibling .mp4 and mark the photo task SUCCESS.
        is_reel_like_url = _is_fb_reel_url(original_low) or _is_fb_reel_url(resolved_low)
        is_watch_video_url = _is_fb_watch_video_url(original_low) or _is_fb_watch_video_url(resolved_low)

        force_playwright_first = (
            any(x in original_low for x in [
                "/share/",
                "/posts/",
                "/photo",
                "/photos/",
                "story_fbid=",
                "fbid=",
                "/permalink/",
            ])
            and not is_reel_like_url
        )

        explicit_video_url = is_reel_like_url or is_watch_video_url or any(
            (x in original_low) or (x in resolved_low)
            for x in [
                "/watch",
                "/videos/",
                "video.php",
                "/reel",
                "/reels/",
                "fb.watch",
            ]
        )

        if explicit_video_url and not force_playwright_first:
            # v12.04:
            # Normal /watch/?v= is a single-video task.  Do not spend the first
            # attempt in Playwright/gallery deep scan. Reels/share-v still keep
            # Playwright-first because they are more prone to sibling pollution.
            if is_watch_video_url and not is_reel_like_url:
                status3, error3 = _download_via_ytdlp(resolved, watch_fast=True)
                if status3 == "SUCCESS":
                    result_box[0] = (status3, error3)
                    return

                logger.info(
                    f"FB watch fast yt-dlp did not complete; try Playwright active-video fallback: {error3}"
                )
                status2, error2 = _collect_fb_media_playwright(resolved)
                if status2 == "SUCCESS":
                    result_box[0] = (status2, error2)
                    return

                final_status, _ = _classify_error(f"ytdlp={error3} | playwright={error2}")
                if status2 in ("BLOCKED", "UNAVAILABLE"):
                    final_status = status2
                elif status3 == "RETRY" or status2 == "RETRY":
                    final_status = "RETRY"

                result_box[0] = (
                    final_status,
                    f"ytdlp={error3} | playwright={error2}",
                )
                return

            status2, error2 = _collect_fb_media_playwright(resolved)

            if status2 == "SUCCESS":
                result_box[0] = (status2, error2)
                return

            if status2 in ("RETRY", "BLOCKED", "UNAVAILABLE"):
                result_box[0] = (status2, error2)
                return

            result_box[0] = (status2 or "FAILED", error2)
            return

        status1, error1 = _collect_fb_media_playwright(resolved)

        if status1 == "SUCCESS":
            result_box[0] = (status1, error1)
            return

        if status1 in ("RETRY", "BLOCKED", "UNAVAILABLE"):
            logger.info(
                f"FB Playwright returned {status1}; skip yt-dlp fallback to preserve gallery integrity: {error1}"
            )
            result_box[0] = (status1, error1)
            return

        # Only allow yt-dlp fallback for URLs that are explicitly video-like.
        # Generic /share/ photo galleries must not become .mp4 after Playwright fails.
        if explicit_video_url:
            status2, error2 = _download_via_ytdlp(resolved)

            if status2 == "SUCCESS":
                result_box[0] = (status2, error2)
                return

            final_status, _ = _classify_error(f"playwright={error1} | ytdlp={error2}")

            result_box[0] = (
                final_status,
                f"playwright={error1} | ytdlp={error2}",
            )
            return

        logger.info(
            f"FB non-video/gallery route failed in Playwright; skip yt-dlp fallback: {error1}"
        )
        result_box[0] = (status1, error1)

    t = threading.Thread(
        target=_run,
        daemon=True,
    )

    task_timeout = (
        min(_DL_TIMEOUT, _FB_WATCH_YTDLP_MAX_SECONDS + 90)
        if _is_fb_watch_video_url(url)
        else _DL_TIMEOUT
    )

    t.start()
    t.join(task_timeout)

    if t.is_alive():
        logger.error(f"Facebook 下載超時: {url}")
        clear_temp()
        return "RETRY", f"下載超時 ({task_timeout}s)"

    result = result_box[0] or ("FAILED", "未知錯誤")
    status, reason = result
    if status != "SUCCESS":
        _clear_temp_after_terminal_failure(status, reason)
    return result
