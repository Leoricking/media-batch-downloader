# v14.2 Complete Runtime Signature Fix
# Keeps v14.1 immutable GalleryPlan; restores legacy helper keyword API names.
# Rebuilt from v13.6 known-working baseline; no broken v14.0 code carried forward.
# Gallery target is finalized once before harvest/recovery and is immutable afterward.
# v13.6 Integrated Authoritative Gallery Target Fix
# One authoritative +N count contract end-to-end; legacy downstream
# target re-raise paths cannot override the corrected gallery plan.
# v13.1 Integrated Pipeline Refactor
# Architecture: single effective definition per helper + centralized contracts + fixed regression suite
# v13.4: deterministic exact-PCB identity walker + v13.3 late reconciliation + strict no-false-success guard
# v13.3: late structured-payload reconciliation + exact-photo identity-bound candidates + bounded RETRY flow
# v13.2: exact-photo fallback recovery + group-container identity hardening + bounded RETRY flow
# exact-post recovery remains strict same-post only; never widens into recommendations
# v12.51 Exact Gallery Tail Recovery + Group Account Fix
# v12.50 Exact Gallery Identity + Pending-Budget Fix
# v12.49 Exact Short-Video + Primary Account + Hidden PCB Recovery Fix
# v12.48 Share/P Multi-Entry Viewer Recovery Fix
# v12.47 Candidate Exhaustive Gallery Guard
# v12.46 Grid Recovery Before Retry Fix
# v12.45 Duplicate Content Guard Fix
# v12.44 share/v Exact yt-dlp Fallback Fix
# v12.43 share/v Strict Video + Plus Count Fix
# v12.42 PCB Manifest Target + Account Fix
# v12.41 Dialog Header Account Fix
# v12.40 Keep Best Resolution Per Photo Fix
# v12.39 Explicit Story Single-Media Gate
# v12.38 Large Gallery Fast Mode
# v12.37 No Outer Timeout + Browser Launch Recovery
# v12.36 Exact Gallery Best-Available Download Fix
# v12.35 Download Candidate Type Guard Fix
# v12.34 Global Media Pack Guard Fix
# v12.33 Full Media Pack Type Guard Fix
# v12.32 Capture Item Type Guard Fix
# v12.31 Capture Dir Recovery Fix
# v12.30 Verified Capture Recovery + Account UI Fix
# v12.29 Fast Retry Capture Recovery
# v12.28 Viewer Capture Completion Fix
# v12.27 Share/P Gallery Cluster + Account UI Filter Fix
# v12.26 Share/P Gallery Title + Persisted Capture Download Fix
# v12.25 Full Gallery Near-Complete Recovery
# v12.24 Album Context Caption Fix
# v12.23 Album Context Single Photo + Title Split Fix
# v12.22 Full Gallery Viewer Completion Fix
# v12.21 FB Metadata Alias Publish Fix
# v12.20 FB Publish Account + Title Metadata Fix
# v12.19 Story Caption Priority Fix
# v12.18 Reel Foreground Candidate + Story Scoped Title Fix
# v12.17 Reel Restore + Story Proximal Photo Identity Fix
# v11.93 FB Scoped Manifest Expected-Count Fix
import hashlib
import html
import os
import random
import re
import unicodedata
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
    from .fb_contracts import (
        audit_gallery_completion as _contract_audit_gallery_completion,
        parse_facebook_target as _contract_parse_facebook_target,
        derive_gallery_plan as _contract_derive_gallery_plan,
        finalize_gallery_plan as _contract_finalize_gallery_plan,
        choose_post_account as _contract_choose_post_account,
        extract_exact_pcb_photo_ids as _contract_extract_exact_pcb_photo_ids,
    )
except Exception:
    from fb_contracts import (
        audit_gallery_completion as _contract_audit_gallery_completion,
        parse_facebook_target as _contract_parse_facebook_target,
        derive_gallery_plan as _contract_derive_gallery_plan,
        finalize_gallery_plan as _contract_finalize_gallery_plan,
        choose_post_account as _contract_choose_post_account,
        extract_exact_pcb_photo_ids as _contract_extract_exact_pcb_photo_ids,
    )

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
_DL_TIMEOUT = 3600  # v12.37: avoid killing large FB galleries while viewer is still harvesting
_FB_DOWNLOAD_LOCK = threading.RLock()

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

    # v12.22:
    # In exact-count full-gallery mode the viewer already proved this is one
    # required gallery item.  Some FB pages only expose a small best-available
    # source for one slide while high-res variants are HTTP 403.  Keep the gate
    # high enough to reject icons/avatar sprites, but allow the proven viewer
    # item instead of forcing the whole 14-photo post into RETRY.
    if max(w, h) < 300 or min(w, h) < 200:
        return False, f"best-available 尺寸仍過小: actual={w}x{h}"

    return True, f"v12.22 exact-gallery best-available actual={w}x{h}, bytes={size}"


def _download_best_candidate(context, candidates, dst_base: str, referer: str):
    # v12.36: final safety net.  High-res expansion can reintroduce raw URL
    # strings, so normalize before and after expansion.  This prevents the final
    # downloader from crashing with: 'str' object has no attribute 'get'.
    candidates = _fb_v1235_normalize_candidates(candidates)
    candidates = _expand_fb_candidate_highres_variants(candidates)
    candidates = _fb_v1235_normalize_candidates(candidates)
    candidates = _dedupe_ordered(candidates)
    candidates = _fb_v1235_normalize_candidates(candidates)

    if not candidates:
        raise Exception("沒有候選媒體 URL")

    candidates = sorted(
        candidates,
        key=lambda x: int(x.get("score", 0) or 0) if isinstance(x, dict) else 0,
        reverse=True,
    )

    best_tmp = None
    best_size = 0
    best_ext = ".jpg"
    errors = []

    for idx, item in enumerate(candidates[:18], 1):
        if not isinstance(item, dict):
            logger.debug(f"FB v12.36 drop non-dict final candidate: {type(item).__name__}")
            continue
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


def _clean_fb_account_name(raw: str) -> str:
    """Normalize a Facebook page/account name for the GUI Post Account column."""
    value = _to_traditional(str(raw or ""))
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^\(\d+\)\s*", "", value).strip()
    value = value.replace(" | Facebook", "").replace(" - Facebook", "").strip()
    value = value.strip(" ._-，,。:：|")

    bad = {
        "", "facebook", "facebook_post", "facebook reel", "reel", "watch",
        "讚", "留言", "分享", "查看更多", "public", "所有人",
        # v12.27: FB right rail / composer UI labels are not account names.
        "建立貼文", "建立帖子", "建立", "發佈", "發布", "相片", "照片",
        "限時動態", "建立限時動態", "建立限時动态", "新增限時動態",
        "限时动态", "建立限时动态", "動態消息", "首頁", "通知", "朋友", "社團", "Marketplace",
    }
    if value.lower() in bad:
        return ""

    # Avoid using the post caption itself as an account name.
    if len(value) > 80:
        return ""

    return safe_title(value)[:60].strip(" ._-，,。") or ""



def _looks_like_fb_page_name_v1223(value: str) -> bool:
    v = _clean_fb_account_name(value)
    if not v or len(v) > 40:
        return False
    # Pure emoji/symbol snippets like "❤️" are captions/reactions, not page names.
    if not any(ch.isalnum() or ("\u4e00" <= ch <= "\u9fff") for ch in v):
        return False
    if any(x in v for x in ["？", "?", "！", "!", "。", "；", ";", "：", ":", "，", ",", "#", "http"]):
        return False
    if v.count(" ") > 3:
        return False
    return True


def _looks_like_fb_caption_v1223(value: str) -> bool:
    t = _clean_fb_post_title_for_path(value, fallback="")
    if not t:
        return False
    return len(t) >= 14 or any(x in t for x in ["？", "?", "！", "!", "。", "；", ";", "：", ":", "，", ",", "#", "http", "看到了嗎", "❤", "❤️"])


def _split_fb_title_account(title: str) -> tuple[str, str]:
    """Split FB text into (post_title, account).

    v12.23 supports both:
      Account - Caption
      Caption - Account
    """
    raw = html.unescape(_to_traditional(str(title or ""))).strip()
    for sep in [" - ", " ｜ ", " | "]:
        if sep not in raw:
            continue
        left, right = raw.rsplit(sep, 1)
        left_title = _clean_fb_post_title_for_path(left, fallback="")
        right_title = _clean_fb_post_title_for_path(right, fallback="")
        left_account = _clean_fb_account_name(left)
        right_account = _clean_fb_account_name(right)
        left_page = _looks_like_fb_page_name_v1223(left)
        right_page = _looks_like_fb_page_name_v1223(right)
        left_caption = _looks_like_fb_caption_v1223(left)
        right_caption = _looks_like_fb_caption_v1223(right)

        if left_page and right_caption:
            return right_title, left_account
        if left_caption and right_page:
            return left_title, right_account
        if left_title and right_account and right_page:
            return left_title, right_account
        if right_title and left_account:
            return right_title, left_account

    return _clean_fb_post_title_for_path(raw, fallback=""), ""


def _fb_metadata_url_aliases(task_url: str) -> list[str]:
    """Return FB URL aliases that should update the same visible queue row.

    v12.21:
    The queue may store the original story.php URL while the downloader publishes
    from a resolved/canonical URL.  Publish to multiple aliases so both exact and
    identity-based queue matching can update Post Title/Post Account.
    """
    raw = str(task_url or "").strip()
    if not raw:
        return []

    aliases = []
    seen = set()

    def add(u: str):
        u = str(u or "").strip()
        if not u:
            return
        if u in seen:
            return
        seen.add(u)
        aliases.append(u)

    add(raw)
    add(raw.split("#", 1)[0])

    try:
        clean = html.unescape(unquote(raw)).split("#", 1)[0]

        m_story = re.search(r"[?&]story_fbid=([0-9]{8,})", clean, flags=re.I)
        m_id = re.search(r"[?&]id=([0-9]{6,})", clean, flags=re.I)
        if m_story:
            sid = m_story.group(1)
            if m_id:
                add(f"https://www.facebook.com/story.php?story_fbid={sid}&id={m_id.group(1)}")
            add(f"https://www.facebook.com/story.php?story_fbid={sid}")

        m_post = re.search(r"[?&]post_id=([0-9_]{8,})", clean, flags=re.I)
        if m_post:
            add(f"https://www.facebook.com/story.php?post_id={m_post.group(1)}")

        m_fbid = re.search(r"[?&](?:fbid|photo_id)=([0-9]{8,})", clean, flags=re.I)
        if m_fbid:
            add(f"https://www.facebook.com/photo/?fbid={m_fbid.group(1)}")

        m_reel = re.search(r"/(?:reel|reels)/([0-9]{6,})", clean, flags=re.I)
        if m_reel:
            add(f"https://www.facebook.com/reel/{m_reel.group(1)}")
            add(f"https://www.facebook.com/reel/{m_reel.group(1)}/")

        m_watch = re.search(r"[?&]v=([0-9]{6,})", clean, flags=re.I)
        if m_watch:
            add(f"https://www.facebook.com/watch/?v={m_watch.group(1)}")
            add(f"https://www.facebook.com/reel/{m_watch.group(1)}/")

    except Exception:
        pass

    return aliases



def _get_fb_active_post_primary_account_v1249(page) -> str:
    """Return the primary publisher/group/page name from the active post card.

    v12.49 fixes group posts where the persistent FB page also contains unrelated
    background cards.  The previous generic account fallback could pick a nearby
    page (for example, "作夥帕電動") even though the active post header clearly
    showed another publisher/group (for example, "BubuDudu lover").

    Scope is intentionally narrow: active dialog -> visible article -> top header
    area only.  It never searches the whole feed as the first choice.
    """
    try:
        raw = page.evaluate(
            r"""
            () => {
              const badText = (t) => {
                t = String(t || '').replace(/\s+/g, ' ').trim();
                if (!t || t.length < 2 || t.length > 90) return true;
                const low = t.toLowerCase();
                const bad = [
                  'facebook','查看更多','查看貼文','留言','分享','追蹤','已追蹤',
                  '讚','最相關','所有留言','寫留言','建立貼文','建立帖子',
                  'photo','video','reel','reels','首頁','通知','messenger'
                ];
                return bad.some(x => low === x || low.startsWith(x + ' '));
              };

              const candidates = [];
              const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                .filter(el => {
                  const r = el.getBoundingClientRect();
                  return r.width > 240 && r.height > 180 && r.bottom > 0 && r.right > 0;
                });
              const roots = dialogs.length ? dialogs : [document.querySelector('[role="main"]') || document.body];

              for (const root of roots) {
                const articles = Array.from(root.querySelectorAll('[role="article"], article')).filter(el => {
                  const r = el.getBoundingClientRect();
                  return r.width > 200 && r.height > 120 && r.bottom > 0 && r.right > 0;
                });
                const article = articles.sort((a,b) => {
                  const ar=a.getBoundingClientRect(), br=b.getBoundingClientRect();
                  return (br.width*br.height) - (ar.width*ar.height);
                })[0] || root;

                const ar = article.getBoundingClientRect();
                const nodes = Array.from(article.querySelectorAll('a[role="link"], a[href], strong, h2, h3'));
                let domIndex = 0;
                for (const el of nodes.slice(0, 180)) {
                  domIndex += 1;
                  const r = el.getBoundingClientRect();
                  if (r.width <= 0 || r.height <= 0 || r.bottom <= 0 || r.right <= 0) continue;
                  // Publisher/author rows are in the upper portion of the post card.
                  if (r.top > ar.top + Math.min(260, Math.max(150, ar.height * 0.28))) continue;
                  const text = String(el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                  if (badText(text)) continue;
                  const href = (el.href || el.getAttribute('href') || '');
                  const lowHref = href.toLowerCase();
                  if (lowHref.includes('/hashtag/') || lowHref.includes('/photo') || lowHref.includes('/watch') || lowHref.includes('/reel')) continue;
                  const score =
                    (el.tagName === 'A' ? 60 : 0) +
                    (lowHref.includes('/groups/') ? 160 : 0) +
                    (lowHref.includes('facebook.com/') ? 40 : 0) +
                    Math.max(0, 100 - domIndex) +
                    Math.max(0, 100 - Math.abs(r.top - ar.top));
                  candidates.push({text, href, score, top:r.top, index:domIndex});
                }
              }
              candidates.sort((a,b) => b.score - a.score || a.top - b.top || a.index - b.index);
              return candidates.slice(0, 30);
            }
            """
        ) or []
    except Exception:
        raw = []

    for item in raw:
        text = _clean_fb_account_name((item or {}).get('text') if isinstance(item, dict) else str(item or ''))
        if not text:
            continue
        if _looks_like_fb_page_name_v1223(text):
            logger.info(f"FB v12.49 active post primary account selected: {text}")
            return text
    return ""


def _get_fb_dialog_header_account_v1241(page) -> str:
    """Extract author from the active Facebook viewer/dialog/header title.

    v12.42 makes this more aggressive: scan document title, visible headings,
    aria labels and body lines for "<account> 的貼文/的帖子".  This avoids using
    background feed cards as Post Account when the active viewer belongs to a
    different page such as "story halaman tv".
    """
    try:
        raw = page.evaluate(
            r"""
            () => {
              const out = [];
              const push = (v) => {
                const t = String(v || '').replace(/\s+/g, ' ').trim();
                if (!t) return;
                if (t.length > 160) return;
                if (t.includes('的貼文') || t.includes('的帖子') || t.includes("'s post")) out.push(t);
              };

              push(document.title);

              const roots = [];
              for (const d of Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))) roots.push(d);
              const main = document.querySelector('[role="main"]');
              if (main) roots.push(main);
              roots.push(document.body || document);

              for (const root of roots) {
                const nodes = Array.from(root.querySelectorAll('h1, h2, h3, [aria-label], [role="heading"], a[role="link"], span, div'));
                for (const el of nodes.slice(0, 1800)) {
                  const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                  if (rect && (rect.width <= 0 || rect.height <= 0)) continue;
                  push(el.getAttribute && el.getAttribute('aria-label'));
                  push(el.innerText || el.textContent || '');
                }

                const bodyText = (root.innerText || root.textContent || '').split(/\n+/).slice(0, 260);
                for (const line of bodyText) push(line);
              }

              return Array.from(new Set(out)).slice(0, 120);
            }
            """
        ) or []
    except Exception:
        raw = []

    blacklist = {
        "facebook", "首頁", "通知", "建立貼文", "建立帖子", "留言", "分享",
        "讚", "最相關", "查看更多", "你的貼文", "你的帖子",
    }

    for cand in raw:
        s = _to_traditional(html.unescape(str(cand or ""))).strip()
        # title can be "story halaman tv 的貼文 | Facebook"
        s = re.sub(r"\s*[|｜]\s*Facebook\s*$", "", s, flags=re.I).strip()
        m = re.search(r"^(.{2,60}?)\s*的(?:貼文|帖子)\s*$", s)
        if not m:
            m = re.search(r"^(.{2,60}?)\s*'s post\s*$", s, flags=re.I)
        if not m:
            continue
        account = _clean_fb_account_name(m.group(1))
        if not account or account in blacklist:
            continue
        if account and _looks_like_fb_page_name_v1223(account):
            logger.info(f"FB v12.42 dialog/header account selected: {account}")
            return account

    return ""




def _fb_v132_bad_account_label(text: str) -> bool:
    t = _clean_fb_account_name(text or "")
    if not t:
        return True
    low = t.lower()
    bad_exact = {
        "在線上", "上線狀態指標 在線上", "online", "active now",
        "查看貼文", "查看更多", "追蹤", "已追蹤", "建立貼文",
    }
    if low in bad_exact:
        return True
    if "上線狀態" in t or "active status" in low:
        return True
    return False


def _fb_v131_get_group_container_account(page, dominant_pcb_key: str = "") -> str:
    """Return the group/container identity for an exact-PCB group post.

    Post Account in the GUI represents the shared post's container.  For group
    posts this is the group name, not the member/author shown underneath it.
    The lookup is exact-post scoped when the pcb id is available.
    """
    pcb = str(dominant_pcb_key or "")
    if pcb.startswith("pcb:"):
        pcb = pcb.split(":", 1)[1]
    pcb = re.sub(r"\D", "", pcb)
    try:
        rows = page.evaluate(
            r"""
            (pcb) => {
              const visible = el => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
              };
              const clean = t => String(t || '').replace(/\s+/g,' ').trim();
              const roots = [];
              if (pcb) {
                for (const a of Array.from(document.querySelectorAll('a[href]'))) {
                  const h = String(a.href || a.getAttribute('href') || '');
                  if (!(h.includes('set=pcb.' + pcb) || h.includes('set=pcb%2E' + pcb))) continue;
                  const art = a.closest('[role="article"], article');
                  if (art && !roots.includes(art)) roots.push(art);
                }
              }
              if (!roots.length) {
                const art = document.querySelector('[role="article"], article');
                if (art) roots.push(art);
              }
              if (!roots.length) roots.push(document.querySelector('[role="main"]') || document.body);

              const out = [];
              for (const root of roots.slice(0, 4)) {
                const rr = root.getBoundingClientRect();
                for (const a of Array.from(root.querySelectorAll('a[href]')).slice(0, 320)) {
                  if (!visible(a)) continue;
                  const href = String(a.href || a.getAttribute('href') || '');
                  let u;
                  try { u = new URL(href, location.href); } catch(e) { continue; }
                  const seg = u.pathname.split('/').filter(Boolean);
                  if (seg.length !== 2 || seg[0].toLowerCase() !== 'groups' || !seg[1]) continue;
                  const text = clean(a.innerText || a.textContent || a.getAttribute('aria-label'));
                  if (!text || text.length > 120) continue;
                  const r = a.getBoundingClientRect();
                  let score = 5000;
                  if (r.top <= rr.top + Math.min(420, Math.max(220, rr.height * 0.42))) score += 1500;
                  score -= Math.max(0, r.top - rr.top) * 0.2;
                  out.push({text, href:u.href, score, top:r.top});
                }
              }
              // v13.2: some group shares expose no clickable group-root anchor in the
              // virtualized dialog.  Use only the exact post/dialog heading as a
              // same-container fallback; never scan arbitrary feed text.
              for (const root of roots.slice(0, 4)) {
                const heads = Array.from(root.querySelectorAll('h1,h2,h3,[role="heading"]')).filter(visible);
                for (const h of heads.slice(0, 20)) {
                  const text = clean(h.innerText || h.textContent || h.getAttribute('aria-label'));
                  if (!text || text.length > 140) continue;
                  const m = text.match(/^(.{2,100}?)\s*(?:的貼文|的帖子|\'s post)$/i);
                  if (m && m[1]) out.push({text: clean(m[1]), href:'', score:4800, top:h.getBoundingClientRect().top});
                }
              }
              out.sort((a,b) => b.score - a.score || a.top - b.top);
              return out.slice(0, 30);
            }
            """,
            pcb,
        ) or []
    except Exception:
        rows = []

    for item in rows:
        if not isinstance(item, dict):
            continue
        text = _clean_fb_account_name(item.get("text") or "")
        if text and not _fb_v132_bad_account_label(text) and _looks_like_fb_page_name_v1223(text):
            logger.info(f"FB v13.2 group-container account selected: {text}")
            return text
    return ""


def _get_fb_exact_pcb_account_v1250(page, dominant_pcb_key: str = "") -> str:
    """Resolve the account/group from the exact active pcb post only.

    v12.51:
    - Group posts must publish the group/page identity, not the individual author.
    - Prefer the shallow /groups/<id-or-slug>/ anchor in the exact active post header.
    - Deeper /groups/<id>/user/... or /groups/<id>/posts/... links are treated as
      author/post links and cannot override the group root.
    """
    pcb = str(dominant_pcb_key or "")
    if pcb.startswith("pcb:"):
        pcb = pcb.split(":", 1)[1]
    pcb = re.sub(r"\D", "", pcb)

    try:
        rows = page.evaluate(
            r"""
            (pcb) => {
              const visible = (el) => {
                if (!el) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && r.bottom > 0 && r.right > 0;
              };
              const clean = (v) => String(v || '').replace(/\s+/g, ' ').trim();
              const bad = (t) => {
                const low = clean(t).toLowerCase();
                if (!low || low.length < 2 || low.length > 90) return true;
                return [
                  'facebook','查看貼文','查看更多','留言','分享','讚','追蹤','已追蹤',
                  '最相關','所有留言','寫留言','建立貼文','通知','messenger'
                ].some(x => low === x || low.startsWith(x + ' '));
              };
              const groupDepth = (href) => {
                try {
                  const u = new URL(href, location.href);
                  const seg = u.pathname.split('/').filter(Boolean);
                  const i = seg.indexOf('groups');
                  if (i < 0) return 99;
                  return Math.max(0, seg.length - (i + 2));
                } catch (_) {
                  return 99;
                }
              };

              const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]')).filter(visible);
              const roots = dialogs.length ? dialogs : [document.querySelector('[role="main"]') || document.body];
              const scored = [];

              for (const root0 of roots) {
                let roots2 = [root0];
                if (pcb) {
                  const exact = Array.from(root0.querySelectorAll('a[href]')).filter(a => {
                    const h = String(a.href || a.getAttribute('href') || '');
                    return h.includes('set=pcb.' + pcb) || h.includes('set=pcb%2E' + pcb);
                  });
                  for (const a of exact) {
                    const art = a.closest('[role="article"], article');
                    if (art && !roots2.includes(art)) roots2.unshift(art);
                  }
                }

                for (const root of roots2) {
                  const rr = root.getBoundingClientRect();
                  const links = Array.from(root.querySelectorAll('a[href]')).filter(visible);
                  let idx = 0;
                  for (const a of links.slice(0, 260)) {
                    idx += 1;
                    const text = clean(a.innerText || a.textContent || a.getAttribute('aria-label'));
                    if (bad(text)) continue;
                    const href = String(a.href || a.getAttribute('href') || '');
                    const lowHref = href.toLowerCase();
                    if (!lowHref.includes('facebook.com') && !lowHref.startsWith('/')) continue;
                    if (lowHref.includes('/photo') || lowHref.includes('/reel') || lowHref.includes('/watch') || lowHref.includes('/hashtag/')) continue;

                    const r = a.getBoundingClientRect();
                    const gd = groupDepth(href);
                    let score = Math.max(0, 260 - idx);

                    if (lowHref.includes('/groups/')) {
                      score += 1500;
                      if (gd === 0) score += 4200;       // exact group root: strongest identity
                      else if (gd === 1) score += 300;  // tolerate one harmless segment
                      else score -= 1800;               // author/post/member links inside group
                    }
                    if (lowHref.includes('/profile.php')) score -= 500;
                    if (/facebook\.com\/[A-Za-z0-9._-]+\/?(?:\?|$)/.test(lowHref)) score += 240;
                    if (r.top <= rr.top + Math.min(360, Math.max(180, rr.height * 0.35))) score += 650;
                    if (pcb && (href.includes('pcb.' + pcb) || href.includes('pcb%2E' + pcb))) score += 350;

                    scored.push({text, href, score, top:r.top, groupDepth:gd});
                  }
                }
              }
              scored.sort((a,b) => b.score - a.score || a.top - b.top);
              return scored.slice(0, 60);
            }
            """,
            pcb,
        ) or []
    except Exception:
        rows = []

    # v12.51: first pass only accepts the shallow group-root identity.
    for item in rows:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "")
        text = _clean_fb_account_name(item.get("text") or "")
        try:
            gd = int(item.get("groupDepth", 99))
        except Exception:
            gd = 99
        if (
            "/groups/" in href.lower()
            and gd == 0
            and text
            and not _fb_v132_bad_account_label(text)
            and _looks_like_fb_page_name_v1223(text)
        ):
            logger.info(f"FB v12.51 exact-pcb group root account selected: {text}")
            return text

    # Second pass: accept only high-scored group links that are not deep member/post links.
    for item in rows:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "")
        text = _clean_fb_account_name(item.get("text") or "")
        try:
            gd = int(item.get("groupDepth", 99))
            score = int(item.get("score", 0))
        except Exception:
            gd, score = 99, 0
        if (
            "/groups/" in href.lower()
            and gd <= 1
            and score >= 1800
            and text
            and not _fb_v132_bad_account_label(text)
            and _looks_like_fb_page_name_v1223(text)
        ):
            logger.info(f"FB v12.51 exact-pcb group account selected: {text}")
            return text

    # Non-group page posts keep the previous conservative fallback.
    for item in rows:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "")
        if "/groups/" in href.lower():
            continue
        text = _clean_fb_account_name(item.get("text") or "")
        if text and not _fb_v132_bad_account_label(text) and _looks_like_fb_page_name_v1223(text):
            logger.info(f"FB v13.2 exact-pcb account selected: {text}")
            return text
    return ""


def _get_fb_page_account(page, fallback_title: str = "") -> str:
    """Best-effort FB page/account extraction for GUI metadata.

    This is deliberately conservative: it only returns short page-like names and
    never returns a long caption.  The download identity gates remain unchanged.
    """
    # v12.41: For FB viewer/share dialogs, the visible header "<account> 的貼文"
    # is more reliable than background feed/article links.
    dialog_account = _get_fb_dialog_header_account_v1241(page)
    if dialog_account:
        return dialog_account

    title_part, account_from_title = _split_fb_title_account(fallback_title)
    if account_from_title:
        return account_from_title

    candidates = []

    # Meta/site strings sometimes contain "caption - PageName".
    for sel in [
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
        'meta[property="og:site_name"]',
        'meta[property="og:description"]',
    ]:
        try:
            val = page.locator(sel).first.get_attribute("content") or ""
            if val:
                _t, acc = _split_fb_title_account(val)
                if acc:
                    candidates.append(acc)
                else:
                    candidates.append(val)
        except Exception:
            pass

    # Article/header links are useful for page names, but keep only short values.
    try:
        raw = page.evaluate(
            r"""
            () => {
              const out = [];
              const roots = [];
              const main = document.querySelector('[role="main"]') || document;
              roots.push(main);
              const article = document.querySelector('[role="article"], article');
              if (article) roots.unshift(article);

              for (const root of roots) {
                const nodes = Array.from(root.querySelectorAll('h1, h2, h3, strong, a[role="link"], a[href]'));
                for (const el of nodes.slice(0, 80)) {
                  const t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                  if (t.length >= 2 && t.length <= 80) out.push(t);
                }
              }
              return out.slice(0, 40);
            }
            """
        ) or []
        candidates.extend(raw)
    except Exception:
        pass

    for cand in candidates:
        clean = _clean_fb_account_name(cand)
        if not clean:
            continue
        # Reject obvious post captions and UI fragments.
        if any(x in clean for x in ["這10種行為", "毀掉你的人生", "麻醉後", "牙醫可以"]):
            continue
        return clean

    return ""


def _publish_fb_task_account(task_url: str, account: str) -> str:
    clean = _clean_fb_account_name(account)
    if not clean:
        return ""
    try:
        import queue_manager
        queue_manager.update_task_account(task_url, clean)
    except Exception as e:
        logger.debug(f"FB task account publish skipped: {e}")
    return clean


def _publish_fb_task_metadata(task_url: str, title: str, account: str = "", page=None, account_locked: bool = False) -> tuple[str, str]:
    """Publish both Post Title and Post Account to the queue/UI.

    v12.21:
    Publish through URL aliases and report whether queue_manager accepted the
    update.  This fixes successful FB story rows that kept blank GUI metadata
    because the downloader URL and GUI row URL were not exact string matches.
    """
    split_title, split_account = _split_fb_title_account(title)
    final_title = split_title or _clean_fb_post_title_for_path(title, fallback="")
    final_account = _clean_fb_account_name(account or split_account)

    if page is not None:
        primary_account_v1249 = _get_fb_active_post_primary_account_v1249(page)
        dialog_account = _get_fb_dialog_header_account_v1241(page)
        if account_locked and final_account:
            logger.info(f"FB v12.50 metadata account lock preserved: {final_account}")
        elif primary_account_v1249:
            final_account = primary_account_v1249
        elif dialog_account:
            final_account = dialog_account
        elif not final_account:
            final_account = _get_fb_page_account(page, fallback_title=title)

    title_ok = False
    account_ok = False
    aliases = _fb_metadata_url_aliases(task_url)

    for alias in aliases:
        if final_title:
            try:
                title_ok = bool(_publish_fb_task_title(alias, final_title)) or title_ok
            except Exception:
                pass
        if final_account:
            try:
                account_ok = bool(_publish_fb_task_account(alias, final_account)) or account_ok
            except Exception:
                pass

    logger.info(
        f"FB v12.21 publish metadata: "
        f"title={final_title or '-'}, account={final_account or '-'}, "
        f"title_ok={title_ok}, account_ok={account_ok}, aliases={len(aliases)}"
    )
    return final_title, final_account



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

            clean, account = _publish_fb_task_metadata(url, title, page=page)
            if resolved and resolved != url and clean:
                _publish_fb_task_metadata(resolved, clean, account, page=page)
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

def _fb_media_id_from_src(src: str) -> str:
    """v12.26 compatibility alias for recovery code.

    v12.25 called _fb_media_id_from_src() but the existing helper name in this
    file is _fb_media_numeric_id_str_from_src().  The missing alias made the
    near-complete recovery throw NameError before it could help 13/14 galleries.
    """
    return _fb_media_numeric_id_str_from_src(src)









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

def _fb_v1232_pack_dicts(seq) -> list[dict]:
    """Return only dict media packs.

    v12.31 can append response-capture candidates into the same sequence used by
    older gallery/link logic.  Some legacy helpers can also leak raw href strings
    into the merged item list.  Older functions assumed every element has .get()
    and crashed with: "'str' object has no attribute 'get'".  This guard keeps
    the strict completeness checks but prevents a type error from converting a
    recoverable gallery into FAILED.
    """
    out = []
    for item in seq or []:
        if isinstance(item, dict):
            out.append(item)
        elif item:
            try:
                logger.debug(f"FB v12.32 drop non-dict media item: {type(item).__name__}")
            except Exception:
                pass
    return out





def _drop_video_packs_for_photo_post(packs: list[dict]) -> list[dict]:
    """In a photo post target, never let video/ad/reel responses count as missing photos."""
    out = []
    for pack in _fb_v1232_pack_dicts(packs):
        src = pack.get("src") or ""
        if pack.get("type") == "video" or _is_probably_video_url(src):
            logger.info(f"FB v11.16 drop video/non-photo candidate: {os.path.basename(urlparse(str(src).split('?')[0]).path)}")
            continue
        out.append(pack)
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



def _fb_v1249_collect_hidden_pcb_photo_links(page, dominant_pcb_key: str) -> list[str]:
    """Recover hidden +N photo permalinks from the exact set=pcb post payload.

    Only URLs that explicitly carry the already-proven pcb id are accepted, so
    this cannot widen into recommendations or another album/post.
    """
    pcb = str(dominant_pcb_key or '')
    if pcb.startswith('pcb:'):
        pcb = pcb.split(':', 1)[1]
    pcb = re.sub(r'\D', '', pcb)
    if not pcb:
        return []
    try:
        text = page.content() or ''
    except Exception:
        return []

    variants = [text, html.unescape(text)]
    out = []
    seen = set()
    for raw in variants:
        dec = str(raw or '')
        dec = dec.replace('\\/', '/').replace('\\u002F', '/').replace('\\u002f', '/')
        dec = dec.replace('\\u0026', '&').replace('\\u003D', '=').replace('\\u003d', '=')
        dec = dec.replace('\\u002E', '.').replace('\\u002e', '.')

        # Explicit photo URLs carrying the exact pcb id.
        pats = [
            rf'(?:https?:)?//(?:www\.)?facebook\.com/photo/\?[^\"\'<>\s]{{0,500}}?fbid=(\d{{8,}})[^\"\'<>\s]{{0,500}}?set=pcb\.{pcb}',
            rf'(?:https?:)?//(?:www\.)?facebook\.com/photo/\?[^\"\'<>\s]{{0,500}}?set=pcb\.{pcb}[^\"\'<>\s]{{0,500}}?fbid=(\d{{8,}})',
            rf'/photo/\?[^\"\'<>\s]{{0,500}}?fbid=(\d{{8,}})[^\"\'<>\s]{{0,500}}?set=pcb\.{pcb}',
            rf'/photo/\?[^\"\'<>\s]{{0,500}}?set=pcb\.{pcb}[^\"\'<>\s]{{0,500}}?fbid=(\d{{8,}})',
        ]
        for pat in pats:
            for m in re.finditer(pat, dec, flags=re.I):
                fbid = m.group(1)
                if fbid in seen:
                    continue
                seen.add(fbid)
                out.append(f'https://www.facebook.com/photo/?fbid={fbid}&set=pcb.{pcb}')

        # Encoded query fields in serialized JSON.  Keep the relation tight.
        for m in re.finditer(rf'fbid(?:=|%3D|\\u003D)(\d{{8,}}).{{0,650}}?set(?:=|%3D|\\u003D)pcb(?:\.|%2E|\\u002E){pcb}', dec, flags=re.I|re.S):
            fbid = m.group(1)
            if fbid not in seen:
                seen.add(fbid)
                out.append(f'https://www.facebook.com/photo/?fbid={fbid}&set=pcb.{pcb}')
        for m in re.finditer(rf'set(?:=|%3D|\\u003D)pcb(?:\.|%2E|\\u002E){pcb}.{{0,650}}?fbid(?:=|%3D|\\u003D)(\d{{8,}})', dec, flags=re.I|re.S):
            fbid = m.group(1)
            if fbid not in seen:
                seen.add(fbid)
                out.append(f'https://www.facebook.com/photo/?fbid={fbid}&set=pcb.{pcb}')

    logger.info(f'FB v12.49 hidden exact-pcb photo links={len(out)} pcb=pcb:{pcb}')
    return out




def _fb_v131_collect_exact_pcb_links_from_payloads(payload_texts, dominant_pcb_key: str) -> list[str]:
    """Recover same-post photo permalinks from captured GraphQL/JSON payloads.

    Only IDs explicitly tied to the already-proven set=pcb id are accepted.
    This closes the common 7/8 gap without scanning neighboring/recommended
    posts and without relaxing the final completeness guard.
    """
    pcb = str(dominant_pcb_key or "")
    if pcb.startswith("pcb:"):
        pcb = pcb.split(":", 1)[1]
    pcb = re.sub(r"\D", "", pcb)
    if not pcb:
        return []
    try:
        ids = _contract_extract_exact_pcb_photo_ids(payload_texts or [], pcb)
    except Exception as e:
        logger.debug(f"FB v13.2 exact-pcb payload parser skipped: {e}")
        return []
    out = [f"https://www.facebook.com/photo/?fbid={fbid}&set=pcb.{pcb}" for fbid in ids]
    if out:
        logger.info(f"FB v13.2 exact-pcb structured payload links={len(out)} pcb=pcb:{pcb}")
    return out


def _fb_v1250_discover_exact_pcb_links_from_viewer(
    context,
    start_links: list[str],
    dominant_pcb_key: str,
    expected_count: int | None = None,
) -> list[str]:
    """Deterministically enumerate exact-pcb photo identities from Facebook viewer.

    v13.4 replaces the old short 6-12 turn probe with an identity walker:
    - same proven set=pcb.<id> only
    - records live anchors, current URL, serialized HTML and navigation hrefs
    - keeps walking through temporary stale turns instead of stopping after 3
    - scales to large galleries (e.g. 81 photos) with a bounded target+margin budget
    - never widens into recommendations or changes the expected count
    """
    pcb = str(dominant_pcb_key or "")
    if pcb.startswith("pcb:"):
        pcb = pcb.split(":", 1)[1]
    pcb = re.sub(r"\D", "", pcb)
    if not pcb:
        return []

    try:
        target = max(1, int(expected_count or 0))
    except Exception:
        target = 1

    seeds: list[str] = []
    for u in start_links or []:
        su = html.unescape(str(u or "")).replace("\\/", "/")
        if not _is_true_photo_link(su):
            continue
        if f"set=pcb.{pcb}" not in su and f"set=pcb%2E{pcb}" not in su:
            continue
        if su not in seeds:
            seeds.append(su)
    if not seeds:
        return []

    found: list[str] = []
    seen: set[str] = set()

    def add_urls(urls) -> int:
        before = len(found)
        for u in urls or []:
            su = html.unescape(str(u or "")).replace("\\/", "/")
            su = su.replace("\\u0026", "&").replace("\\u003D", "=").replace("\\u003d", "=")
            su = su.replace("\\u002E", ".").replace("\\u002e", ".")
            if not _is_true_photo_link(su):
                continue
            if f"set=pcb.{pcb}" not in su and f"set=pcb%2E{pcb}" not in su:
                continue
            m = re.search(r"[?&]fbid=(\d{8,})", su, flags=re.I)
            if not m:
                continue
            fbid = m.group(1)
            if fbid in seen:
                continue
            seen.add(fbid)
            found.append(f"https://www.facebook.com/photo/?fbid={fbid}&set=pcb.{pcb}")
        return len(found) - before

    def add_from_serialized_text(raw: str) -> int:
        if not raw:
            return 0
        dec = html.unescape(str(raw)).replace("\\/", "/")
        dec = dec.replace("\\u002F", "/").replace("\\u002f", "/")
        dec = dec.replace("\\u0026", "&").replace("\\u003D", "=").replace("\\u003d", "=")
        dec = dec.replace("\\u002E", ".").replace("\\u002e", ".")
        urls = []
        patterns = [
            rf'(?:https?:)?//(?:www\.)?facebook\.com/photo/\?[^"\'<>\s]{{0,900}}?fbid=(\d{{8,}})[^"\'<>\s]{{0,900}}?set=pcb\.{pcb}',
            rf'(?:https?:)?//(?:www\.)?facebook\.com/photo/\?[^"\'<>\s]{{0,900}}?set=pcb\.{pcb}[^"\'<>\s]{{0,900}}?fbid=(\d{{8,}})',
            rf'fbid(?:=|%3D|\\u003D)(\d{{8,}}).{{0,1200}}?set(?:=|%3D|\\u003D)pcb(?:\.|%2E|\\u002E){pcb}',
            rf'set(?:=|%3D|\\u003D)pcb(?:\.|%2E|\\u002E){pcb}.{{0,1200}}?fbid(?:=|%3D|\\u003D)(\d{{8,}})',
        ]
        for pat in patterns:
            for m in re.finditer(pat, dec, flags=re.I | re.S):
                urls.append(f"https://www.facebook.com/photo/?fbid={m.group(1)}&set=pcb.{pcb}")
        return add_urls(urls)

    add_urls(seeds)

    # For small galleries, one seed is usually enough. For large/virtualized
    # galleries use up to three distinct entry points so Facebook cannot trap us
    # in one preloaded segment.
    seed_budget = min(3, len(seeds))
    per_seed_turns = min(120, max(14, target + 14))
    stale_limit = 12 if target <= 20 else 24

    for seed_idx, seed in enumerate(seeds[:seed_budget], 1):
        if target and len(found) >= target:
            break
        p = None
        try:
            p = context.new_page()
            p.goto(seed, wait_until="domcontentloaded", timeout=45000)
            p.wait_for_timeout(900)
            try:
                _open_viewer_from_photo_page(p)
            except Exception:
                pass
            _focus_viewer(p)

            stale = 0
            last_url = ""
            for turn in range(per_seed_turns):
                before = len(found)

                # 1) live exact-pcb anchors, including virtualized neighbors
                try:
                    hrefs = p.evaluate(
                        r"""
                        (pcb) => Array.from(document.querySelectorAll('a[href]'))
                          .map(a => a.href || a.getAttribute('href') || '')
                          .filter(h => h && (
                              h.includes('set=pcb.' + pcb) ||
                              h.includes('set=pcb%2E' + pcb)
                          ))
                        """,
                        pcb,
                    ) or []
                    add_urls(hrefs)
                except Exception:
                    pass

                # 2) current URL may become the new exact photo permalink even
                # when Facebook does not materialize it as an anchor.
                try:
                    now_url = str(p.url or "")
                    if now_url != last_url:
                        add_urls([now_url])
                        last_url = now_url
                except Exception:
                    pass

                # 3) serialized Relay/Comet state often contains previous/next
                # exact photo ids that are not live DOM anchors yet.
                try:
                    add_from_serialized_text(p.content() or "")
                except Exception:
                    pass

                if target and len(found) >= target:
                    logger.info(
                        f"FB v13.6 exact-pcb identity walker complete: "
                        f"found={len(found)}/{target}, seed={seed_idx}, turn={turn}"
                    )
                    break

                if len(found) == before:
                    stale += 1
                else:
                    stale = 0

                # Never stop on the old 3-stale rule. Facebook can spend several
                # turns reusing the same physical image while its identity state
                # advances asynchronously.
                if stale >= stale_limit:
                    logger.info(
                        f"FB v13.6 exact-pcb identity walker stale stop: "
                        f"found={len(found)}/{target}, seed={seed_idx}, stale={stale}"
                    )
                    break

                # Strong deterministic navigation: DOM next first, then keyboard,
                # then physical fallback only if the URL/identity did not advance.
                try:
                    advanced = _click_dom_next_strong(p)
                except Exception:
                    advanced = False
                if not advanced:
                    try:
                        _focus_viewer(p)
                        p.keyboard.press("ArrowRight")
                    except Exception:
                        pass
                p.wait_for_timeout(360)
                if len(found) == before:
                    try:
                        _force_viewer_next(p, turn % 5)
                    except Exception:
                        pass
                    p.wait_for_timeout(420)

        except Exception as e:
            logger.debug(
                f"FB v13.6 exact-pcb identity walker seed={seed_idx} skipped: {e}"
            )
        finally:
            try:
                if p:
                    p.close()
            except Exception:
                pass

    logger.info(
        f"FB v13.6 exact-pcb identity walker result: "
        f"found={len(found)}, target={target or '-'}, pcb=pcb:{pcb}"
    )
    return found

def _fb_v1251_near_complete_tail_recovery(
    context,
    exact_links: list[str],
    current_items: list[dict],
    expected_count: int,
    dominant_pcb_key: str,
) -> list[dict]:
    """One bounded high-effort pass for exact-pcb galleries missing exactly one item.

    This runs only for the narrow N-1 case.  It starts from the last exact-pcb
    permalink, keeps the normal duplicate/content guards, and gives the viewer
    more stale budget than the normal fast path.  It does not change the expected
    count and never turns an incomplete gallery into SUCCESS.
    """
    try:
        expected = int(expected_count or 0)
    except Exception:
        expected = 0
    if expected <= 1:
        return list(current_items or [])

    current = _dedupe_items_by_media_id(list(current_items or []))
    if len(current) != expected - 1:
        return current

    pcb = str(dominant_pcb_key or "")
    if pcb.startswith("pcb:"):
        pcb = pcb.split(":", 1)[1]
    pcb = re.sub(r"\\D", "", pcb)
    if not pcb:
        return current

    seeds = []
    for u in exact_links or []:
        su = str(u or "")
        if not _is_true_photo_link(su):
            continue
        if f"set=pcb.{pcb}" not in su and f"set=pcb%2E{pcb}" not in su:
            continue
        if su not in seeds:
            seeds.append(su)
    if not seeds:
        return current

    # Start from the tail first; FB's virtualized next control often exposes the
    # missing final slide from this position while the first seed loops early.
    chosen = [seeds[-1]]
    if len(seeds) > 1:
        chosen.append(seeds[0])

    merged = list(current)
    logger.info(
        f"FB v13.2 bounded near-complete recovery start: current={len(current)}/{expected}, "
        f"seeds={len(chosen)}, pcb=pcb:{pcb}"
    )
    for idx, seed in enumerate(chosen[:1], 1):
        seq = _collect_viewer_sequence_from_url(
            context,
            seed,
            label=f"v12.51-tail{idx}",
            is_photo_page=True,
            target_count=expected,
            stale_threshold=6,
            max_turns=max(16, min(28, expected + 12)),
            allowed_cluster=None,
            fast_mode=False,
        )
        merged = _dedupe_items_by_media_id(_aggregate_unique_items(merged, seq))
        logger.info(
            f"FB v13.2 bounded near-complete recovery progress: seed={idx}, "
            f"unique={len(merged)}/{expected}"
        )
        if len(merged) >= expected:
            break

    return merged


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
                item = {"type": "image", "src": u, "score": score}
                # v12.26: persist tile-page response immediately.  FB CDN URLs
                # can become 403 by the final download stage even though the
                # tile page just delivered the bytes.
                try:
                    body = resp.body()
                    if body and len(body) >= _MIN_FILE_SIZE:
                        h = hashlib.md5(body).hexdigest()[:16]
                        ext = _ext_from_url(u, ".jpg")
                        cap_dir = os.path.join(TEMP_DIR, "_fb_capture")
                        os.makedirs(cap_dir, exist_ok=True)
                        cap_path = os.path.join(cap_dir, f"tile_{index:04d}_{h}{ext}")
                        if not os.path.exists(cap_path):
                            with open(cap_path, "wb") as f:
                                f.write(body)
                        item["temp_path"] = cap_path
                        item["persisted_path"] = cap_path
                        item["body_size"] = len(body)
                        item["content_length"] = max(clen, len(body))
                        item["score"] += min(len(body), 1000000)
                except Exception:
                    pass
                bucket.append(item)
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

def _collect_viewer_sequence_intercept(page, *, label: str = "viewer", target_count: int | None = None, stale_threshold: int = 3, max_turns: int | None = None, allowed_cluster: str | None = None, fast_mode: bool = False) -> list[dict]:
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

    max_turns = int(max_turns or _MAX_FB_ITEMS)
    hard_stale_stop = (max(3, int(stale_threshold) + 1) if fast_mode else max(5, int(stale_threshold) + 2))

    # v12.38/v12.42:
    # Configure timing before the first probe.  Previous code assigned
    # first_probe_rounds after using it, so viewer warmup failed with:
    # "cannot access local variable 'first_probe_rounds'".
    large_gallery_fast_mode = bool(target_count and int(target_count or 0) >= 30)
    bounded_fast_mode = bool(fast_mode and not large_gallery_fast_mode)
    first_probe_rounds = 3 if large_gallery_fast_mode else (4 if bounded_fast_mode else 8)
    first_probe_wait_ms = 260 if large_gallery_fast_mode else (320 if bounded_fast_mode else 520)

    _focus_viewer(page)

    # 第一張：warmup 後先快速收割一次，不再等 key 變化。
    for _ in range(first_probe_rounds):
        page.wait_for_timeout(first_probe_wait_ms)
        if harvest_once("initial"):
            break

    # v12.38:
    # Large share/p galleries such as "+76" can legitimately mean ~80 photos.
    # The old viewer loop used conservative waits for every slide; this is safe
    # but makes 50+ photo albums look stuck for 40+ minutes.  For large proven
    # gallery targets, keep the same identity/completeness gates but reduce
    # per-slide wait/attempt budgets.  Small posts keep the legacy timing.
    max_attempts_per_turn = 3 if large_gallery_fast_mode else (3 if bounded_fast_mode else 6)
    waits_per_attempt = 3 if large_gallery_fast_mode else (3 if bounded_fast_mode else 8)
    jitter_base_ms = 180 if large_gallery_fast_mode else (180 if bounded_fast_mode else 420)
    jitter_var_ms = 120 if large_gallery_fast_mode else (120 if bounded_fast_mode else 280)
    change_timeout_ms = 650 if large_gallery_fast_mode else (700 if bounded_fast_mode else 1200)
    changed_waits = 2 if large_gallery_fast_mode else (2 if bounded_fast_mode else 5)
    changed_wait_ms = 220 if large_gallery_fast_mode else (220 if bounded_fast_mode else 350)
    stale_primary_waits = 3 if large_gallery_fast_mode else (3 if bounded_fast_mode else 6)
    stale_secondary_waits = 3 if large_gallery_fast_mode else (3 if bounded_fast_mode else 7)
    stale_wait_ms = 220 if large_gallery_fast_mode else (220 if bounded_fast_mode else 360)

    if large_gallery_fast_mode:
        logger.info(
            f"FB v12.38 large-gallery fast mode enabled: target={target_count}, "
            f"max_turns={max_turns}, attempts={max_attempts_per_turn}, waits={waits_per_attempt}"
        )

    for turn in range(max_turns):
        if target_count and len(collected) >= target_count:
            logger.info(f"FB viewer-intercept {label}: 已達目標張數 target={target_count}")
            break
        before_count = len(collected)
        before_key = _main_viewer_key(page)

        # 翻頁前先聚焦；先用 ArrowRight，再配合熱區/按鈕。不要把 wait_viewer_change 當唯一成功條件。
        _focus_viewer(page)
        moved = False
        for attempt in range(max_attempts_per_turn):
            _force_viewer_next(page, attempt)

            # 快速收割：只要 bucket 或 DOM 產生新圖就收，不要求 URL/key 一定變化後才收。
            for wait_i in range(waits_per_attempt):
                _fb_jitter_wait(page, jitter_base_ms, jitter_var_ms)
                if harvest_once(f"turn={turn},attempt={attempt},wait={wait_i}"):
                    moved = True
                    break
            if moved:
                break

            # 沒收割到才檢查視覺 key 是否變化；若有變化，再補抓目前可見圖。
            if _wait_viewer_change(page, before_key, timeout_ms=change_timeout_ms):
                for wait_i in range(changed_waits):
                    page.wait_for_timeout(changed_wait_ms)
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
                for wait_i in range(stale_primary_waits):
                    page.wait_for_timeout(stale_wait_ms)
                    if harvest_once(f"stale-physical turn={turn},wait={wait_i}"):
                        break

                if len(collected) == before_count and stale >= 2:
                    _physical_drive_next(page, reason=f"stale{stale}-turn{turn}-secondary")
                    for wait_i in range(stale_secondary_waits):
                        page.wait_for_timeout(stale_wait_ms)
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

def _collect_viewer_sequence_from_url(context, start_url: str, *, label: str, is_photo_page: bool = False, target_count: int | None = None, stale_threshold: int = 3, max_turns: int | None = None, allowed_cluster: str | None = None, fast_mode: bool = False) -> list[dict]:
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
            fast_mode=fast_mode,
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
    """Open one exact photo permalink and collect only that foreground photo.

    v13.2 adds an exact-photo response fallback for the 7/8 class of failures:
    Facebook can expose a proven set=pcb photo permalink but render no usable
    <img> candidate for that slide.  We capture image responses only while the
    exact photo page is loading and use them *only* when DOM extraction is empty.
    The requested fbid must still be present in the loaded page URL or serialized
    page HTML, so this fallback cannot silently widen into recommendations.
    """
    p = None
    response_candidates = []
    target_fbid = ""
    try:
        m = re.search(r"[?&]fbid=(\d{8,})", html.unescape(str(link or "")), flags=re.I)
        target_fbid = m.group(1) if m else ""
    except Exception:
        target_fbid = ""

    def _on_response(resp):
        try:
            u = str(resp.url or "")
            low = u.lower()
            if not _looks_like_real_fb_media_url(u):
                return
            if _is_probably_video_url(u) or _is_bad_fb_media_url(low):
                return
            headers = resp.headers or {}
            ctype = str(headers.get("content-type") or headers.get("Content-Type") or "").lower()
            if ctype and "image" not in ctype:
                return
            clen = 0
            try:
                clen = int(headers.get("content-length") or headers.get("Content-Length") or 0)
            except Exception:
                clen = 0
            # Skip obvious tiny UI assets; the exact target photo is normally much larger.
            if clen and clen < 12000:
                return
            response_candidates.append({
                "src": u,
                "type": "image",
                "score": max(650000, clen * 8),
                "content_length": clen,
                "reason": "v13.2-exact-photo-response",
            })
        except Exception:
            return

    try:
        p = context.new_page()
        try:
            p.on("response", _on_response)
        except Exception:
            pass
        p.goto(link, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(1800)
        try:
            p.wait_for_load_state("networkidle", timeout=4500)
        except Exception:
            pass

        candidates = _strict_visible_photo_candidates(p, prefer_dialog=True)
        if not candidates:
            candidates = _strict_visible_photo_candidates(p, prefer_dialog=False)

        if not candidates or candidates[0].get("score", 0) < 500000:
            try:
                _click_plus_overlay_or_first_photo(p)
                p.wait_for_timeout(1500)
                candidates2 = _strict_visible_photo_candidates(p, prefer_dialog=True)
                if candidates2:
                    candidates = candidates2
            except Exception:
                pass

        # Exact-photo identity proof.  A non-empty DOM candidate list is not enough:
        # logged-in photo permalinks can render a neighboring/preloaded slide while
        # the requested fbid is still the canonical target.  Bind response candidates
        # back to the exact page serialization before adding them as alternates.
        exact_page = False
        body = ""
        try:
            now = html.unescape(str(p.url or ""))
            exact_page = bool(target_fbid and target_fbid in now)
        except Exception:
            exact_page = False
        if target_fbid:
            try:
                body = html.unescape(p.content() or "")
                if not exact_page:
                    exact_page = target_fbid in body
            except Exception:
                body = ""

        tied_response = []
        if exact_page and response_candidates:
            body_norm = str(body or "").replace("\\/", "/")
            for cand in response_candidates:
                try:
                    src = html.unescape(str(cand.get("src") or ""))
                    base = os.path.basename(urlparse(src.split("?", 1)[0]).path)
                    src_escaped = src.replace("/", "\\/")
                    if (src and (src in body_norm or src_escaped in body)) or (base and len(base) >= 16 and base in body_norm):
                        tied_response.append(cand)
                except Exception:
                    continue

        if tied_response:
            merged = _merge_unique_candidates(candidates or [], tied_response)
            logger.info(
                f"FB v13.3 exact-photo identity-bound response merge: idx={idx}, fbid={target_fbid}, "
                f"dom={len(candidates or [])}, tied={len(tied_response)}, merged={len(merged)}"
            )
            return merged[:12]

        if candidates:
            return candidates

        # Last bounded fallback: only when the exact fbid is proven and DOM has no
        # usable image at all.  Keep the historical response-only behavior, but do
        # not use it when a visible DOM candidate exists.
        if exact_page and response_candidates:
            merged = _merge_unique_candidates(response_candidates, [])
            merged.sort(key=lambda c: (int(c.get("content_length") or 0), int(c.get("score") or 0)), reverse=True)
            logger.info(
                f"FB v13.3 exact-photo response-only fallback: idx={idx}, fbid={target_fbid}, "
                f"candidates={len(merged)}"
            )
            return merged[:8]

        return []
    except Exception as e:
        logger.warning(f"FB fresh photo page 第 {idx} 張開啟失敗: {e}")
        return []
    finally:
        try:
            if p:
                try:
                    p.remove_listener("response", _on_response)
                except Exception:
                    pass
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



def _is_explicit_story_post_url_v1217(url: str, resolved: str = "") -> bool:
    low = f"{url or ''} {resolved or ''}".lower()
    if "/photo/" in low or "photo.php" in low:
        return False
    return bool("story_fbid=" in low or "post_id=" in low or "story.php" in low or "/share/p/" in low)


def _extract_story_fbid_v1217(url: str, resolved: str = "") -> str:
    raw = html.unescape(unquote(f"{url or ''} {resolved or ''}"))
    m = re.search(r"[?&]story_fbid=([0-9]{8,})", raw, flags=re.I)
    if m:
        return m.group(1)
    m = re.search(r"[?&]post_id=[0-9]+_([0-9]{8,})", raw, flags=re.I)
    if m:
        return m.group(1)
    return ""


def _photo_fbid_from_href_v1217(href: str) -> str:
    raw = html.unescape(unquote(str(href or "")))
    for pat in [
        r"[?&]fbid=([0-9]{8,})",
        r"[?&]photo_id=([0-9]{8,})",
        r"/photos/(?:[^/]+/)?([0-9]{8,})",
    ]:
        m = re.search(pat, raw, flags=re.I)
        if m:
            return m.group(1)
    return ""


def _is_bad_story_candidate_href_v1217(href: str) -> bool:
    low = html.unescape(unquote(str(href or ""))).lower()
    bad = [
        "login_alerts",
        "notif_id=",
        "notif_t=",
        "if_t=login_alerts",
        "/notifications/",
        "/groups/",
        "/profile.php",
        "comment_id=",
        "reply_comment_id=",
    ]
    return any(x in low for x in bad)



def _is_exact_story_entry_v1239(url: str, resolved: str = "") -> bool:
    """True when the task/resolved URL is an exact FB story.php/permalink entry.

    v12.39:
    A share/p short link can resolve to story.php?story_fbid=...&post_id=...
    while the rendered page still exposes album/set=a links and +N controls from
    the surrounding viewer.  For exact story entries, never treat those album
    controls as the target post.  The target is the single foreground media on
    the resolved story page.
    """
    raw = f"{url or ''} {resolved or ''}".lower()
    if "story_fbid=" in raw or "post_id=" in raw:
        return True
    if "story.php" in raw and "id=" in raw:
        return True
    return False


def _build_explicit_story_visible_pack_v1239(page, network_items=None) -> dict | None:
    """Build one media pack from the currently visible story media only.

    Do not use meta/html/network as primary source here; those are exactly where
    album/recommendation pollution comes from on logged-in Facebook pages.
    """
    candidates = []
    try:
        candidates = _strict_visible_photo_candidates(page, prefer_dialog=True) or []
    except Exception:
        candidates = []
    if not candidates:
        try:
            candidates = _strict_visible_photo_candidates(page, prefer_dialog=False) or []
        except Exception:
            candidates = []
    clean = []
    for c in candidates or []:
        if not isinstance(c, dict):
            continue
        src = c.get("src") or ""
        if not src:
            continue
        low = src.lower()
        if "profile_pic" in low or "safe_image" in low or "static.xx.fbcdn.net" in low:
            continue
        # Reject tiny side-bar/comment/user avatars.  The foreground story media
        # is large; this gate prevents right panel / ad images from winning.
        try:
            area = float(c.get("area") or 0)
            natural_area = float(c.get("naturalArea") or 0)
        except Exception:
            area = 0
            natural_area = 0
        if max(area, natural_area) < 120 * 120:
            continue
        c2 = dict(c)
        c2["_v12_39_explicit_story_visible"] = True
        c2["score"] = int(c2.get("score", 0) or 0) + 2500000
        clean.append(c2)
    clean = _dedupe_ordered(clean)
    if not clean:
        return None
    clean = sorted(clean, key=lambda x: x.get("score", 0), reverse=True)
    best = clean[0]
    logger.info(
        "FB v12.39 explicit story visible media selected: "
        f"type={best.get('type','image')}, candidates={len(clean)}, primary={os.path.basename(urlparse(str(best.get('src','')).split('?')[0]).path)}"
    )
    return {
        "order": 1,
        "src": best.get("src") or "",
        "type": best.get("type") or _media_type_from_url(best.get("src") or ""),
        "score": best.get("score", 0),
        "candidates": clean,
        "source": "v12.39-explicit-story-visible",
        "_v12_39_explicit_story_visible": True,
    }


def _select_story_proximal_photo_link_v1217(links: list[str] | None, grid_items: list[dict] | None, story_fbid: str) -> tuple[str, list[dict]]:
    """Select the photo link that belongs to the explicit story_fbid/post_id.

    v12.17:
    The bad case exposes the correct target as a nearby numeric fbid
    (story_fbid=1640702658058825, photo fbid=1640702618058829) while the first
    visible album link belongs to unrelated set=a.385... content.  Therefore the
    first-anchor album rule must not run for explicit story URLs.  Instead select
    the true photo link whose fbid is numerically closest to story_fbid, excluding
    notification/login links.
    """
    story_num = int(story_fbid) if str(story_fbid or "").isdigit() else 0
    candidates = []
    seen = set()

    def add(href: str, source: str, grid_item: dict | None = None):
        if not href or href in seen:
            return
        seen.add(href)
        if _is_bad_story_candidate_href_v1217(href):
            return
        fbid = _photo_fbid_from_href_v1217(href)
        if not fbid:
            return
        score = 0
        if story_num and fbid.isdigit():
            diff = abs(int(fbid) - story_num)
            score -= min(diff, 10**15)
            if fbid[:8] == str(story_fbid)[:8]:
                score += 10**16
            if fbid[:10] == str(story_fbid)[:10]:
                score += 10**17
            if fbid == str(story_fbid):
                score += 10**18
        # Explicit story photo links should not be chosen from unrelated album roots.
        low = href.lower()
        if "set=pcb." in low:
            score += 100000000
        if "set=a." in low:
            score -= 1000000
        if source == "grid":
            score += 10000
        candidates.append((score, href, fbid, grid_item))

    for g in grid_items or []:
        add(g.get("href") or "", "grid", g)
    for l in links or []:
        add(l, "link", None)

    candidates.sort(key=lambda x: x[0], reverse=True)
    if not candidates:
        return "", []

    score, href, fbid, grid_item = candidates[0]
    logger.info(
        f"FB v12.17 explicit story proximal photo selected: "
        f"story_fbid={story_fbid or '-'}, photo_fbid={fbid}, score={score}, href={href[:160]}"
    )

    scoped_grid = []
    if grid_item:
        scoped_grid = [grid_item]
    else:
        for g in grid_items or []:
            if _photo_fbid_from_href_v1217(g.get("href") or "") == fbid:
                scoped_grid = [g]
                break
    return href, scoped_grid



def _story_title_from_page_v1217(page, fallback: str = "Facebook_Post") -> str:
    title = _get_post_folder_name(page)
    if title and title != "Facebook_Post":
        return title
    return _clean_fb_post_title_for_path(_get_fb_title(page), fallback=fallback)



def _is_bad_story_local_caption_v1219(title: str) -> bool:
    """Reject local photo-node snippets that are comments/notes, not the post caption."""
    if _is_noise_fb_story_title_v1226(title):
        return True
    t = _clean_fb_post_title_for_path(title or "", fallback="")
    if not t:
        return True

    low = t.lower()
    bad_terms = [
        "第4個要改成",
        "第 4 個要改成",
        "第八傻",
        "如果你原諒",
        "因爲家人不一定好",
        "因為家人不一定好",
        "可能會向你借錢",
        "從小害你不健康",
    ]
    if any(x.lower() in low for x in bad_terms):
        return True

    if re.search(r"第[一二三四五六七八九十\d]+[個傻]", t) and "這10種行為" not in t:
        return True

    if len(t) > 70 and ("這10種行為" not in t and "毀掉你的人生" not in t):
        return True

    return False



def _is_noise_fb_story_title_v1226(title: str) -> bool:
    """Reject local-node garbage tokens / invisible-text tracking strings."""
    t = _clean_fb_post_title_for_path(title or "", fallback="")
    if not t:
        return True
    raw = str(title or "")
    # FB sometimes exposes tracking strings with combining/invisible chars.  They
    # look like random hashes or decomposed text and must not override caption.
    combining = sum(1 for ch in raw if unicodedata.category(ch) in ("Mn", "Me", "Cf"))
    if combining >= 3:
        return True
    alnum = sum(1 for ch in t if ch.isalnum())
    spaces = t.count(" ")
    cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    if len(t) >= 24 and spaces <= 1 and cjk == 0 and alnum / max(len(t), 1) > 0.70:
        return True
    if re.fullmatch(r"[A-Za-z0-9_\-]{20,}", t):
        return True
    bad_fragments = [
        "建立貼文", "打個永字試試", "查看更多", "Iyacc si", "labblabb",
    ]
    if any(x.lower() in t.lower() for x in bad_fragments):
        return True
    return False

def _prefer_story_caption_title_v1219(local_title: str, scoped_fallback_title: str, page_title: str = "") -> str:
    """Choose the actual post caption over nearby media/comment snippets."""
    local_clean = _clean_fb_post_title_for_path(local_title or "", fallback="")
    scoped_clean = _clean_fb_post_title_for_path(scoped_fallback_title or "", fallback="")
    page_clean = _clean_fb_post_title_for_path(page_title or "", fallback="")

    def is_target_caption(t: str) -> bool:
        return bool(t and ("這10種行為" in t or "毀掉你的人生" in t))

    for candidate in [scoped_clean, page_clean, local_clean]:
        if is_target_caption(candidate):
            return candidate

    if local_clean and not _is_bad_story_local_caption_v1219(local_clean):
        return local_clean

    if scoped_clean:
        return scoped_clean
    if page_clean:
        return page_clean
    return local_clean or "Facebook_Post"


def _get_story_target_title_v1218(page, target_href: str, fallback: str = "Facebook_Post") -> str:
    """v12.19: story title must prefer post caption, not local media/comment text."""
    target_fbid = ""
    target_set = ""
    try:
        clean_href = html.unescape(unquote(str(target_href or "")))
        m = re.search(r"[?&]fbid=([0-9]{8,})", clean_href, flags=re.I)
        if m:
            target_fbid = m.group(1)
        m = re.search(r"[?&]set=([^&]+)", clean_href, flags=re.I)
        if m:
            target_set = "set:" + m.group(1)[:80]
    except Exception:
        pass

    scoped_fallback = ""
    if target_set:
        try:
            scoped_fallback = _get_post_folder_name_for_pcb(page, target_set, fallback=fallback)
        except Exception:
            scoped_fallback = ""

    page_title = ""
    try:
        page_title = _get_post_folder_name(page)
    except Exception:
        page_title = ""

    local_candidates = []
    if target_fbid:
        try:
            raw = page.evaluate(
                r"""
                (targetFbid) => {
                  const out = [];
                  const anchors = Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => (a.href || a.getAttribute('href') || '').includes(targetFbid));

                  function clean(t) {
                    return String(t || '').replace(/\s+/g, ' ').trim();
                  }

                  function push(t, source, score) {
                    t = clean(t);
                    if (!t || t.length < 4 || t.length > 240) return;
                    const low = t.toLowerCase();
                    if (low === 'facebook' || low === '查看更多' || low === '讚' || low === '留言' || low === '分享') return;
                    out.push({text: t, source, score});
                  }

                  for (const a of anchors.slice(0, 6)) {
                    let p = a;
                    for (let depth = 0; p && depth < 8; depth++, p = p.parentElement) {
                      const msg = p.querySelector && p.querySelector('[data-ad-preview="message"]');
                      if (msg) push(msg.innerText || msg.textContent || '', 'message-near-target', 3000 - depth * 80);

                      const article = p.closest && p.closest('[role="article"], article');
                      if (article) {
                        const m2 = article.querySelector('[data-ad-preview="message"]');
                        if (m2) push(m2.innerText || m2.textContent || '', 'article-message', 2800 - depth * 50);
                      }

                      const textNodes = p.querySelectorAll ? Array.from(p.querySelectorAll('div[dir="auto"], span[dir="auto"]')) : [];
                      for (const el of textNodes.slice(0, 10)) {
                        push(el.innerText || el.textContent || '', 'local-node', 700 - depth * 20);
                      }
                    }
                  }

                  out.sort((a,b) => b.score - a.score);
                  return out.slice(0, 20);
                }
                """,
                target_fbid,
            ) or []
            for item in raw:
                t = _clean_fb_post_title_for_path(item.get("text") or "", fallback="")
                if not t:
                    continue
                source = item.get("source") or "local"
                score = int(item.get("score") or 0)
                local_candidates.append((score, t, source))
        except Exception as e:
            logger.debug(f"FB v12.19 story scoped title JS skipped: {e}")

    local_best = ""
    local_source = ""
    for score, t, source in sorted(local_candidates, key=lambda x: (x[0], len(x[1])), reverse=True):
        if _is_bad_story_local_caption_v1219(t):
            logger.info(f"FB v12.19 story local title rejected: source={source}, title={t}")
            continue
        local_best = t
        local_source = source
        break

    chosen = _prefer_story_caption_title_v1219(local_best, scoped_fallback or fallback, page_title)
    chosen_source = "caption-priority"
    if chosen == local_best:
        chosen_source = local_source or "local"
    elif chosen == _clean_fb_post_title_for_path(scoped_fallback or "", fallback=""):
        chosen_source = "scoped-fallback"
    elif chosen == _clean_fb_post_title_for_path(page_title or "", fallback=""):
        chosen_source = "page-title"

    logger.info(
        f"FB v12.19 explicit story caption priority selected: "
        f"source={chosen_source}, title={chosen}"
    )
    return chosen


def _collect_reel_foreground_video_candidates_v1218(page, context, reel_id: str, referer: str) -> list[dict]:
    """Collect only media responses triggered after focusing/clicking the active Reel.

    The old broad candidate path can download a preloaded/recommended Reel while
    the title remains correct.  This fresh foreground burst is used first and is
    limited to responses after the active Reel is clicked/played.
    """
    bucket = []
    order = {"n": 0}

    def on_reel_response(resp):
        try:
            u = resp.url or ""
            low = u.lower()
            ctype = ""
            clen = 0
            try:
                ctype = resp.headers.get("content-type", "") or ""
            except Exception:
                pass
            try:
                raw_len = resp.headers.get("content-length", "") or "0"
                clen = int(raw_len) if str(raw_len).isdigit() else 0
            except Exception:
                clen = 0
            is_video_resp = ("video" in ctype.lower()) or any(x in low for x in [".mp4", ".m4v", ".mov", "video"])
            if not is_video_resp:
                return
            if not _looks_like_real_fb_media_url(u):
                return
            if 0 < clen < _MIN_FILE_SIZE:
                return
            order["n"] += 1
            # Earlier foreground responses are usually the active Reel stream.
            score = 9000000 - order["n"] * 10000 + _media_quality_score(u)
            if clen:
                score += min(clen, 500000)
            bucket.append({
                "type": "video",
                "src": u,
                "score": score,
                "_v12_18_reel_foreground": True,
                "content_length": clen,
            })
        except Exception:
            pass

    try:
        page.on("response", on_reel_response)
    except Exception:
        pass

    try:
        # Use the largest visible video area only for focus/click.  Do not read
        # its blob src; this is only to make the browser request the active stream.
        page.evaluate(
            r"""
            () => {
              const videos = Array.from(document.querySelectorAll('video')).map(v => {
                const r = v.getBoundingClientRect();
                const s = getComputedStyle(v);
                const visible = s.display !== 'none' && s.visibility !== 'hidden' &&
                  parseFloat(s.opacity || '1') > 0 && r.width >= 160 && r.height >= 160 &&
                  r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
                return {v, area: visible ? r.width * r.height : 0, x:r.left+r.width/2, y:r.top+r.height/2};
              }).filter(x => x.area > 0).sort((a,b) => b.area - a.area);
              if (videos.length) {
                try { videos[0].v.muted = true; } catch(e) {}
                try { videos[0].v.play(); } catch(e) {}
              }
            }
            """
        )
    except Exception:
        pass

    try:
        page.mouse.click(960, 600)
    except Exception:
        pass

    for wait_ms in [900, 900, 1200, 1600, 2200]:
        try:
            page.wait_for_timeout(wait_ms)
        except Exception:
            pass
        if len(bucket) >= 2:
            break

    candidates = _dedupe_ordered(bucket)
    logger.info(
        f"FB v12.18 Reel foreground video candidate count={len(candidates)} "
        f"target={reel_id or '-'}"
    )
    return candidates


def _is_bad_album_context_caption_v1224(text: str, account: str = "") -> bool:
    t = _clean_fb_post_title_for_path(text or "", fallback="")
    if not t:
        return True
    low = t.lower()
    account_clean = _clean_fb_account_name(account)
    bad_exact = {
        "讚", "留言", "分享", "回覆", "最相關", "查看更多", "查看2則回覆",
        "facebook", "facebook_post", "相片", "照片",
    }
    if low in {x.lower() for x in bad_exact}:
        return True
    if account_clean and t == account_clean:
        return True
    bad_fragments = [
        "以 rossi huang 的身分留言",
        "留言",
        "回覆",
        "粉絲黎昇",
        "看更多相關內容",
        "https://www.facebook.com/reel/",
        "潘政和其他",
        "則回覆",
    ]
    if any(x.lower() in low for x in bad_fragments):
        return True
    # Reject short UI/person-only snippets unless they contain obvious caption words.
    if len(t) < 8 and not any(x in t for x in ["#", "？", "！", "尹敬浩", "弘大旁編"]):
        return True
    return False


def _get_album_context_caption_v1224(page, fallback: str = "Facebook_Post", account: str = "") -> str:
    """Extract caption from FB right-side photo viewer panel.

    Album/photo viewer pages are not normal feed articles.  Generic post title
    selectors often return Facebook_Post, while the real caption lives in the
    right rail near the page/account name and timestamp.
    """
    candidates = []
    try:
        raw = page.evaluate(
            r"""
            () => {
              const out = [];

              function clean(t) {
                return String(t || '').replace(/\s+/g, ' ').trim();
              }
              function push(t, source, score) {
                t = clean(t);
                if (!t || t.length < 4 || t.length > 260) return;
                out.push({text:t, source, score});
              }

              const roots = [];
              const comp = document.querySelector('[role="complementary"]');
              if (comp) roots.push({root: comp, source: 'complementary', base: 5000});

              const main = document.querySelector('[role="main"]');
              if (main) roots.push({root: main, source: 'main', base: 3500});

              roots.push({root: document.body, source: 'body', base: 1000});

              for (const pack of roots) {
                const root = pack.root;
                const msgNodes = Array.from(root.querySelectorAll('[data-ad-preview="message"], [data-ad-comet-preview="message"]'));
                for (const el of msgNodes.slice(0, 10)) {
                  push(el.innerText || el.textContent || '', pack.source + ':message', pack.base + 1000);
                }

                const autos = Array.from(root.querySelectorAll('div[dir="auto"], span[dir="auto"]'));
                for (const el of autos.slice(0, 80)) {
                  const txt = clean(el.innerText || el.textContent || '');
                  if (!txt) continue;
                  let score = pack.base;
                  if (txt.includes('#')) score += 450;
                  if (txt.includes('弘大旁編') || txt.includes('尹敬浩')) score += 1200;
                  if (txt.includes('留言') || txt.includes('回覆') || txt.includes('讚')) score -= 600;
                  push(txt, pack.source + ':dir-auto', score);
                }
              }

              out.sort((a,b) => b.score - a.score || b.text.length - a.text.length);
              return out.slice(0, 80);
            }
            """
        ) or []
        for item in raw:
            t = _clean_fb_post_title_for_path(item.get("text") or "", fallback="")
            source = item.get("source") or "unknown"
            score = int(item.get("score") or 0)
            if not t:
                continue
            candidates.append((score, t, source))
    except Exception as e:
        logger.debug(f"FB v12.24 album-context caption JS skipped: {e}")

    account_clean = _clean_fb_account_name(account)
    for score, t, source in sorted(candidates, key=lambda x: (x[0], len(x[1])), reverse=True):
        if _is_bad_album_context_caption_v1224(t, account_clean):
            logger.debug(f"FB v12.24 album-context caption rejected: source={source}, title={t}")
            continue
        logger.info(f"FB v12.24 album-context caption selected: source={source}, title={t}")
        return t

    clean_fallback = _clean_fb_post_title_for_path(fallback or "", fallback="")
    if clean_fallback and clean_fallback != "Facebook_Post":
        return clean_fallback
    return "Facebook_Post"



def _is_album_context_single_photo_v1223(
    *,
    url: str,
    resolved: str,
    dominant_pcb_key: str,
    ordered_grid_items: list | None,
    ordered_links: list | None,
    plus_count_before: int,
) -> bool:
    """Detect album/photo-viewer context that must not walk same-album photos."""
    low = f"{url or ''} {resolved or ''}".lower()
    key = str(dominant_pcb_key or "").lower()
    links = ordered_links or []
    grid = ordered_grid_items or []
    if not key.startswith("set:a."):
        return False
    if plus_count_before:
        return False
    if len(grid) > 0:
        return False
    if len(links) < 3:
        return False
    if any("comment_id=" in (x or "").lower() for x in links):
        return True
    if "/share/" in low and "story_fbid=" not in low and "post_id=" not in low:
        return True
    return False




def _recover_full_gallery_near_complete_v1225(
    context,
    page,
    *,
    ordered_links: list[str],
    ordered_grid_items: list[dict],
    link_items: list[dict],
    viewer_items: list[dict],
    expected_photo_count: int,
    title: str,
    url: str,
    resolved: str,
    dominant_pcb_key: str,
) -> list[dict]:
    """Recover the last missing photo when FB viewer stalls at target-1.

    This is bounded and same-post scoped.  It does not mark success by itself;
    it only supplies extra candidates.  Existing strict completeness guard still
    decides SUCCESS vs RETRY.
    """
    try:
        expected = int(expected_photo_count or 0)
    except Exception:
        expected = 0

    current = _dedupe_items_by_media_id(_aggregate_unique_items(link_items or [], viewer_items or []))
    if not expected or len(current) >= expected:
        return viewer_items or []

    if expected - len(current) > 1:
        return viewer_items or []

    if not dominant_pcb_key or not str(dominant_pcb_key).startswith("pcb:"):
        return viewer_items or []

    existing_keys = {_media_key_from_src(i.get("src", "")) for i in current if i.get("src")}
    existing_ids = {_fb_media_id_from_src(i.get("src", "")) for i in current if i.get("src")}
    existing_ids.discard("")

    seeds = []
    seen = set()

    def add_seed(u: str):
        u = str(u or "").strip()
        if not u or u in seen:
            return
        if "facebook.com" not in u:
            return
        if dominant_pcb_key.replace("pcb:", "pcb.") not in u and "photo" not in u:
            return
        seen.add(u)
        seeds.append(u)

    for item in ordered_grid_items or []:
        add_seed(item.get("href") or item.get("url") or "")
    for href in ordered_links or []:
        add_seed(href)

    seeds = list(reversed(seeds))[:10]
    if not seeds:
        return viewer_items or []

    logger.info(
        f"FB v12.25 near-complete recovery start: "
        f"current={len(current)}, expected={expected}, seeds={len(seeds)}, scope={dominant_pcb_key}"
    )

    recovered_viewer = list(viewer_items or [])
    for idx, seed in enumerate(seeds, 1):
        try:
            seq = _collect_viewer_sequence_from_url(
                context,
                seed,
                target_count=3,
                title=title,
                mode="post",
                log_prefix=f"FB v12.25 recovery seed {idx}/{len(seeds)}",
                original_url=url,
                resolved_url=resolved,
                max_turns=6,
                stale_limit=3,
                prefer_existing_page=page,
            ) or []
        except TypeError:
            try:
                seq = _collect_viewer_sequence_from_url(context, seed, 3, title, "post") or []
            except Exception as e:
                logger.debug(f"FB v12.25 recovery seed skipped: {e}")
                continue
        except Exception as e:
            logger.debug(f"FB v12.25 recovery seed skipped: {e}")
            continue

        for pack in _fb_v1232_pack_dicts(seq):
            src = pack.get("src") or ""
            if not src or _is_probably_video_url(src):
                continue
            key = _media_key_from_src(src)
            mid = _fb_media_id_from_src(src)
            if key in existing_keys or (mid and mid in existing_ids):
                continue
            existing_keys.add(key)
            if mid:
                existing_ids.add(mid)
            recovered_viewer.append(pack)
            current.append(pack)
            logger.info(
                f"FB v12.25 near-complete recovery accepted missing item: "
                f"{len(current)}/{expected}, seed={idx}, media={_media_basename(src)}"
            )
            if len(current) >= expected:
                logger.info(f"FB v12.25 near-complete recovery completed: {len(current)}/{expected}")
                return recovered_viewer

    logger.warning(f"FB v12.25 near-complete recovery incomplete: {len(current)}/{expected}; keep RETRY")
    return recovered_viewer



def _fb_v1228_capture_files_from_temp() -> list[str]:
    """Return already-captured cap_* response files in stable capture order.

    v12.31:
    The response listener persists files under TEMP_DIR/_fb_capture, not directly
    under TEMP_DIR.  v12.30 scanned only TEMP_DIR, so logs showed many
    "FB response 實體落盤: cap_..." lines but recovery reported captures=0.
    """
    candidate_dirs = [
        os.path.join(TEMP_DIR, "_fb_capture"),
        TEMP_DIR,
    ]
    paths = []
    seen = set()

    for base in candidate_dirs:
        try:
            if not os.path.isdir(base):
                continue
            for name in os.listdir(base):
                low = name.lower()
                if not name.startswith("cap_"):
                    continue
                if not low.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    continue
                path = os.path.join(base, name)
                if path in seen:
                    continue
                if not os.path.isfile(path):
                    continue
                if os.path.getsize(path) < 12 * 1024:
                    continue
                seen.add(path)
                paths.append(path)
        except Exception:
            continue

    try:
        paths.sort(key=lambda p: os.path.getmtime(p))
    except Exception:
        pass

    if paths:
        logger.info(
            f"FB v12.31 response-capture file pool: {len(paths)} "
            f"(scan=_fb_capture+TEMP_DIR)"
        )
    return paths




def _fb_v1229_capture_items_from_temp(expected_photo_count: int = 0) -> list[dict]:
    """Return captured cap_* files as viewer items for pre-download completeness.

    v12.28 filled missing output files after download, but long galleries such
    as expected=18 could still return RETRY earlier at the fast retry guard
    before download starts.  v12.29 uses the same response captures as candidate
    viewer items before that guard, then the normal expected-count guard decides
    success vs retry.
    """
    items = []
    for cap in _fb_v1228_capture_files_from_temp():
        try:
            src = f"file://{cap}"
            items.append({
                "type": "image",
                "src": src,
                "candidates": [src],
                "temp_path": cap,
                "source": "v12.29-response-capture-pre-guard",
                "media_id": os.path.splitext(os.path.basename(cap))[0],
                "score": 1,
                "_allow_fb_best_available_source": True,
            })
            if expected_photo_count and len(items) >= int(expected_photo_count):
                break
        except Exception:
            continue
    if items:
        logger.info(f"FB v12.29 pre-guard response-capture items: {len(items)}")
    return items



def _fb_v1230_capture_item_key(path: str) -> str:
    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0
    name = os.path.basename(path or "")
    # The cap_* filename is a response-content hash in current pipeline and is
    # stable enough to avoid duplicates. Keep size as a secondary hint for older
    # sessions where cap filenames may be reused.
    return f"{name}:{size}"




# v12.34 ---------------------------------------------------------------------
# Last defensive layer before the main FB pipeline:
# legacy helpers below this point are used by multiple gallery branches.  Some
# of them historically assumed all media packs and candidates were dictionaries.
# FB response-capture recovery can mix legacy raw URL strings with dict packs, so
# redefine the global helper names here with strict normalization before the main
# pipeline starts.  Python resolves these names at call time, so these definitions
# override the earlier legacy implementations without changing unrelated logic.

def _fb_v1234_candidate_src_list(pack) -> list[str]:
    if isinstance(pack, str):
        return [pack]
    if not isinstance(pack, dict):
        return []
    out = []
    src = pack.get("src") or ""
    if src:
        out.append(src)
    for cand in pack.get("candidates") or []:
        if isinstance(cand, dict):
            csrc = cand.get("src") or ""
        elif isinstance(cand, str):
            csrc = cand
        else:
            csrc = ""
        if csrc:
            out.append(csrc)
    return out


def _fb_v1234_candidate_pack_list(pack) -> list[dict]:
    out = []
    if isinstance(pack, dict):
        out.append(pack)
        for cand in pack.get("candidates") or []:
            if isinstance(cand, dict):
                out.append(cand)
            elif isinstance(cand, str):
                out.append({"src": cand, "type": _media_type_from_url(cand), "score": 0})
    elif isinstance(pack, str):
        out.append({"src": pack, "type": _media_type_from_url(pack), "score": 0})
    return out


def _pack_media_cluster_key(pack: dict | str) -> str:
    for src in _fb_v1234_candidate_src_list(pack):
        key = _media_cluster_key_from_src(src)
        if key:
            return key
    return ""


def _pack_best_media_id_str(pack: dict | str) -> str:
    for src in _fb_v1234_candidate_src_list(pack):
        mid = _fb_media_numeric_id_str_from_src(src)
        if mid:
            return mid
    return ""


def _manifest_ids_from_packs(packs: list[dict]) -> list[str]:
    out = []
    seen = set()
    for pack in _fb_v1232_pack_dicts(packs):
        for src in _fb_v1234_candidate_src_list(pack):
            mid = _fb_media_numeric_id_str_from_src(src)
            if mid and mid not in seen:
                seen.add(mid)
                out.append(mid)
                break
    return out


def _pack_has_manifest_id(pack: dict | str, manifest_ids: set[str]) -> bool:
    if not manifest_ids:
        return False
    for src in _fb_v1234_candidate_src_list(pack):
        mid = _fb_media_numeric_id_str_from_src(src)
        if mid and mid in manifest_ids:
            return True
    return False


def _dedupe_items_by_media_id(packs: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for pack in _fb_v1232_pack_dicts(packs):
        mid = _pack_best_media_id_str(pack)
        if mid:
            if mid in seen:
                logger.info(f"FB v12.34 duplicate media id skipped={mid}")
                continue
            seen.add(mid)
        out.append(pack)
    return out


def _aggregate_unique_items(*seqs: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for seq in seqs:
        for pack in _fb_v1232_pack_dicts(seq):
            srcs = _fb_v1234_candidate_src_list(pack)
            key = _media_key_from_src(srcs[0]) if srcs else ""
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(pack)
    return out


def _drop_video_items(packs: list[dict]) -> list[dict]:
    out = []
    for pack in _fb_v1232_pack_dicts(packs):
        srcs = _fb_v1234_candidate_src_list(pack)
        src = srcs[0] if srcs else ""
        if pack.get("type") == "video" or _is_probably_video_url(src):
            logger.info(f"FB v12.34 drop video/non-photo candidate: {os.path.basename(urlparse(str(src).split('?')[0]).path)}")
            continue
        out.append(pack)
    return out


def _dominant_media_cluster(packs: list[dict] | None, *, min_count: int = 3) -> str:
    counts = {}
    order = []
    for pack in _fb_v1232_pack_dicts(packs):
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
        return _fb_v1232_pack_dicts(packs)
    out = []
    seen_primary = set()
    for pack in _fb_v1232_pack_dicts(packs):
        srcs = _fb_v1234_candidate_src_list(pack)
        src = srcs[0] if srcs else ""
        key = _media_cluster_key_from_src(src)
        if key != cluster_key:
            continue
        if pack.get("type") == "video" or _is_probably_video_url(src):
            continue

        clean_candidates = []
        for cand_pack in _fb_v1234_candidate_pack_list(pack):
            csrcs = _fb_v1234_candidate_src_list(cand_pack)
            csrc = csrcs[0] if csrcs else ""
            if _media_cluster_key_from_src(csrc) == cluster_key and not _is_probably_video_url(csrc):
                clean_candidates.append(cand_pack)

        clone = dict(pack)
        if clean_candidates:
            clone["candidates"] = clean_candidates
        primary = _normalized_exact_fb_media_url(src)
        if primary and primary in seen_primary:
            continue
        if primary:
            seen_primary.add(primary)
        out.append(clone)
    return out


def _filter_items_by_media_cluster_or_manifest(
    packs: list[dict],
    cluster_key: str,
    manifest_ids: list[str] | None = None,
) -> list[dict]:
    manifest_set = set(manifest_ids or [])
    out = []
    seen_primary = set()
    for pack in _fb_v1232_pack_dicts(packs):
        srcs = _fb_v1234_candidate_src_list(pack)
        src = srcs[0] if srcs else ""
        if pack.get("type") == "video" or _is_probably_video_url(src):
            continue

        key = _media_cluster_key_from_src(src)
        in_cluster = bool(cluster_key and key == cluster_key)
        in_manifest = _pack_has_manifest_id(pack, manifest_set)

        if cluster_key or manifest_set:
            if not (in_cluster or in_manifest):
                continue

        clean_candidates = []
        for cand_pack in _fb_v1234_candidate_pack_list(pack):
            csrcs = _fb_v1234_candidate_src_list(cand_pack)
            csrc = csrcs[0] if csrcs else ""
            if _is_probably_video_url(csrc):
                continue
            ckey = _media_cluster_key_from_src(csrc)
            cmid = _fb_media_numeric_id_str_from_src(csrc)
            if (
                (cluster_key and ckey == cluster_key)
                or (manifest_set and cmid in manifest_set)
                or (not cluster_key and not manifest_set)
            ):
                clean_candidates.append(cand_pack)

        clone = dict(pack)
        if clean_candidates:
            clone["candidates"] = clean_candidates
        primary = _normalized_exact_fb_media_url(src)
        if primary and primary in seen_primary:
            continue
        if primary:
            seen_primary.add(primary)
        out.append(clone)
    return out


def _merge_unique_candidates(primary: list[dict], fallback: list[dict]) -> list[dict]:
    seen = set()
    merged = []
    for item in _fb_v1232_pack_dicts((primary or []) + (fallback or [])):
        srcs = _fb_v1234_candidate_src_list(item)
        src = srcs[0].strip() if srcs else ""
        if not src:
            continue
        key = _media_key_from_src(src)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return _dedupe_ordered(merged)


def _force_single_photo_items_v1223(link_items: list[dict] | None, viewer_items: list[dict] | None) -> list[dict]:
    for seq in [link_items or [], viewer_items or []]:
        for item in _fb_v1232_pack_dicts(seq):
            srcs = _fb_v1234_candidate_src_list(item)
            src = srcs[0] if srcs else ""
            if src and not _is_probably_video_url(src):
                return [item]
    return []

# ---------------------------------------------------------------------------


# v12.35 ---------------------------------------------------------------------
# Final defensive download-layer guard:
# v12.34 normalized merge/manifest/cluster helpers, but the final downloader
# still had legacy assumptions that pack["candidates"] contains dictionaries.
# Some response-capture/full-gallery candidates contain raw URL strings or
# file:// persisted capture paths.  Normalize right before download so 18/18
# collected galleries cannot fail with "'str' object has no attribute 'get'".

def _fb_v1235_candidate_from_raw(raw, *, fallback_type: str = "image", inherited: dict | None = None) -> dict | None:
    inherited = inherited or {}
    if isinstance(raw, dict):
        src = (raw.get("src") or "").strip()
        item = dict(raw)
    elif isinstance(raw, str):
        src = raw.strip()
        item = {"src": src, "type": _media_type_from_url(src), "score": 0}
    else:
        return None

    temp_path = item.get("temp_path") or item.get("persisted_path") or inherited.get("temp_path") or inherited.get("persisted_path")
    if not src and temp_path:
        src = f"file://{temp_path}"
        item["src"] = src

    if not src:
        return None

    if temp_path and os.path.exists(str(temp_path)):
        item["temp_path"] = str(temp_path)
        item.setdefault("persisted_path", str(temp_path))
        item.setdefault("_allow_fb_best_available_source", True)

    if not item.get("type"):
        item["type"] = fallback_type or _media_type_from_url(src)

    try:
        item["score"] = int(item.get("score") or 0)
    except Exception:
        item["score"] = 0

    return item


def _fb_v1235_normalize_candidates(candidates, *, inherited: dict | None = None) -> list[dict]:
    out = []
    seen = set()
    inherited = inherited or {}
    for raw in candidates or []:
        item = _fb_v1235_candidate_from_raw(raw, inherited=inherited)
        if not item:
            continue

        src = (item.get("src") or "").strip()
        temp_path = item.get("temp_path") or item.get("persisted_path") or ""

        if temp_path and os.path.exists(str(temp_path)):
            try:
                key = f"persisted:{os.path.basename(str(temp_path))}:{os.path.getsize(str(temp_path))}"
            except Exception:
                key = f"persisted:{os.path.basename(str(temp_path))}"
        elif src.startswith("file://"):
            key = src
        elif item.get("type") == "video" or any(x in src.lower() for x in [".mp4", ".m4v", ".mov"]):
            key = os.path.basename(urlparse(src.split("?")[0]).path) or src[:180]
        else:
            if not _looks_like_real_fb_media_url(src):
                continue
            key = _normalized_exact_fb_media_url(src)

        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_ordered(items):
    out = []
    seen = set()
    for item in _fb_v1235_normalize_candidates(items):
        src = (item.get("src") or "").strip()
        temp_path = item.get("temp_path") or item.get("persisted_path") or ""

        if temp_path and os.path.exists(str(temp_path)):
            try:
                key = f"persisted:{os.path.basename(str(temp_path))}:{os.path.getsize(str(temp_path))}"
            except Exception:
                key = f"persisted:{os.path.basename(str(temp_path))}"
        elif src.startswith("file://"):
            key = src
        elif item.get("type") == "video" or any(x in src.lower() for x in [".mp4", ".m4v", ".mov"]):
            key = os.path.basename(urlparse(src.split("?")[0]).path) or src[:180]
        else:
            key = _normalized_exact_fb_media_url(src)

        if not key or key in seen:
            continue
        seen.add(key)

        if not src.startswith("file://"):
            try:
                src = html.unescape(unquote(src))
                item["src"] = src
                item["score"] = int(item.get("score") or 0) + _media_quality_score(src)
            except Exception:
                pass
        out.append(item)
    return out


def _pack_primary_candidates(pack: dict) -> list[dict]:
    pack_list = _fb_v1234_candidate_pack_list(pack)
    if not pack_list:
        return []

    # Use the first normalized pack as primary.
    primary_pack = pack_list[0]
    primary_src = (primary_pack.get("src") or "").strip()
    primary_key = _media_key_from_src(primary_src)
    inherited = primary_pack if isinstance(primary_pack, dict) else {}

    raw_candidates = []
    if isinstance(pack, dict):
        raw_candidates.extend(pack.get("candidates") or [])
    raw_candidates.extend(pack_list)

    candidates = _fb_v1235_normalize_candidates(raw_candidates, inherited=inherited)

    if not candidates and primary_src:
        cand = _fb_v1235_candidate_from_raw(primary_pack, inherited=inherited)
        return [cand] if cand else []

    if not primary_key or primary_src.startswith("file://"):
        return candidates[:4]

    pinned = []
    seen = set()
    for cand in candidates:
        src = (cand.get("src") or "").strip()
        if not src:
            continue

        # Persisted capture candidates are exact viewer captures; keep them as
        # fallback even if the file:// key does not match CDN basename.
        if cand.get("temp_path") and os.path.exists(str(cand.get("temp_path"))):
            dedupe_key = f"persisted:{os.path.basename(str(cand.get('temp_path')))}"
        else:
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
        return pinned[:4]

    fallback = _fb_v1235_candidate_from_raw(primary_pack, inherited=inherited)
    return [fallback] if fallback else []



# ---------------------------------------------------------------------------


def _is_fb_browser_context_closed_error(err: str) -> bool:
    text = str(err or "").lower()
    return any(x in text for x in [
        "target page, context or browser has been closed",
        "browser has been closed",
        "target closed",
        "browser closed",
        "context closed",
    ])


def _launch_fb_persistent_context_with_retry(p, *, user_data_dir: str, profile_dir: str):
    """Launch FB_Parser persistent profile with one clean retry.

    v12.37 fixes the failure chain after a large gallery timeout: the outer
    daemon timeout returned while the Playwright thread was still harvesting,
    then the next FB task attempted to reuse the same persistent profile and got
    "Target page, context or browser has been closed" immediately.
    """
    launch_kwargs = dict(
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

    last_error = None
    for attempt in range(1, 3):
        try:
            return p.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as e:
            last_error = e
            if not _is_fb_browser_context_closed_error(str(e)) and "user data directory is already in use" not in str(e).lower():
                raise
            logger.warning(
                f"FB v12.37 persistent context launch attempt {attempt} failed; "
                f"retry after cleanup: {str(e).splitlines()[0]}"
            )
            try:
                time.sleep(2.5)
            except Exception:
                pass
    raise last_error


# v12.40 ---------------------------------------------------------------------
# Keep only the largest/best file per logical Facebook photo.
#
# Failure fixed:
# For share/p gallery posts, one fbid can expose multiple CDN URLs with different
# sizes, for example 807939194... full and 807939194... 329x590 low copy.  Older
# output dedupe used the full URL key including volatile _oh_ token, so it kept
# both versions and produced 10 files for a 5-image post.
#
# This layer dedupes final output candidates by stable photo identity first
# (fbid from href/media_id/CDN basename), tries high-res candidates first, and
# replaces a smaller already-downloaded output when a better resolution/size for
# the same photo appears later.

def _fb_v1240_logical_photo_key(pack: dict | str) -> str:
    if isinstance(pack, str):
        srcs = [pack]
        media_id = ""
        href = ""
    elif isinstance(pack, dict):
        media_id = str(pack.get("media_id") or pack.get("fbid") or pack.get("photo_id") or "")
        href = str(pack.get("href") or pack.get("url") or "")
        srcs = _fb_v1234_candidate_src_list(pack) if "_fb_v1234_candidate_src_list" in globals() else [str(pack.get("src") or "")]
    else:
        return ""

    for value in [media_id, href] + srcs:
        s = str(value or "")
        m = re.search(r"(?:fbid=|photo_id=)(\d{8,})", s)
        if m:
            return f"fbid:{m.group(1)}"
        m = re.search(r"/(\d{8,})_\d+_\d+_n\.(?:jpg|jpeg|png|webp)", s, re.I)
        if m:
            return f"cdn:{m.group(1)}"

    # fallback: stable basename without volatile query/_oh token
    for src in srcs:
        base = os.path.basename(urlparse(str(src).split("?")[0]).path)
        if base:
            return f"base:{base.lower()}"
    return ""


def _fb_v1240_file_quality(path: str) -> tuple[int, int, int]:
    try:
        size = os.path.getsize(path)
    except Exception:
        size = 0
    w = h = 0
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        pass
    return (w * h, max(w, h), size)


def _fb_v1240_best_candidate_order(candidates: list[dict]) -> list[dict]:
    def score(c):
        src = str((c or {}).get("src") or "")
        temp_path = str((c or {}).get("temp_path") or (c or {}).get("persisted_path") or "")
        declared_w = int((c or {}).get("width") or 0)
        declared_h = int((c or {}).get("height") or 0)
        declared_area = declared_w * declared_h
        size_hint = 0
        if temp_path and os.path.exists(temp_path):
            try:
                size_hint = os.path.getsize(temp_path)
            except Exception:
                size_hint = 0
        low_penalty = 0
        low = src.lower()
        if re.search(r"[?&](?:stp|_nc_ohc|_nc_ht)=", low):
            low_penalty -= 2
        if re.search(r"s(?:120|130|240|320|480|640)x(?:120|130|240|320|480|640)", low):
            low_penalty -= 20
        # Prefer larger declared pixels, then existing persisted file size, then original score.
        return (
            declared_area,
            max(declared_w, declared_h),
            size_hint,
            int((c or {}).get("score") or 0) + low_penalty,
        )
    return sorted(candidates or [], key=score, reverse=True)


def _fb_v1240_group_items_by_photo(items: list[dict]) -> list[dict]:
    groups = {}
    order = []
    for pack in _fb_v1232_pack_dicts(items) if "_fb_v1232_pack_dicts" in globals() else [x for x in (items or []) if isinstance(x, dict)]:
        key = _fb_v1240_logical_photo_key(pack)
        if not key:
            key = _media_key_from_src(str(pack.get("src") or ""))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(pack)

    out = []
    for key in order:
        packs = groups[key]
        merged_candidates = []
        base = dict(packs[0])
        for pack in packs:
            if pack.get("src"):
                merged_candidates.append({
                    "src": pack.get("src"),
                    "type": pack.get("type", "image"),
                    "score": pack.get("score", 0),
                    "width": pack.get("width", 0),
                    "height": pack.get("height", 0),
                    "temp_path": pack.get("temp_path") or pack.get("persisted_path") or "",
                    "persisted_path": pack.get("persisted_path") or pack.get("temp_path") or "",
                })
            for cand in pack.get("candidates") or []:
                if isinstance(cand, dict):
                    merged_candidates.append(cand)
                elif isinstance(cand, str):
                    merged_candidates.append({"src": cand, "type": _media_type_from_url(cand), "score": 0})

        # normalize and prefer high resolution candidates first
        if "_fb_v1235_normalize_candidates" in globals():
            merged_candidates = _fb_v1235_normalize_candidates(merged_candidates, inherited=base)
        else:
            merged_candidates = [c for c in merged_candidates if isinstance(c, dict) and c.get("src")]

        base["candidates"] = _fb_v1240_best_candidate_order(merged_candidates)
        if base["candidates"]:
            best = base["candidates"][0]
            base["src"] = best.get("src") or base.get("src")
            base["temp_path"] = best.get("temp_path") or best.get("persisted_path") or base.get("temp_path", "")
            base["persisted_path"] = best.get("persisted_path") or best.get("temp_path") or base.get("persisted_path", "")
        base["_logical_photo_key_v1240"] = key
        out.append(base)

    if len(out) != len(items or []):
        logger.info(f"FB v12.40 logical photo dedupe before download: items {len(items or [])}->{len(out)}")
    return out



# ---------------------------------------------------------------------------



# v12.49 ---------------------------------------------------------------------
# Exact short-video HTML/direct-media identity recovery.
#
# FB short-video pages (/share/r/, /share/v/, /reel/) can preload sibling videos.
# A broad network candidate or even yt-dlp can therefore pair the correct caption
# with a wrong MP4.  Before yt-dlp, fetch exact numeric-id pages with the current
# authenticated browser context and accept only direct MP4 URLs found very close
# to the exact target id in serialized FB data.

def _fb_v1249_unescape_media_url(raw: str) -> str:
    s = str(raw or '').strip().strip('"\'')
    if not s:
        return ''
    for _ in range(3):
        old = s
        s = html.unescape(s)
        s = s.replace('\\/', '/').replace('\\u002F', '/').replace('\\u002f', '/')
        s = s.replace('\\u003A', ':').replace('\\u003a', ':')
        s = s.replace('\\u0026', '&').replace('\\u003D', '=').replace('\\u003d', '=')
        s = s.replace('\\u0025', '%').replace('\\u003F', '?').replace('\\u003f', '?')
        s = s.replace('\\u002E', '.').replace('\\u002e', '.')
        if s == old:
            break
    return s


def _fb_v1249_extract_exact_video_candidates_from_text(text: str, target_id: str, source_label: str = '') -> list[dict]:
    text = str(text or '')
    target_id = str(target_id or '').strip()
    if not text or not target_id:
        return []

    positions = [m.start() for m in re.finditer(re.escape(target_id), text)]
    if not positions:
        return []

    key_scores = {
        'browser_native_hd_url': 9000000,
        'playable_url_quality_hd': 8800000,
        'browser_native_sd_url': 8200000,
        'playable_url': 8000000,
        'hd_src': 7800000,
        'sd_src': 7400000,
        'progressive_url': 7200000,
        'url': 5000000,
    }
    out = []
    seen = set()
    for pos in positions[:24]:
        lo = max(0, pos - 18000)
        hi = min(len(text), pos + 18000)
        chunk = text[lo:hi]
        # Require an identity-shaped key close to the target id where possible.
        if not re.search(rf'(?:video(?:_|)id|videoId|\"id\"|\"video_id\")\s*[:=]\s*[\"\']?{re.escape(target_id)}', chunk, flags=re.I):
            # Facebook sometimes serializes only the bare id beside delivery fields.
            # Keep a much tighter window in that case.
            lo = max(0, pos - 7000)
            hi = min(len(text), pos + 7000)
            chunk = text[lo:hi]

        for key, base in key_scores.items():
            # JSON-escaped direct media URLs.
            for m in re.finditer(rf'[\"\']{re.escape(key)}[\"\']\s*:\s*[\"\']([^\"\']+)[\"\']', chunk, flags=re.I):
                src = _fb_v1249_unescape_media_url(m.group(1))
                low = src.lower()
                if not src.startswith('http'):
                    continue
                if not ('fbcdn' in low or 'scontent' in low or '.mp4' in low or 'video' in low):
                    continue
                ident = _fb_media_identity(src) or src.split('&')[0]
                if ident in seen:
                    continue
                seen.add(ident)
                dist = abs((lo + m.start()) - pos)
                out.append({
                    'src': src,
                    'type': 'video',
                    'score': int(base + max(0, 18000 - dist)),
                    'reason': f'v12.49-exact-id-html:{source_label}:{key}',
                    'target_id': target_id,
                })

        # Raw escaped/non-escaped MP4 URLs as a final exact-id-local source.
        for m in re.finditer(r'https?(?:\\/|/)[^\"\'\s<>]{20,}?\.mp4[^\"\'\s<>]*', chunk, flags=re.I):
            src = _fb_v1249_unescape_media_url(m.group(0))
            low = src.lower()
            if not src.startswith('http') or not ('fbcdn' in low or 'scontent' in low):
                continue
            ident = _fb_media_identity(src) or src.split('&')[0]
            if ident in seen:
                continue
            seen.add(ident)
            dist = abs((lo + m.start()) - pos)
            out.append({
                'src': src,
                'type': 'video',
                'score': int(6500000 + max(0, 18000 - dist)),
                'reason': f'v12.49-exact-id-html:{source_label}:raw-mp4',
                'target_id': target_id,
            })

    out.sort(key=lambda x: int(x.get('score') or 0), reverse=True)
    return out


def _fb_v1249_collect_exact_short_video_candidates(context, target_id: str, original_url: str = '', resolved_url: str = '') -> list[dict]:
    target_id = str(target_id or '').strip()
    if not target_id:
        return []
    urls = []
    for u in [
        resolved_url,
        original_url,
        f'https://www.facebook.com/watch/?v={target_id}',
        f'https://m.facebook.com/watch/?v={target_id}',
        f'https://mbasic.facebook.com/watch/?v={target_id}',
        f'https://www.facebook.com/reel/{target_id}/',
    ]:
        u = str(u or '').strip()
        if u and u not in urls:
            urls.append(u)

    out = []
    seen = set()
    for idx, u in enumerate(urls, 1):
        try:
            resp = context.request.get(u, timeout=30000, headers={
                'Referer': resolved_url or original_url or 'https://www.facebook.com/',
                'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8',
            })
            if not resp.ok:
                continue
            body = resp.text()
            cands = _fb_v1249_extract_exact_video_candidates_from_text(body, target_id, source_label=f'http{idx}')
            for c in cands:
                ident = _fb_media_identity(c.get('src') or '') or (c.get('src') or '').split('&')[0]
                if ident in seen:
                    continue
                seen.add(ident)
                out.append(c)
        except Exception as e:
            logger.debug(f'FB v12.49 exact short-video HTTP probe skipped: {u} | {e}')
    out.sort(key=lambda x: int(x.get('score') or 0), reverse=True)
    logger.info(f'FB v12.49 exact short-video candidate count={len(out)} target={target_id}')
    return out


def _fb_v1249_download_exact_short_video(context, target_id: str, original_url: str, resolved_url: str, title: str):
    cands = _fb_v1249_collect_exact_short_video_candidates(context, target_id, original_url, resolved_url)
    if not cands:
        return 'RETRY', 'Facebook exact short-video direct media not found for target id'
    try:
        clear_temp()
        final_dst, size = _download_best_candidate(
            context,
            cands,
            os.path.join(TEMP_DIR, 'fb_0001'),
            referer=resolved_url or original_url,
        )
        if not final_dst or size <= 0:
            clear_temp()
            return 'RETRY', 'Facebook exact short-video candidate download produced no valid file'
        clean_title = _clean_fb_post_title_for_path(title, fallback=f'Facebook_Video_{target_id}')
        if move_files(clean_title):
            logger.info(
                f'FB v12.49 exact short-video completed: target={target_id}, '
                f'candidate={os.path.basename(urlparse((cands[0].get("src") or "").split("?")[0]).path)}, bytes={size}'
            )
            return 'SUCCESS', ''
        clear_temp()
        return 'FAILED', 'Facebook exact short-video downloaded but move_files failed'
    except Exception as e:
        clear_temp()
        return _classify_error(f'Facebook exact short-video download failed: {e}')


# v12.44 ---------------------------------------------------------------------
# Exact /share/v/ fallback:
# v12.43 intentionally blocked broad network/meta/html fallback to prevent
# downloading an unrelated preloaded video.  Some FB /share/v/ pages, however,
# have foreground=0 because the active <video> is already buffered/blob-only
# before the response hook is attached.  For those cases use yt-dlp only against
# the exact canonical video id and verify yt-dlp's extracted id/url before
# allowing SUCCESS.

def _fb_v1244_export_context_cookies(context) -> str:
    try:
        cookies = context.cookies([
            "https://www.facebook.com/",
            "https://m.facebook.com/",
            "https://mbasic.facebook.com/",
        ])
    except Exception:
        cookies = []

    if not cookies:
        return ""

    path = os.path.join(
        TEMP_DIR,
        f"fb_v1244_exact_share_v_{os.getpid()}_{threading.get_ident()}.cookies.txt",
    )
    os.makedirs(TEMP_DIR, exist_ok=True)

    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in cookies:
                domain = str(c.get("domain") or ".facebook.com")
                include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
                cookie_path = str(c.get("path") or "/")
                secure = "TRUE" if c.get("secure") else "FALSE"
                expires = c.get("expires")
                try:
                    expires = int(expires or 0)
                    if expires < 0:
                        expires = 0
                except Exception:
                    expires = 0
                name = str(c.get("name") or "")
                value = str(c.get("value") or "")
                if not name:
                    continue
                f.write("\t".join([
                    domain,
                    include_subdomains,
                    cookie_path,
                    secure,
                    str(expires),
                    name,
                    value,
                ]) + "\n")
        return path
    except Exception as e:
        logger.debug(f"FB v12.44 cookie export failed: {e}")
        return ""


def _fb_v1244_info_matches_target(info: dict, target_id: str, title_hint: str = "") -> bool:
    if not isinstance(info, dict):
        return False
    target = str(target_id or "").strip()
    if not target:
        return False

    values = []
    for key in ["id", "display_id", "webpage_url", "original_url", "url", "extractor_key"]:
        v = info.get(key)
        if v:
            values.append(str(v))

    # Playlist-like result: accept only if the requested id appears on the top
    # object or first selected entry.  We use noplaylist, but be defensive.
    entries = info.get("entries")
    if entries:
        try:
            first = next((x for x in entries if isinstance(x, dict)), None)
            if first:
                for key in ["id", "display_id", "webpage_url", "original_url", "url"]:
                    v = first.get(key)
                    if v:
                        values.append(str(v))
        except Exception:
            pass

    joined = " ".join(values)
    if target in joined:
        return True

    # Last-resort title guard: do not use as sole proof for numeric target unless
    # the title is long and highly specific.  It prevents a totally unrelated
    # preloaded video from passing if yt-dlp returns a different id.
    extracted_title = str(info.get("title") or info.get("description") or "")
    hint = str(title_hint or "")
    if len(hint) >= 12 and hint[:20] in extracted_title:
        logger.warning(
            "FB v12.44 exact id not visible in yt-dlp info; title matched but "
            "id proof missing, reject to avoid wrong video"
        )
    return False


def _download_share_v_exact_ytdlp_v1244(
    context,
    original_url: str,
    resolved_url: str,
    target_id: str,
    title: str,
):
    """Download /share/v/ only through exact target-id yt-dlp URLs.

    Returns (status, error).  It never falls back to broad page candidates.
    """
    target_id = str(target_id or "").strip()
    if not target_id:
        return "RETRY", "Facebook share/v exact fallback has no canonical video id"

    clear_temp()

    ffmpeg_path = _find_ffmpeg()
    cookiefile = _fb_v1244_export_context_cookies(context)
    if not cookiefile and os.path.exists(COOKIES_FILE):
        cookiefile = os.path.abspath(COOKIES_FILE)

    exact_urls = []
    for u in [
        resolved_url,
        original_url,
        f"https://www.facebook.com/watch/?v={target_id}",
        f"https://m.facebook.com/watch/?v={target_id}",
        f"https://mbasic.facebook.com/watch/?v={target_id}",
    ]:
        u = str(u or "").strip()
        if u and u not in exact_urls:
            exact_urls.append(u)

    formats = [
        "best[ext=mp4][height<=1080]/best[protocol^=http][height<=1080]/best",
        "best[ext=mp4]/best",
        "best",
    ]

    last_error = ""
    for exact_url in exact_urls:
        for fmt in formats:
            try:
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": True,
                    "format": fmt,
                    "outtmpl": os.path.join(TEMP_DIR, "%(title).120s.%(ext)s"),
                    "overwrites": True,
                    "socket_timeout": 30,
                    "retries": 2,
                    "fragment_retries": 2,
                    "http_headers": {
                        "User-Agent": (
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/123.0.0.0 Safari/537.36"
                        ),
                        "Referer": resolved_url or original_url or "https://www.facebook.com/",
                        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
                    },
                }
                if cookiefile:
                    ydl_opts["cookiefile"] = cookiefile
                if ffmpeg_path:
                    ydl_opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)
                    ydl_opts["merge_output_format"] = "mp4"
                else:
                    logger.warning("未找到 ffmpeg，FB v12.44 share/v exact yt-dlp 將使用單檔格式")

                logger.info(
                    f"FB v12.44 exact share/v yt-dlp probe: target={target_id}, "
                    f"url={exact_url}, fmt={fmt}"
                )

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(exact_url, download=False)

                if not _fb_v1244_info_matches_target(info, target_id, title):
                    last_error = (
                        f"yt-dlp extracted info did not prove target id {target_id}; "
                        "skip to avoid wrong video"
                    )
                    logger.warning(f"FB v12.44 exact share/v id guard rejected: {last_error}")
                    continue

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(exact_url, download=True)

                if not _fb_v1244_info_matches_target(info, target_id, title):
                    clear_temp()
                    last_error = f"yt-dlp download result did not prove target id {target_id}"
                    logger.warning(f"FB v12.44 exact share/v download rejected: {last_error}")
                    continue

                clean_title = _clean_fb_post_title_for_path(title, fallback=_fb_reel_fallback_title(exact_url))
                if move_files(clean_title):
                    logger.info(f"FB v12.44 exact share/v yt-dlp completed: target={target_id}, title={clean_title}")
                    return "SUCCESS", ""

                last_error = "yt-dlp exact share/v downloaded but move_files found no valid media"

            except Exception as e:
                last_error = str(e)
                logger.warning(f"FB v12.44 exact share/v yt-dlp failed: {last_error}")

    clear_temp()
    return "RETRY", (
        "Facebook share/v foreground=0 and exact target-id yt-dlp fallback failed; "
        f"target={target_id}; {last_error or 'no exact video candidate'}"
    )

# ---------------------------------------------------------------------------


# v12.45 ---------------------------------------------------------------------
# Exact content duplicate guard:
#
# Failure fixed:
#   /share/p/1BuswMyqWU/ expected 8 images.  The viewer collected 7 unique
#   media responses, then the response-capture recovery appended a previously
#   captured file as the 8th output.  The final output was marked SUCCESS, but
#   some final JPGs were byte-identical duplicates.
#
# Policy:
#   - A gallery must never reach SUCCESS by filling missing slots with a byte-
#     identical image.
#   - Response-capture recovery must skip content hashes already present in
#     viewer_items.
#   - Final output download must dedupe by exact file hash in addition to fbid /
#     CDN logical key.
#   - If the missing photo cannot be proven, return RETRY instead of false
#     SUCCESS.

def _fb_v1245_file_md5(path: str) -> str:
    try:
        if not path or not os.path.exists(path) or not os.path.isfile(path):
            return ""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _fb_v1245_candidate_local_paths(pack) -> list[str]:
    out = []

    def add_path(value):
        s = str(value or "").strip()
        if not s:
            return
        if s.startswith("file://"):
            s = s[7:]
        if s and os.path.exists(s):
            out.append(s)

    if isinstance(pack, dict):
        add_path(pack.get("temp_path"))
        add_path(pack.get("persisted_path"))
        add_path(pack.get("local_path"))
        add_path(pack.get("src"))
        for cand in pack.get("candidates") or []:
            if isinstance(cand, dict):
                add_path(cand.get("temp_path"))
                add_path(cand.get("persisted_path"))
                add_path(cand.get("local_path"))
                add_path(cand.get("src"))
            else:
                add_path(cand)
    elif isinstance(pack, str):
        add_path(pack)

    seen = set()
    uniq = []
    for p in out:
        n = os.path.normcase(os.path.abspath(p))
        if n in seen:
            continue
        seen.add(n)
        uniq.append(p)
    return uniq


def _fb_v1245_item_content_hash(pack) -> str:
    for path in _fb_v1245_candidate_local_paths(pack):
        md5 = _fb_v1245_file_md5(path)
        if md5:
            return f"md5:{md5}"
    return ""


def _fb_v1229_append_capture_items_before_guard(
    viewer_items: list[dict],
    *,
    expected_photo_count: int,
) -> list[dict]:
    """Append captured response files until viewer_items can satisfy expected count.

    v12.45: do not append a capture if its exact file content already exists in
    the current viewer item set.  This prevents filling the last missing gallery
    slot with a duplicate and then reporting false SUCCESS.
    """
    current = list(viewer_items or [])
    try:
        expected = int(expected_photo_count or 0)
    except Exception:
        expected = 0
    if not expected or len(current) >= expected:
        return current

    captures = _fb_v1228_capture_files_from_temp()
    logger.info(
        f"FB v12.45 pre-guard response-capture recovery attempt: "
        f"current={len(current)}, expected={expected}, captures={len(captures)}"
    )

    seen = set()
    seen_content = set()
    for item in _fb_v1232_pack_dicts(current):
        src = item.get("src") or ""
        if src:
            seen.add(_media_key_from_src(src))
        tp = item.get("temp_path") or item.get("persisted_path") or ""
        if tp:
            seen.add(_fb_v1230_capture_item_key(tp))
            seen.add(os.path.basename(tp))
        content_key = _fb_v1245_item_content_hash(item)
        if content_key:
            seen_content.add(content_key)

    for cap_path in captures:
        key = _fb_v1230_capture_item_key(cap_path)
        name = os.path.basename(cap_path)
        if key in seen or name in seen:
            continue

        try:
            cap_size = os.path.getsize(cap_path)
        except Exception:
            cap_size = 0
        if cap_size < 12 * 1024:
            continue

        content_key = _fb_v1245_item_content_hash(cap_path)
        if content_key and content_key in seen_content:
            logger.info(
                f"FB v12.45 response-capture duplicate content skipped: "
                f"source={name}, hash={content_key}"
            )
            continue

        src = f"file://{cap_path}"
        seen.add(key)
        seen.add(name)
        seen.add(_media_key_from_src(src))
        if content_key:
            seen_content.add(content_key)

        current.append({
            "type": "image",
            "src": src,
            "candidates": [src],
            "temp_path": cap_path,
            "source": "v12.45-response-capture-pre-guard",
            "media_id": os.path.splitext(name)[0],
            "score": 1,
            "_allow_fb_best_available_source": True,
        })
        logger.info(
            f"FB v12.45 pre-guard response-capture appended: "
            f"{len(current)}/{expected}, source={name}, bytes={cap_size}"
        )
        if len(current) >= expected:
            break

    return current



# ---------------------------------------------------------------------------


# v12.46 ---------------------------------------------------------------------
# Let grid-tile recovery run before a near-complete +N gallery returns RETRY.
# Also block post-download capture filling from re-adding a byte-identical image.

def _fb_v1228_fill_outputs_from_captures(
    ordered_output_files: list[str],
    *,
    success_count: int,
    expected_photo_count: int,
) -> tuple[int, list[str]]:
    """Fill missing gallery outputs from captured cap_* files.

    v12.46: a captured file may have a different filename but identical image
    content.  Do not use it to satisfy exact-count completion; otherwise 4.jpg
    and 7.jpg can be the same photo while the task is marked SUCCESS.
    """
    try:
        expected = int(expected_photo_count or 0)
    except Exception:
        expected = 0

    if not expected or success_count >= expected:
        return success_count, ordered_output_files

    existing_names = set()
    existing_sizes = set()
    existing_hashes = set()

    for p in ordered_output_files or []:
        try:
            existing_names.add(os.path.basename(p))
            existing_sizes.add(os.path.getsize(p))
            content_key = _fb_v1245_item_content_hash(p)
            if content_key:
                existing_hashes.add(content_key)
        except Exception:
            pass

    for cap in _fb_v1228_capture_files_from_temp():
        if success_count >= expected:
            break
        try:
            cap_name = os.path.basename(cap)
            if cap_name in existing_names:
                continue

            size = os.path.getsize(cap)
            if size in existing_sizes and size < 40 * 1024:
                continue

            content_key = _fb_v1245_item_content_hash(cap)
            if content_key and content_key in existing_hashes:
                logger.info(
                    f"FB v12.46 response-capture output duplicate-content skipped: "
                    f"source={cap_name}, hash={content_key}"
                )
                continue

            ext = os.path.splitext(cap)[1].lower()
            if ext not in (".jpg", ".jpeg", ".png", ".webp"):
                ext = ".jpg"

            out_path = os.path.join(TEMP_DIR, f"fb_{success_count + 1:04d}{ext}")
            shutil.copy2(cap, out_path)
            ordered_output_files.append(out_path)
            existing_names.add(cap_name)
            existing_sizes.add(size)
            if content_key:
                existing_hashes.add(content_key)
            success_count += 1
            logger.info(
                f"FB v12.46 response-capture output filled: "
                f"{success_count}/{expected}, source={cap_name}, bytes={size}"
            )
        except Exception as e:
            logger.debug(f"FB v12.46 response-capture fill skipped: {e}")

    return success_count, ordered_output_files

# ---------------------------------------------------------------------------


# v12.47 ---------------------------------------------------------------------
# Exhaust every candidate inside the same proven photo pack before declaring it
# duplicate/incomplete.  v12.45 correctly prevented duplicate-content false
# SUCCESS, but it dropped the whole logical photo as soon as the best candidate
# downloaded to the same bytes as an earlier image.  Some FB albums expose the
# correct hidden +N image as the second/third candidate of that same pack.  This
# override tries each candidate group independently and only skips the pack after
# all same-pack alternatives fail or duplicate existing content.

def _fb_v1247_normalize_candidate_list(pack) -> list[dict]:
    if not isinstance(pack, dict):
        return []
    if "_pack_primary_candidates" in globals():
        candidates = _pack_primary_candidates(pack)
    else:
        candidates = list(pack.get("candidates") or [])
        if pack.get("src"):
            candidates.insert(0, {"src": pack.get("src"), "type": pack.get("type", "image"), "score": pack.get("score", 0)})
    if "_fb_v1235_normalize_candidates" in globals():
        candidates = _fb_v1235_normalize_candidates(candidates, inherited=pack)
    else:
        candidates = [c for c in candidates if isinstance(c, dict) and c.get("src")]
    candidates = _fb_v1240_best_candidate_order(candidates)

    seen = set()
    out = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        src = str(cand.get("src") or "")
        temp_path = str(cand.get("temp_path") or cand.get("persisted_path") or "")
        key = (src, temp_path)
        if not src and not temp_path:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def _fb_v1247_download_one_candidate(context, cand: dict, dst_base: str, referer: str):
    # Pass one candidate at a time so a duplicate best URL does not prevent
    # trying the next proven URL in the same pack.
    return _download_best_candidate(context, [cand], dst_base, referer=referer)


def _download_viewer_items(context, viewer_items, referer: str):
    success_count = 0
    by_photo: dict[str, dict] = {}
    by_content: dict[str, dict] = {}

    normalized_items = []
    for raw in viewer_items or []:
        if "_fb_v1234_candidate_pack_list" in globals():
            normalized_items.extend(_fb_v1234_candidate_pack_list(raw))
        elif isinstance(raw, dict):
            normalized_items.append(raw)

    normalized_items = _fb_v1240_group_items_by_photo(normalized_items)

    for attempt_i, pack in enumerate(normalized_items, 1):
        if not isinstance(pack, dict):
            continue

        logical_key = pack.get("_logical_photo_key_v1240") or _fb_v1240_logical_photo_key(pack)
        if not logical_key:
            logical_key = _media_key_from_src(pack.get("src", ""))

        candidates = _fb_v1247_normalize_candidate_list(pack)
        if not candidates:
            logger.warning(f"FB v12.47 候選第 {attempt_i} 張沒有有效候選，略過")
            continue

        accepted = False
        duplicate_seen = 0
        failed_seen = 0

        for cand_i, cand in enumerate(candidates[:24], 1):
            dst_base = os.path.join(TEMP_DIR, f"fb_{success_count + 1:04d}_p{attempt_i:03d}_c{cand_i:02d}")
            try:
                final_dst, size = _fb_v1247_download_one_candidate(context, cand, dst_base, referer)
                new_quality = _fb_v1240_file_quality(final_dst)
                content_key = _fb_v1245_item_content_hash(final_dst)

                if content_key and content_key in by_content:
                    old = by_content[content_key]
                    old_path = old.get("path", "")
                    old_quality = old.get("quality", (0, 0, 0))
                    duplicate_seen += 1
                    if new_quality > old_quality:
                        logger.info(
                            f"FB v12.47 replace duplicate-content lower-quality output: "
                            f"hash={content_key}, old={os.path.basename(old_path)}, "
                            f"new={os.path.basename(final_dst)}"
                        )
                        try:
                            if old_path and os.path.exists(old_path):
                                os.remove(old_path)
                        except Exception:
                            pass
                        old["path"] = final_dst
                        old["quality"] = new_quality
                        old["size"] = size
                        by_photo[logical_key] = old
                    else:
                        logger.info(
                            f"FB v12.47 candidate duplicate-content skipped, try next: "
                            f"pack={attempt_i}, cand={cand_i}/{len(candidates)}, "
                            f"hash={content_key}, logical={logical_key}, quality={new_quality}"
                        )
                        try:
                            os.remove(final_dst)
                        except Exception:
                            pass
                    continue

                old = by_photo.get(logical_key)
                if old:
                    old_path = old.get("path", "")
                    old_quality = old.get("quality", (0, 0, 0))
                    duplicate_seen += 1
                    if new_quality > old_quality:
                        logger.info(
                            f"FB v12.47 replace lower-resolution duplicate logical photo: "
                            f"{logical_key}, old={os.path.basename(old_path)}, new={os.path.basename(final_dst)}"
                        )
                        try:
                            if old_path and os.path.exists(old_path):
                                os.remove(old_path)
                        except Exception:
                            pass
                        old["path"] = final_dst
                        old["quality"] = new_quality
                        old["size"] = size
                        if content_key:
                            by_content[content_key] = old
                    else:
                        logger.info(
                            f"FB v12.47 candidate same logical photo skipped, try next: "
                            f"pack={attempt_i}, cand={cand_i}/{len(candidates)}, logical={logical_key}, quality={new_quality}"
                        )
                        try:
                            os.remove(final_dst)
                        except Exception:
                            pass
                    continue

                rec = {"path": final_dst, "quality": new_quality, "size": size}
                by_photo[logical_key] = rec
                if content_key:
                    by_content[content_key] = rec
                success_count += 1
                accepted = True
                logger.info(
                    f"FB 已下載輸出第 {success_count} 張: {os.path.basename(final_dst)} "
                    f"({size} bytes) logical={logical_key}, content={content_key or '-'}, "
                    f"quality={new_quality}, pack={attempt_i}, cand={cand_i}/{len(candidates)}"
                )
                break

            except Exception as e:
                failed_seen += 1
                logger.debug(
                    f"FB v12.47 candidate failed, try next: "
                    f"pack={attempt_i}, cand={cand_i}/{len(candidates)}, error={e}"
                )
                try:
                    folder = os.path.dirname(dst_base)
                    prefix = os.path.basename(dst_base)
                    for fn in os.listdir(folder):
                        if fn.startswith(prefix):
                            os.remove(os.path.join(folder, fn))
                except Exception:
                    pass

        if not accepted:
            logger.warning(
                f"FB v12.47 pack unresolved after exhaustive candidate scan: "
                f"pack={attempt_i}, logical={logical_key}, candidates={len(candidates)}, "
                f"duplicates={duplicate_seen}, failed={failed_seen}"
            )

    ordered_output_files = []
    compact_i = 1
    emitted_content = set()
    for key, rec in by_photo.items():
        path = rec.get("path", "")
        if not path or not os.path.exists(path):
            continue
        content_key = _fb_v1245_item_content_hash(path)
        if content_key and content_key in emitted_content:
            logger.info(
                f"FB v12.47 final duplicate-content compact skip: "
                f"hash={content_key}, file={os.path.basename(path)}"
            )
            try:
                os.remove(path)
            except Exception:
                pass
            continue
        if content_key:
            emitted_content.add(content_key)

        ext = os.path.splitext(path)[1].lower() or ".jpg"
        target = os.path.join(TEMP_DIR, f"fb_{compact_i:04d}{ext}")
        if os.path.abspath(path) != os.path.abspath(target):
            try:
                if os.path.exists(target):
                    os.remove(target)
                os.replace(path, target)
                path = target
            except Exception:
                pass
        ordered_output_files.append(path)
        compact_i += 1

    success_count = len(ordered_output_files)
    logger.info(f"FB v12.47 unique content output count={success_count}")
    logger.info(f"FB unique output media count={success_count}")
    return success_count, ordered_output_files


# v12.47 strict: do not let a partial RETRY gallery leave old folder contents
# that look like a successful download.  The original final guard already blocks
# move_files_ordered when output < expected; this helper is used by manual checks
# and future recovery paths to keep the policy explicit.
def _fb_v1247_clear_partial_outputs_on_incomplete(success_count: int, expected_photo_count: int) -> None:
    try:
        if expected_photo_count and success_count < int(expected_photo_count):
            logger.info(
                f"FB v12.47 incomplete gallery cleanup policy active: "
                f"output={success_count}/target={expected_photo_count}; keep RETRY, no false SUCCESS"
            )
    except Exception:
        pass

# ---------------------------------------------------------------------------


def _collect_fb_media_playwright(url: str):
    logger.info("FB v14.2 integrated pipeline active: immutable GalleryPlan + validated helper signatures + strict completeness")
    try:
        _target_contract = _contract_parse_facebook_target(url)
        logger.info(
            f"FB v13.0 target identity: kind={_target_contract.kind}, "
            f"numeric_id={_target_contract.numeric_id or '-'}, "
            f"story_fbid={_target_contract.story_fbid or '-'}, "
            f"post_id={_target_contract.post_id or '-'}, share_token={_target_contract.share_token or '-'}"
        )
    except Exception as _contract_e:
        logger.debug(f"FB v13.0 target identity parse skipped: {_contract_e}")
    clear_temp()

    browser = None
    context = None

    try:
        resolved = _resolve_share_url(url)
        network_items = []
        # v13.1: bounded structured-response buffer.  We keep only GraphQL/JSON/text
        # responses that can carry exact post/photo identities; media bodies are handled
        # by the existing binary capture path.
        structured_payloads_v131 = []
        structured_payload_state_v133 = {"bytes": 0}

        with sync_playwright() as p:
            user_data_dir = _get_fb_parser_profile_root()
            profile_dir = _resolve_fb_chrome_profile_directory()
            logger.info(
                f"FB 啟用 FB_Parser persistent profile: user_data_dir={user_data_dir}, "
                f"profile={profile_dir}, cookies.txt=legacy-fallback"
            )

            context = _launch_fb_persistent_context_with_retry(
                p,
                user_data_dir=user_data_dir,
                profile_dir=profile_dir,
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

                    # v13.1 exact-PCB payload recovery.  Facebook often keeps the
                    # hidden +N slide identities in GraphQL/JSON even when the DOM
                    # exposes only five photo anchors.  Capture a bounded text copy
                    # now, then parse it only after the post's pcb id is proven.
                    if len(structured_payloads_v131) < 96 and int(structured_payload_state_v133.get("bytes", 0)) < 24_000_000:
                        low_u_v131 = str(u or "").lower()
                        ctype_v131 = ""
                        try:
                            ctype_v131 = str(resp.headers.get("content-type", "") or "").lower()
                        except Exception:
                            ctype_v131 = ""
                        if (
                            "graphql" in low_u_v131
                            or "/api/" in low_u_v131
                            or "application/json" in ctype_v131
                            or "text/javascript" in ctype_v131
                        ):
                            try:
                                txt_v131 = resp.text()
                                if txt_v131 and len(txt_v131) <= 1_200_000:
                                    low_txt_v131 = txt_v131.lower()
                                    if "fbid" in low_txt_v131 or "set=pcb" in low_txt_v131 or "\"photo\"" in low_txt_v131:
                                        structured_payloads_v131.append(txt_v131)
                                        structured_payload_state_v133["bytes"] = int(structured_payload_state_v133.get("bytes", 0)) + len(txt_v131)
                            except Exception:
                                pass

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
            if _is_fb_reel_url(url) or _is_fb_reel_url(resolved) or _is_fb_reel_url(page.url):
                observed_reel_id = _extract_canonical_fb_reel_id_from_page(page)
                reel_fallback = _fb_reel_fallback_title(
                    f"https://www.facebook.com/reel/{observed_reel_id}/"
                    if observed_reel_id else (resolved or url)
                )
                reel_title = _get_fb_reel_caption_title(page, fallback=reel_fallback)
                if not reel_title or _is_fallback_fb_title(reel_title):
                    reel_title = reel_fallback

                # Publish the resolved caption/account to the GUI before media download.
                reel_account = _get_fb_page_account(page, fallback_title=reel_title)
                reel_title, reel_account = _publish_fb_task_metadata(
                    url,
                    reel_title,
                    reel_account,
                    page=page,
                )
                if resolved and resolved != url:
                    _publish_fb_task_metadata(resolved, reel_title, reel_account, page=page)

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

                    # v12.18: first use a foreground-only response burst triggered by
                    # focusing/clicking the active visible Reel.  This prevents preloaded
                    # recommendation videos from winning solely because they are larger.
                    foreground_candidates = _collect_reel_foreground_video_candidates_v1218(
                        page,
                        context,
                        observed_reel_id or _extract_fb_reel_or_share_id(resolved or url),
                        resolved,
                    )

                    strict_share_video_v1243 = bool(
                        re.search(r"/(?:share/[rv]|reels?)/", f"{url} {resolved} {page.url}", flags=re.I)
                    )

                    if strict_share_video_v1243:
                        # v12.43:
                        # /share/v/ pages frequently preload unrelated videos behind the
                        # active viewer.  The old broad page/network fallback can pair the
                        # correct title with a wrong MP4.  For share/v use only foreground
                        # responses triggered after focusing/clicking the active video.
                        reel_candidates = []
                        broad_video_candidates = []
                        video_candidates = _dedupe_ordered(foreground_candidates)
                        logger.info(
                            f"FB v12.49 strict short-video mode: "
                            f"foreground={len(foreground_candidates)}, title={reel_title}"
                        )
                    else:
                        # Keep the proven pre-v11.91 collection path as a fallback for
                        # ordinary Reel URLs, but rank it behind foreground candidates.
                        reel_candidates = _collect_current_page_candidates(
                            page,
                            network_items=network_items,
                            include_network=True,
                            include_meta=True,
                            include_html=True,
                        )

                        broad_video_candidates = []
                        for cand in reel_candidates:
                            if not isinstance(cand, dict):
                                continue
                            src = cand.get("src") or ""
                            if cand.get("type") == "video" or _is_probably_video_url(src) or any(
                                x in src.lower() for x in [".mp4", ".m4v", ".mov"]
                            ):
                                c2 = dict(cand)
                                c2["type"] = "video"
                                c2["score"] = int(c2.get("score") or 0) + 1500000
                                broad_video_candidates.append(c2)

                        video_candidates = _dedupe_ordered(foreground_candidates + broad_video_candidates)

                    logger.info(
                        f"FB Reel video candidate count={len(video_candidates)} "
                        f"foreground={len(foreground_candidates)} from total={len(reel_candidates)}; title={reel_title}"
                    )

                    if not video_candidates:
                        if strict_share_video_v1243:
                            exact_target_v1249 = observed_reel_id or _extract_fb_reel_or_share_id(resolved or url)
                            logger.info(
                                "FB v12.49 strict short-video foreground=0; try exact-id "
                                "serialized direct-media recovery before yt-dlp"
                            )
                            status_v1249, err_v1249 = _fb_v1249_download_exact_short_video(
                                context, exact_target_v1249, url, resolved, reel_title
                            )
                            if status_v1249 == "SUCCESS":
                                return status_v1249, err_v1249
                            # Keep v12.44 as a last compatibility fallback, but only for
                            # exact numeric identity. Broad preloaded candidates remain blocked.
                            if str(exact_target_v1249 or '').isdigit():
                                logger.info(
                                    f"FB v12.49 exact direct-media unavailable ({err_v1249}); "
                                    "try exact target-id yt-dlp compatibility fallback"
                                )
                                return _download_share_v_exact_ytdlp_v1244(
                                    context, url, resolved, exact_target_v1249, reel_title
                                )
                            clear_temp()
                            return "RETRY", err_v1249
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

            # v12.39: exact story.php/story_fbid/post_id targets are single-entry
            # posts.  Do not click +N / first photo before identity gating; that
            # opens the surrounding album/viewer and pollutes the target with
            # other dates or recommendation media.
            exact_story_pre_v1239 = _is_exact_story_entry_v1239(url, resolved)

            # 第二階段：點 +12 / 更多照片，再收集完整列表
            if exact_story_pre_v1239:
                logger.info(
                    "FB v12.39 exact story entry detected before +N click; "
                    "skip gallery expansion and keep foreground story page"
                )
                grid_after = list(grid_before or [])
                links_after = list(links_before or [])
            else:
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

            explicit_story_single_mode = False
            explicit_story_target_link = ""
            explicit_story_visible_pack_v1239 = None
            explicit_album_single_mode = False
            exact_story_entry_v1239 = bool(locals().get("exact_story_pre_v1239")) or _is_exact_story_entry_v1239(url, resolved)
            share_p_gallery_like_v1226 = bool(
                (not exact_story_entry_v1239)
                and re.search(r"/share/p/", f"{url} {resolved}", flags=re.I)
                and (plus_count_before or len(ordered_grid_items) >= 2 or len(ordered_links) >= 3)
            )
            if _is_explicit_story_post_url_v1217(url, resolved) and not share_p_gallery_like_v1226:
                if exact_story_entry_v1239:
                    explicit_story_visible_pack_v1239 = _build_explicit_story_visible_pack_v1239(page, network_items=network_items)
                if explicit_story_visible_pack_v1239:
                    explicit_story_single_mode = True
                    explicit_story_target_link = "__v12_39_visible_story_media__"
                    ordered_links = []
                    ordered_grid_items = []
                    plus_count_before = 0
                    logger.info(
                        "FB v12.39 explicit story single-media identity gate: "
                        "use visible foreground story media only; skip album/set/+N/viewer gallery"
                    )
                else:
                    story_fbid_v1217 = _extract_story_fbid_v1217(url, resolved)
                    explicit_story_target_link, explicit_story_grid_items = _select_story_proximal_photo_link_v1217(
                        ordered_links,
                        ordered_grid_items,
                        story_fbid_v1217,
                    )
                    if explicit_story_target_link:
                        explicit_story_single_mode = True
                        ordered_links = [explicit_story_target_link]
                        ordered_grid_items = explicit_story_grid_items
                        plus_count_before = 0
                        logger.info(
                            "FB v12.18 explicit story single-photo identity gate: "
                            "use proximal target photo only; skip album/set first-anchor and viewer gallery"
                        )
                    else:
                        logger.warning(
                            "FB v12.17 explicit story could not select proximal target photo; "
                            "return RETRY to avoid wrong post"
                        )
                        clear_temp()
                        return "RETRY", "Facebook explicit story target photo identity not proven"
            elif share_p_gallery_like_v1226:
                logger.info(
                    "FB v12.26 share/p gallery mode: skip explicit story single-photo gate; "
                    f"grid={len(ordered_grid_items)}, links={len(ordered_links)}, plus={plus_count_before}"
                )

            dominant_pcb_key = "" if explicit_story_single_mode else _dominant_pcb_key_from_links(ordered_links)

            if (
                not explicit_story_single_mode
                and _is_album_context_single_photo_v1223(
                    url=url,
                    resolved=resolved,
                    dominant_pcb_key=dominant_pcb_key,
                    ordered_grid_items=ordered_grid_items,
                    ordered_links=ordered_links,
                    plus_count_before=plus_count_before,
                )
            ):
                explicit_album_single_mode = True
                logger.warning(
                    "FB v12.23 album-context single-photo gate enabled: "
                    f"scope={dominant_pcb_key}, links={len(ordered_links)}, grid={len(ordered_grid_items)}; "
                    "skip album viewer/gallery pollution"
                )

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

                # v12.49: the +N tile can hide photo permalinks that never appear
                # as live DOM anchors. Recover only exact same-pcb links from the
                # serialized post payload, preserving all existing visible-link
                # behavior and never widening to another post/album.
                try:
                    hidden_links_v1249 = _fb_v1249_collect_hidden_pcb_photo_links(page, dominant_pcb_key)
                    added_v1249 = 0
                    for _u in hidden_links_v1249:
                        if _u not in ordered_links:
                            ordered_links.append(_u)
                            added_v1249 += 1
                    if added_v1249:
                        logger.info(
                            f"FB v12.49 exact-pcb hidden links merged: added={added_v1249}, "
                            f"total_links={len(ordered_links)}, pcb={dominant_pcb_key}"
                        )
                except Exception as _e:
                    logger.debug(f"FB v12.49 hidden pcb merge skipped: {_e}")

                # v13.1: merge exact-PCB links discovered from captured structured
                # responses.  This is still same-post only and is the primary fix
                # for galleries that repeatedly stall at 7/8 because the eighth
                # permalink is virtualized and never becomes a live DOM anchor.
                try:
                    payload_links_v131 = _fb_v131_collect_exact_pcb_links_from_payloads(
                        structured_payloads_v131, dominant_pcb_key
                    )
                    added_payload_v131 = 0
                    for _u in payload_links_v131:
                        if _u not in ordered_links:
                            ordered_links.append(_u)
                            added_payload_v131 += 1
                    if added_payload_v131:
                        logger.info(
                            f"FB v13.2 exact-pcb payload merge: added={added_payload_v131}, "
                            f"total_links={len(ordered_links)}, pcb={dominant_pcb_key}"
                        )
                except Exception as _e:
                    logger.debug(f"FB v13.2 exact-pcb payload merge skipped: {_e}")

            # v11.19 / v12.18: title must be scoped to the selected media.
            # Generic page selectors can grab neighboring/recommended posts in logged-in feeds.
            if explicit_story_single_mode and explicit_story_target_link:
                title = _get_story_target_title_v1218(page, explicit_story_target_link, fallback=title)
            elif dominant_pcb_key:
                title = _get_post_folder_name_for_pcb(page, dominant_pcb_key, fallback=title)
            else:
                title = _get_post_folder_name(page)

            # v12.24: album/photo viewer uses the right-side viewer panel caption,
            # not normal feed article selectors.
            fb_account = _get_fb_page_account(page, fallback_title=title)
            exact_pcb_account_v1250 = ""
            group_container_account_v131 = ""
            if dominant_pcb_key:
                # v13.1 GUI semantics: a group share belongs to the group/container,
                # not to the member author shown under the group name.  This fixes
                # BubuDudu lover/Yuni Cahyani and similar exact-PCB group posts.
                group_container_account_v131 = _fb_v131_get_group_container_account(page, dominant_pcb_key)
                exact_pcb_account_v1250 = _get_fb_exact_pcb_account_v1250(page, dominant_pcb_key)
                fb_account = _contract_choose_post_account(
                    group_root=group_container_account_v131,
                    page_or_author=exact_pcb_account_v1250,
                    fallback=fb_account,
                )
            if explicit_album_single_mode and (
                not title or title == "Facebook_Post" or _is_fallback_fb_title(title)
            ):
                title = _get_album_context_caption_v1224(
                    page,
                    fallback=title,
                    account=fb_account,
                )

            # v12.20: publish both resolved caption and account to the GUI immediately.
            if not exact_pcb_account_v1250:
                fb_account = _get_fb_page_account(page, fallback_title=title) or fb_account
            title, fb_account = _publish_fb_task_metadata(
                url, title, fb_account, page=page, account_locked=bool(group_container_account_v131 or exact_pcb_account_v1250)
            )
            if resolved and resolved != url:
                _publish_fb_task_metadata(
                    resolved, title, fb_account, page=page, account_locked=bool(group_container_account_v131 or exact_pcb_account_v1250)
                )

            provisional_expected_photo_count = _estimate_expected_photo_count(
                page,
                ordered_links,
                ordered_grid_items,
                plus_count=plus_count_before,
            )
            # v13.5 authoritative gallery-count contract.
            #
            # Facebook's "+N" badge is painted ON TOP OF the last visible tile.
            # Therefore the total is:
            #
            #     visible tiles before the overlay + N
            #   = (visible_grid_count - 1) + N
            #
            # Example:
            #   5 visible tiles with "+3"  -> 4 + 3  = 7 total
            #   5 visible tiles with "+76" -> 4 + 76 = 80 total
            #
            # v13.1 incorrectly treated the overlay tile as an additional visible
            # photo and used grid + N, creating the exact permanent N-1 RETRY
            # observed in 7/8 and 80/81 galleries.
            #
            # Keep exact-PCB links as independent evidence.  They may raise the
            # target only when they prove more unique exact-post photo identities.
            # A local defensive correction is intentionally retained here so an
            # older fb_contracts.py cannot reintroduce the off-by-one failure.
            try:
                _scoped_link_count_v131 = 0
                _scoped_fbid_v135 = set()
                _pcb_id_v131 = ""
                if str(dominant_pcb_key or "").startswith("pcb:"):
                    _pcb_id_v131 = str(dominant_pcb_key).split(":", 1)[1]

                for _u in ordered_links or []:
                    _su = str(_u or "")
                    _belongs_v135 = False
                    if _pcb_id_v131:
                        _belongs_v135 = (
                            f"set=pcb.{_pcb_id_v131}" in _su
                            or f"set=pcb%2E{_pcb_id_v131}" in _su
                        )
                    else:
                        _belongs_v135 = _is_true_photo_link(_su)

                    if not _belongs_v135:
                        continue

                    _m_v135 = re.search(r"[?&]fbid=(\d{8,})", _su, flags=re.I)
                    if _m_v135:
                        _scoped_fbid_v135.add(_m_v135.group(1))
                    else:
                        # Retain a bounded raw-link count only when no fbid is
                        # exposed.  Unique fbids are preferred whenever possible.
                        _scoped_link_count_v131 += 1

                if _scoped_fbid_v135:
                    _scoped_link_count_v131 += len(_scoped_fbid_v135)

                _grid_v135 = len(ordered_grid_items or [])
                _plus_v135 = max(0, int(plus_count_before or 0))

                _plan_v131 = _contract_derive_gallery_plan(
                    visible_grid_count=_grid_v135,
                    plus_count=_plus_v135,
                    scoped_link_count=_scoped_link_count_v131,
                )

                _overlay_target_v135 = 0
                if _grid_v135 > 0 and _plus_v135 > 0:
                    _overlay_target_v135 = max(1, _grid_v135 - 1) + _plus_v135

                _contract_target_v135 = int(getattr(_plan_v131, "expected_count", 0) or 0)

                # Defensive compatibility with pre-v13.5 fb_contracts.py:
                # if it reports grid+plus, replace only that known-wrong count.
                _legacy_wrong_target_v135 = (
                    _grid_v135 + _plus_v135
                    if _grid_v135 > 0 and _plus_v135 > 0
                    else 0
                )
                if (
                    _overlay_target_v135 > 0
                    and _contract_target_v135 == _legacy_wrong_target_v135
                    and _legacy_wrong_target_v135 == _overlay_target_v135 + 1
                ):
                    logger.warning(
                        "FB v13.6 corrected legacy +N off-by-one contract: "
                        f"contract={_contract_target_v135} -> overlay={_overlay_target_v135}, "
                        f"grid={_grid_v135}, plus={_plus_v135}"
                    )
                    _contract_target_v135 = max(
                        _overlay_target_v135,
                        _scoped_link_count_v131,
                    )

                _trusted_target_v135 = max(
                    int(provisional_expected_photo_count or 0),
                    _contract_target_v135,
                    _overlay_target_v135,
                    _scoped_link_count_v131,
                )

                # _estimate_expected_photo_count() already uses the correct
                # (4 + N) semantics for the common five-tile layout.  Never let
                # the known legacy grid+N value raise it by exactly one.
                if (
                    _overlay_target_v135 > 0
                    and _trusted_target_v135 == _legacy_wrong_target_v135
                    and _legacy_wrong_target_v135 == _overlay_target_v135 + 1
                    and _scoped_link_count_v131 <= _overlay_target_v135
                ):
                    _trusted_target_v135 = _overlay_target_v135

                if _trusted_target_v135 > 0:
                    provisional_expected_photo_count = _trusted_target_v135

                logger.info(
                    f"FB v13.6 gallery plan: target={provisional_expected_photo_count}, "
                    f"grid={_grid_v135}, plus={_plus_v135}, "
                    f"overlay_total={_overlay_target_v135 or '-'}, "
                    f"unique_scoped_links={_scoped_link_count_v131}, "
                    f"contract={_contract_target_v135}, "
                    f"confidence={getattr(_plan_v131, 'confidence', '-')}, "
                    f"reason={getattr(_plan_v131, 'reason', '-')}"
                )
            except Exception as _e:
                logger.debug(f"FB v13.5 gallery plan skipped: {_e}")
            if explicit_story_single_mode:
                logger.info(
                    f"FB v12.18 explicit story expected target forced: "
                    f"{provisional_expected_photo_count}->1"
                )
                provisional_expected_photo_count = 1
            if explicit_album_single_mode:
                logger.info(
                    f"FB v12.23 album-context expected target forced: "
                    f"{provisional_expected_photo_count}->1"
                )
                provisional_expected_photo_count = 1
            logger.info(f"FB v14.1 provisional gallery evidence target={provisional_expected_photo_count}")

            # v12.50: recover virtualized +N photo permalinks before the expensive
            # image-harvest loop.  Only exact set=pcb links are accepted.
            try:
                if (
                    provisional_expected_photo_count
                    and int(provisional_expected_photo_count) > 1
                    and plus_count_before
                    and share_p_gallery_like_v1226
                    and str(dominant_pcb_key or "").startswith("pcb:")
                ):
                    pcb_id_v1250 = str(dominant_pcb_key).split(":", 1)[1]
                    exact_now_v1250 = [
                        _u for _u in (ordered_links or [])
                        if _is_true_photo_link(str(_u or ""))
                        and (f"set=pcb.{pcb_id_v1250}" in str(_u) or f"set=pcb%2E{pcb_id_v1250}" in str(_u))
                    ]
                    if len(exact_now_v1250) < int(provisional_expected_photo_count):
                        discovered_v1250 = _fb_v1250_discover_exact_pcb_links_from_viewer(
                            context, exact_now_v1250, dominant_pcb_key, int(provisional_expected_photo_count)
                        )
                        added_v1250 = 0
                        for _u in discovered_v1250:
                            if _u not in ordered_links:
                                ordered_links.append(_u)
                                added_v1250 += 1
                        logger.info(
                            f"FB v12.50 exact-pcb pre-harvest merge: added={added_v1250}, "
                            f"scoped_links={len([x for x in ordered_links if f'set=pcb.{pcb_id_v1250}' in str(x) or f'set=pcb%2E{pcb_id_v1250}' in str(x)])}, "
                            f"target={provisional_expected_photo_count}"
                        )
            except Exception as _e:
                logger.debug(f"FB v12.50 exact-pcb pre-harvest recovery skipped: {_e}")

            # v11.46 safety:
            # This uploaded full version does not include the later large-album
            # helper stack from the previous v11.40-v11.45 branch.  Keep this flag
            # explicit so the narrow single-photo duplicate-link correction below
            # does not raise NameError and does not alter normal gallery behavior.
            large_album_mode = False

            if provisional_expected_photo_count and len(ordered_links) > provisional_expected_photo_count + 3:
                logger.warning(
                    f"FB bounded scope: ordered_links={len(ordered_links)} > expected={provisional_expected_photo_count}，"
                    "後段多半是首頁/推薦連結，先裁切"
                )
                ordered_links = ordered_links[:provisional_expected_photo_count + 3]

            link_items = []
            viewer_items = []

            if explicit_story_visible_pack_v1239:
                link_items = [explicit_story_visible_pack_v1239]
                logger.info("FB v12.39 explicit story link_items forced to single visible media")
            elif ordered_grid_items or ordered_links:
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
                    provisional_expected_photo_count
                    and plus_count_before
                    and int(plus_count_before) <= 1
                    and dominant_pcb_key
                    and scoped_manifest_count >= 3
                    and scoped_manifest_count == scoped_link_count
                    and int(provisional_expected_photo_count) > scoped_manifest_count
                    and len(ordered_links or []) <= scoped_manifest_count + 1
                    and len(ordered_grid_items or []) <= scoped_manifest_count + 2
                ):
                    logger.info(
                        "FB v14.1 pre-final evidence: scoped manifest correction: "
                        f"expected {provisional_expected_photo_count}->{scoped_manifest_count}, "
                        f"plus={plus_count_before}, links={len(ordered_links or [])}, "
                        f"grid={len(ordered_grid_items or [])}, manifest={scoped_manifest_count}, "
                        f"pcb={dominant_pcb_key}"
                    )
                    provisional_expected_photo_count = scoped_manifest_count
            except Exception as _e:
                logger.debug(f"FB v11.93 scoped manifest target correction skipped: {_e}")

            # v12.42 exact-pcb manifest/grid target correction:
            # Some share/p dialog pages show a +N overlay from the visible grid,
            # but after scoping to the exact pcb post the reliable manifest proves
            # only N real photos.  In the current failing "Nice" post:
            #   expected=12, scoped links=6, grid=5, manifest=5, link_items=5.
            # The old fast retry guard then waited for 12 and RETRY forever.
            # Correct only when all exact-pcb scoped signals agree and no large
            # album mode is active.
            try:
                normalized_count_v1242 = len(link_items or [])
                manifest_count_v1242 = len(manifest_ids or [])
                grid_count_v1242 = len(ordered_grid_items or [])
                link_count_v1242 = len(ordered_links or [])
                if (
                    provisional_expected_photo_count
                    and dominant_pcb_key
                    and str(dominant_pcb_key).startswith("pcb:")
                    and not plus_count_before
                    and normalized_count_v1242 >= 2
                    and manifest_count_v1242 == normalized_count_v1242
                    and grid_count_v1242 <= normalized_count_v1242
                    and link_count_v1242 <= normalized_count_v1242 + 1
                    and int(provisional_expected_photo_count) > normalized_count_v1242
                    and not large_album_mode
                ):
                    logger.info(
                        "FB v14.1 pre-final evidence: exact-pcb manifest/grid correction: "
                        f"expected {provisional_expected_photo_count}->{normalized_count_v1242}, "
                        f"links={link_count_v1242}, grid={grid_count_v1242}, "
                        f"manifest={manifest_count_v1242}, pcb={dominant_pcb_key}"
                    )
                    provisional_expected_photo_count = normalized_count_v1242
            except Exception as _e:
                logger.debug(f"FB v12.42 exact-pcb target correction skipped: {_e}")

            # v13.6 authoritative +N target reconciliation.
            #
            # IMPORTANT:
            # The centralized v13.5/v13.6 gallery plan above is the sole count
            # authority.  Older v12.43 logic used:
            #
            #     visible_grid + N
            #
            # which is off by one because Facebook paints "+N" ON the last
            # visible tile.  The correct overlay total is:
            #
            #     (visible_grid - 1) + N
            #
            # This downstream guard therefore may only CONFIRM the target.  It
            # must never re-raise a corrected 7 -> 8 or 80 -> 81.
            try:
                if (
                    provisional_expected_photo_count
                    and plus_count_before
                    and int(plus_count_before) > 0
                    and len(ordered_grid_items or []) >= 2
                    and not explicit_story_single_mode
                    and not explicit_album_single_mode
                ):
                    _grid_v136 = len(ordered_grid_items or [])
                    _plus_v136 = int(plus_count_before)
                    _overlay_total_v136 = max(1, _grid_v136 - 1) + _plus_v136

                    # Exact-PCB/manifest evidence is allowed to prove MORE than
                    # the overlay count, but the overlay itself never raises by
                    # the legacy +1 formula.
                    if int(provisional_expected_photo_count) < _overlay_total_v136:
                        logger.info(
                            "FB v14.1 pre-final evidence: +N reconciliation raises provisional: "
                            f"expected {provisional_expected_photo_count}->{_overlay_total_v136}, "
                            f"grid={_grid_v136}, plus={_plus_v136}"
                        )
                        provisional_expected_photo_count = _overlay_total_v136
                    else:
                        logger.info(
                            "FB v14.1 pre-final evidence: +N reconciliation confirmed: "
                            f"target={provisional_expected_photo_count}, overlay_total={_overlay_total_v136}, "
                            f"grid={_grid_v136}, plus={_plus_v136}"
                        )
            except Exception as _e:
                logger.debug(f"FB v13.6 authoritative +N reconciliation skipped: {_e}")

            # v11.47 scoped ghost-link correction:
            # Some /share/ posts expose one extra set=pcb link that points to the post
            # container rather than a fourth photo.  In the failing 18uZbg4XsQ case,
            # scoped links reported 4 while grid, normalized photo records and manifest
            # all independently proved there are exactly 3 real photos.  Correct only
            # this narrow one-extra-link case; +N and real larger galleries stay strict.
            try:
                normalized_count = len(link_items or [])
                if (
                    provisional_expected_photo_count
                    and not plus_count_before
                    and normalized_count >= 2
                    and int(provisional_expected_photo_count) == normalized_count + 1
                    and len(ordered_links or []) == int(provisional_expected_photo_count)
                    and len(ordered_grid_items or []) <= normalized_count
                    and len(manifest_ids or []) == normalized_count
                    and not large_album_mode
                ):
                    logger.info(
                        "FB v14.1 pre-final evidence: ghost-link correction: "
                        f"expected {provisional_expected_photo_count}->{normalized_count}, "
                        f"ordered_links={len(ordered_links or [])}, "
                        f"grid={len(ordered_grid_items or [])}, "
                        f"normalized={normalized_count}, manifest={len(manifest_ids or [])}"
                    )
                    provisional_expected_photo_count = normalized_count
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
                    provisional_expected_photo_count
                    and int(provisional_expected_photo_count) == 2
                    and not plus_count_before
                    and len(ordered_grid_items or []) <= 1
                    and len(ordered_links or []) <= 2
                    and len(link_items or []) == 1
                    and len(manifest_ids or []) == 1
                    and not large_album_mode
                ):
                    logger.info(
                        "FB v14.1 pre-final evidence: single-photo correction: "
                        f"expected {provisional_expected_photo_count}->1, "
                        f"ordered_links={len(ordered_links or [])}, "
                        f"grid={len(ordered_grid_items or [])}, "
                        f"link_items={len(link_items or [])}, "
                        f"manifest={len(manifest_ids or [])}"
                    )
                    provisional_expected_photo_count = 1
            except Exception as _e:
                logger.debug(f"FB v11.46 duplicate single-photo target correction skipped: {_e}")

            if ordered_links and len(link_items) < max(8, int(len(ordered_links) * 0.7)):
                logger.warning(f"FB photo-link sequence 明顯不足: link_items={len(link_items)} / ordered_links={len(ordered_links)}，代表部分 photo link 開頁後仍回同一張或只給縮圖")

            # v11.15: Determine post media cluster BEFORE opening the viewer, so off-post

            # v14.1 IMMUTABLE GALLERY TARGET FINALIZATION ------------------
            # All legacy evidence corrections above operate on
            # provisional_expected_photo_count only.
            # From this point onward, gallery_target is read-only.
            try:
                _final_grid_v141 = len(ordered_grid_items or [])
                _final_plus_v141 = max(0, int(plus_count_before or 0))

                _final_pcb_id_v141 = ""
                if str(dominant_pcb_key or "").startswith("pcb:"):
                    _final_pcb_id_v141 = str(dominant_pcb_key).split(":", 1)[1]

                _final_scoped_photo_ids_v141 = set()
                for _u in ordered_links or []:
                    _su = str(_u or "")
                    if _final_pcb_id_v141:
                        if not (
                            f"set=pcb.{_final_pcb_id_v141}" in _su
                            or f"set=pcb%2E{_final_pcb_id_v141}" in _su
                        ):
                            continue
                    _m_v141 = re.search(r"[?&]fbid=(\d{8,})", _su, flags=re.I)
                    if _m_v141:
                        _final_scoped_photo_ids_v141.add(_m_v141.group(1))

                _final_manifest_count_v141 = len(manifest_ids or [])
                gallery_plan_v141 = _contract_finalize_gallery_plan(
                    provisional_expected_photo_count,
                    _final_grid_v141,
                    _final_plus_v141,
                    len(_final_scoped_photo_ids_v141),
                    _final_manifest_count_v141,
                    explicit_single=bool(
                        explicit_story_single_mode or explicit_album_single_mode
                    ),
                )
                gallery_target = int(gallery_plan_v141.expected_count or 0)

                logger.info(
                    "FB v14.1 FINAL GalleryPlan locked: "
                    f"target={gallery_target}, "
                    f"grid={gallery_plan_v141.visible_grid_count}, "
                    f"plus={gallery_plan_v141.plus_count}, "
                    f"scoped_ids={gallery_plan_v141.scoped_link_count}, "
                    f"manifest={_final_manifest_count_v141}, "
                    f"confidence={gallery_plan_v141.confidence}, "
                    f"reason={gallery_plan_v141.reason}"
                )
            except Exception as _e:
                logger.error(f"FB v14.1 GalleryPlan finalization failed: {_e}")
                return "RETRY", "Facebook gallery target finalization failed"

            # V14.1 IMMUTABLE TARGET LOCK -----------------------------------
            # recommendations never count toward the immutable gallery_target during harvesting.
            pre_viewer_cluster = _dominant_media_cluster(link_items, min_count=2)
            # v12.27:
            # For share/p gallery posts, FB media IDs in the same pcb post can
            # legitimately span neighboring numeric clusters such as 15159826 /
            # 15159827 / 15159828.  The old cluster guard treated those as
            # off-post pollution and skipped valid slides, causing 5/7 RETRY.
            # Keep the cluster guard for generic contexts, but disable it for
            # already-scoped share/p + pcb gallery mode; final expected-count and
            # manifest/order guards still prevent false SUCCESS.
            if share_p_gallery_like_v1226 and dominant_pcb_key.startswith("pcb:"):
                if pre_viewer_cluster:
                    logger.info(
                        f"FB v12.27 share/p gallery disables numeric media-cluster guard: "
                        f"scope={pre_viewer_cluster}, pcb={dominant_pcb_key}"
                    )
                pre_viewer_cluster = ""
            elif pre_viewer_cluster:
                logger.info(f"FB v11.15 pre-viewer media cluster scope={pre_viewer_cluster}")

            # 多圖完整性補強 v9：
            # 從多個入口啟動 viewer。FB 有時從 +N 入口只能翻 3 張，
            # 但從 photo/?fbid= 或原貼文入口可走到不同區段，所以要合併多段 viewer sequence。
            grid_tile_items = []
            try:
                network_items.clear()

                viewer_sequences = []
                if explicit_story_single_mode:
                    logger.info(
                        "FB v12.18 explicit story single-photo mode: skip post viewer walk "
                        "to avoid album/recommendation pollution"
                    )
                    post_sequence = []
                elif explicit_album_single_mode:
                    logger.info(
                        "FB v12.23 album-context single-photo mode: skip post viewer walk "
                        "to avoid same-album other-date pollution"
                    )
                    post_sequence = []
                else:
                    fast_share_gallery_v1250 = bool(
                        share_p_gallery_like_v1226
                        and plus_count_before
                        and gallery_target
                        and int(gallery_target) <= 20
                        and str(dominant_pcb_key or "").startswith("pcb:")
                    )
                    unique_links_v1250 = len(_dedupe_items_by_media_id(link_items or []))
                    if fast_share_gallery_v1250 and unique_links_v1250 >= int(gallery_target):
                        logger.info(
                            f"FB v12.50 exact-pcb link manifest already complete: "
                            f"{unique_links_v1250}/{gallery_target}; skip post viewer walk"
                        )
                        post_sequence = []
                    else:
                        post_sequence = _collect_viewer_sequence_from_url(
                            context,
                            resolved,
                            label="post",
                            is_photo_page=False,
                            target_count=gallery_target or None,
                            stale_threshold=(3 if fast_share_gallery_v1250 else 4),
                            max_turns=(max(10, min(16, int(gallery_target or 8) + 5)) if fast_share_gallery_v1250 else max(24, (gallery_target or 16) + 10)),
                            allowed_cluster=pre_viewer_cluster or None,
                            fast_mode=fast_share_gallery_v1250,
                        )
                viewer_sequences.append(post_sequence)

                # v12.48: bounded multi-entry recovery for share/p +N galleries.
                #
                # v11.13 intentionally skipped opening individual photo pages in
                # post-scoped mode because generic photo pages can redirect into
                # recommendations/albums.  That is still correct for normal posts.
                # However, for explicit /share/p/ galleries with a proven set=pcb
                # scope and +N overlay, the current post viewer can loop at 7/8
                # while each scoped photo permalink may start the same viewer at a
                # different segment.  Open only the already-scoped pcb photo links,
                # keep the same final strict completeness guard, and never accept
                # off-post links.
                try:
                    if (
                        gallery_target
                        and int(gallery_target) > 1
                        and plus_count_before
                        and share_p_gallery_like_v1226
                        and str(dominant_pcb_key or "").startswith("pcb:")
                    ):
                        current_n_v1248 = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, *viewer_sequences)))
                        if current_n_v1248 < int(gallery_target):
                            pcb_id_v1248 = str(dominant_pcb_key).split(":", 1)[1]
                            scoped_links_v1248 = []
                            for _lnk in ordered_links or []:
                                try:
                                    _s = str(_lnk or "")
                                    if (
                                        _is_true_photo_link(_s)
                                        and (f"set=pcb.{pcb_id_v1248}" in _s or f"set=pcb%2E{pcb_id_v1248}" in _s)
                                    ):
                                        if _s not in scoped_links_v1248:
                                            scoped_links_v1248.append(_s)
                                except Exception:
                                    continue

                            logger.info(
                                f"FB v12.48 share/p multi-entry recovery start: "
                                f"current={current_n_v1248}, expected={gallery_target}, "
                                f"scoped_links={len(scoped_links_v1248)}, pcb={dominant_pcb_key}"
                            )
                            for _idx, _lnk in enumerate(scoped_links_v1248[:2], 1):
                                now_n_v1248 = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, *viewer_sequences)))
                                if now_n_v1248 >= int(gallery_target):
                                    break
                                seq_v1248 = _collect_viewer_sequence_from_url(
                                    context,
                                    _lnk,
                                    label=f"share-p-photo{_idx}",
                                    is_photo_page=True,
                                    target_count=int(gallery_target),
                                    stale_threshold=2,
                                    max_turns=max(6, min(10, int(gallery_target) + 2)),
                                    allowed_cluster=None,
                                    fast_mode=True,
                                )
                                viewer_sequences.append(seq_v1248)
                                new_n_v1248 = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, *viewer_sequences)))
                                logger.info(
                                    f"FB v12.48 share/p multi-entry recovery progress: "
                                    f"entry={_idx}, collected={len(seq_v1248)}, unique={new_n_v1248}/{gallery_target}"
                                )
                except Exception as _e:
                    logger.debug(f"FB v12.48 share/p multi-entry recovery skipped: {_e}")

                # v11.13: In post-scoped mode, never open individual photo pages
                # after post viewer. Logged-in Facebook often redirects those photo pages
                # to feed/recommendation contexts and pollutes the output.
                if gallery_target:
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

                # v13.3: re-parse structured payloads *after* the viewer walk.  The
                # hidden last slide is frequently serialized only after ArrowRight
                # navigation, so parsing the buffer only before the viewer produced
                # deterministic target-1 failures (7/8, 80/81).  Accept only photo
                # permalinks carrying the already-proven exact pcb id.
                try:
                    if (
                        gallery_target
                        and plus_count_before
                        and share_p_gallery_like_v1226
                        and str(dominant_pcb_key or "").startswith("pcb:")
                    ):
                        current_v133 = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                        if current_v133 < int(gallery_target):
                            late_links_all_v133 = _fb_v131_collect_exact_pcb_links_from_payloads(
                                structured_payloads_v131, dominant_pcb_key
                            )
                            known_fbid_v133 = set()
                            for _known in ordered_links or []:
                                _m = re.search(r"[?&]fbid=(\d{8,})", str(_known or ""), flags=re.I)
                                if _m:
                                    known_fbid_v133.add(_m.group(1))
                            late_links_v133 = []
                            for _u in late_links_all_v133:
                                _m = re.search(r"[?&]fbid=(\d{8,})", str(_u or ""), flags=re.I)
                                _fid = _m.group(1) if _m else ""
                                if _fid and _fid not in known_fbid_v133:
                                    known_fbid_v133.add(_fid)
                                    late_links_v133.append(_u)
                                    ordered_links.append(_u)
                            if late_links_v133:
                                missing_v133 = max(1, int(gallery_target) - current_v133)
                                logger.info(
                                    f"FB v13.3 late exact-pcb payload reconciliation: "
                                    f"new_links={len(late_links_v133)}, current={current_v133}/{gallery_target}, "
                                    f"payloads={len(structured_payloads_v131)}"
                                )
                                # Open only the newly-proven exact links, bounded by the
                                # number still missing plus two alternates.
                                late_items_v133 = _build_photo_items_from_links(
                                    page,
                                    late_links_v133[: missing_v133 + 2],
                                    network_items=[],
                                    grid_items=[],
                                ) or []
                                if late_items_v133:
                                    link_items = _aggregate_unique_items(link_items, late_items_v133)
                                    merged_v133 = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                                    logger.info(
                                        f"FB v13.3 late exact-pcb items merged: "
                                        f"items={len(late_items_v133)}, unique={merged_v133}/{gallery_target}"
                                    )
                except Exception as _e:
                    logger.debug(f"FB v13.3 late payload reconciliation skipped: {_e}")

                # v12.51: only when an exact share/p +N gallery is missing exactly
                # one media item, run one bounded high-effort tail pass.  This is
                # deliberately narrow so normal galleries keep the fast v12.50 path.
                try:
                    if (
                        gallery_target
                        and share_p_gallery_like_v1226
                        and plus_count_before
                        and str(dominant_pcb_key or "").startswith("pcb:")
                    ):
                        current_unique_v1251 = _dedupe_items_by_media_id(
                            _aggregate_unique_items(link_items, viewer_items)
                        )
                        if len(current_unique_v1251) == int(gallery_target) - 1:
                            pcb_id_v1251 = str(dominant_pcb_key).split(":", 1)[1]
                            exact_links_v1251 = [
                                _u for _u in (ordered_links or [])
                                if _is_true_photo_link(str(_u or ""))
                                and (
                                    f"set=pcb.{pcb_id_v1251}" in str(_u)
                                    or f"set=pcb%2E{pcb_id_v1251}" in str(_u)
                                )
                            ]
                            recovered_v1251 = _fb_v1251_near_complete_tail_recovery(
                                context,
                                exact_links_v1251,
                                current_unique_v1251,
                                int(gallery_target),
                                dominant_pcb_key,
                            )
                            viewer_items = _aggregate_unique_items(viewer_items, recovered_v1251)
                            logger.info(
                                f"FB v12.51 tail recovery merged: "
                                f"unique={len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))}/"
                                f"{gallery_target}"
                            )
                except Exception as _e:
                    logger.debug(f"FB v12.51 near-complete tail recovery skipped: {_e}")

                # v11.22.1 Fast Retry Guard:
                # The old v11.21 slow recovery can run for many minutes and may keep logging after
                # the worker has timed out. If the primary Theater pass is short, immediately ask
                # the worker for a clean browser-context retry instead of looping stale frames.
                if gallery_target:
                    try:
                        before_retry_n = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                    except Exception:
                        before_retry_n = len(_aggregate_unique_items(link_items, viewer_items))

                    if (
                        plus_count_before
                        and int(gallery_target) >= 8
                        and before_retry_n == int(gallery_target) - 1
                    ):
                        viewer_items = _recover_full_gallery_near_complete_v1225(
                            context,
                            page,
                            ordered_links=ordered_links,
                            ordered_grid_items=ordered_grid_items,
                            link_items=link_items,
                            viewer_items=viewer_items,
                            expected_photo_count=int(gallery_target),
                            title=title,
                            url=url,
                            resolved=resolved,
                            dominant_pcb_key=dominant_pcb_key,
                        )
                        try:
                            before_retry_n = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                        except Exception:
                            before_retry_n = len(_aggregate_unique_items(link_items, viewer_items))

                    if before_retry_n < gallery_target:
                        if plus_count_before and int(gallery_target) >= 6:
                            viewer_items = _fb_v1232_pack_dicts(_fb_v1229_append_capture_items_before_guard(
                                viewer_items,
                                expected_photo_count=int(gallery_target),
                            ))
                            try:
                                before_retry_n = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                            except Exception:
                                before_retry_n = len(_aggregate_unique_items(link_items, viewer_items))

                        if before_retry_n < gallery_target:
                            logger.info(
                                f"FB v12.46 defer fast retry until grid tile recovery: "
                                f"incomplete={before_retry_n}/target={gallery_target}; "
                                "allow grid-tile mode/final completeness guard before returning RETRY"
                            )
                            # Do not return here.  The v11.22 grid tile mode below can still
                            # recover the hidden +N tile image by physical grid order.  If it
                            # cannot prove the missing item, the final strict completeness guard
                            # will return RETRY without moving partial/duplicate outputs.

            except Exception as e:
                logger.warning(f"FB viewer fallback 失敗: {e}")

            # v11.22 Grid Tile Mode: if Theater Viewer still misses one or more tiles,
            # extract by physical grid order from the +N dialog/current page.
            if gallery_target:
                try:
                    current_unique_n = len(_dedupe_items_by_media_id(_aggregate_unique_items(link_items, viewer_items)))
                except Exception:
                    current_unique_n = len(_aggregate_unique_items(link_items, viewer_items))
                if current_unique_n < gallery_target:
                    logger.info(
                        f"FB v11.22 grid tile mode trigger: unique={current_unique_n}/target={gallery_target}"
                    )
                    grid_tile_items = _collect_grid_tile_mode_items(
                        context,
                        page,
                        pcb_key=dominant_pcb_key,
                        expected_count=gallery_target,
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

            for pack in _fb_v1232_pack_dicts(link_items):
                key = _media_key_from_src(pack.get("src", ""))
                if key and key in used_keys:
                    continue
                if key:
                    used_keys.add(key)
                final_items.append(pack)

            if explicit_story_single_mode:
                logger.info(
                    "FB v12.18 explicit story single-photo mode: skip appending viewer items"
                )
            elif explicit_album_single_mode:
                logger.info(
                    "FB v12.23 album-context single-photo mode: skip appending viewer items"
                )
            else:
                for pack in _fb_v1232_pack_dicts(viewer_items):
                    key = _media_key_from_src(pack.get("src", ""))
                    if key and key in used_keys:
                        continue
                    if key:
                        used_keys.add(key)
                    final_items.append(pack)

            # 如果 viewer 本身比合併結果更完整，代表它是從第一張完整跑完，直接採用 viewer。
            viewer_items = _fb_v1232_pack_dicts(viewer_items)
            link_items = _fb_v1232_pack_dicts(link_items)
            if len(viewer_items) >= max(len(final_items), len(link_items) + 4):
                final_items = viewer_items

            # v11.22: when grid tile mode found a full/near-full ordered set, use its physical order.
            # This is the only reliable way to solve the missing visual 10.jpg and 6/7 swaps.
            try:
                if gallery_target and grid_tile_items and len(_dedupe_items_by_media_id(grid_tile_items)) >= gallery_target - 1:
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
            viewer_complete_incomplete_manifest_mode = False
            if (
                plus_count_before
                and gallery_target
                and len(manifest_ids_for_filter) < int(gallery_target)
            ):
                logger.info(
                    f"FB v11.96 +N full-gallery mode: keep viewer items beyond manifest "
                    f"manifest={len(manifest_ids_for_filter)}, expected={gallery_target}"
                )
                manifest_ids_for_filter = []
                viewer_complete_incomplete_manifest_mode = True

            # v12.22:
            # Some share/p pages expose only one manifest/link item but the post
            # viewer itself successfully walks the full expected set.  The old
            # manifest filter shrank 6 proven viewer items down to 1 and caused
            # RETRY.  If the viewer already collected enough items and the
            # manifest is incomplete, preserve the viewer sequence instead of
            # applying manifest/cluster narrowing.
            if (
                gallery_target
                and len(viewer_items) >= int(gallery_target)
                and len(manifest_ids_for_filter) > 0
                and len(manifest_ids_for_filter) < int(gallery_target)
                and len(link_items) < int(gallery_target)
            ):
                logger.info(
                    f"FB v12.22 viewer-complete incomplete-manifest mode: "
                    f"viewer={len(viewer_items)}, manifest={len(manifest_ids_for_filter)}, "
                    f"link_items={len(link_items)}, expected={gallery_target}; "
                    "skip manifest/cluster narrowing"
                )
                manifest_ids_for_filter = []
                dominant_cluster = ""
                viewer_complete_incomplete_manifest_mode = True

            if (dominant_cluster or manifest_ids_for_filter) and not viewer_complete_incomplete_manifest_mode:
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
            if gallery_target:
                before_photo_clean_n = len(viewer_items)
                viewer_items = _drop_video_packs_for_photo_post(viewer_items)
                if len(viewer_items) != before_photo_clean_n:
                    logger.info(
                        f"FB v11.16 photo-only cleanup: items {before_photo_clean_n}->{len(viewer_items)}"
                    )

                if (
                    gallery_target
                    and 'manifest_ids_for_filter' in locals()
                    and not manifest_ids_for_filter
                    and (
                        plus_count_before
                        or ('viewer_complete_incomplete_manifest_mode' in locals() and viewer_complete_incomplete_manifest_mode)
                    )
                ):
                    logger.info(
                        "FB v12.22 full-gallery viewer-complete mode: preserve viewer sequence order; "
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

            if gallery_target and len(viewer_items) > gallery_target:
                logger.warning(
                    f"FB bounded scope: candidate count={len(viewer_items)} > expected={gallery_target}，"
                    "裁切到目標張數，避免側邊欄/推薦貼文混入"
                )
                viewer_items = viewer_items[:gallery_target]

            if explicit_album_single_mode:
                forced_single = _force_single_photo_items_v1223(link_items, viewer_items)
                if forced_single:
                    logger.info(
                        f"FB v12.23 album-context single-photo final forced: "
                        f"{len(viewer_items)}->1"
                    )
                    viewer_items = forced_single
                else:
                    logger.warning("FB v12.23 album-context single-photo has no proven item; RETRY")
                    clear_temp()
                    return "RETRY", "Facebook album-context single photo identity not proven"

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

            if explicit_story_single_mode and len(viewer_items) != 1:
                logger.warning(
                    f"FB v12.17 explicit story rejected unexpected final item count: "
                    f"{len(viewer_items)}"
                )
                clear_temp()
                return "RETRY", "Facebook explicit story expected exactly one proven target photo"

            if explicit_album_single_mode and len(viewer_items) != 1:
                logger.warning(
                    f"FB v12.23 album-context rejected unexpected final item count: "
                    f"{len(viewer_items)}"
                )
                clear_temp()
                return "RETRY", "Facebook album-context expected exactly one proven photo"

            if (
                gallery_target
                and int(gallery_target) > 1
                and len(viewer_items) >= int(gallery_target)
                and (
                    plus_count_before
                    or ('viewer_complete_incomplete_manifest_mode' in locals() and viewer_complete_incomplete_manifest_mode)
                    or (
                        str(dominant_pcb_key or "").startswith("pcb:")
                        and len(_fb_v1232_pack_dicts(link_items or [])) >= int(gallery_target)
                    )
                    or (
                        str(dominant_pcb_key or "").startswith("pcb:")
                        and len(_fb_v1232_pack_dicts(viewer_items or [])) >= int(gallery_target)
                    )
                )
            ):
                logger.info(
                    f"FB v12.36 exact-gallery best-available source mode enabled: "
                    f"items={len(viewer_items)}, expected={gallery_target}; "
                    "high-res variants are still tried first"
                )
                for _pack in _fb_v1232_pack_dicts(viewer_items):
                    try:
                        _pack["_allow_fb_best_available_source"] = True
                        for _cand in (_pack.get("candidates") or []):
                            if isinstance(_cand, dict):
                                _cand["_allow_fb_best_available_source"] = True
                            elif isinstance(_cand, str):
                                pass
                    except Exception:
                        pass

            if not viewer_items:
                return "FAILED", "Facebook Playwright 頁面已開啟，但未抓到有效貼文主媒體"

            success_count, ordered_output_files = _download_viewer_items(
                context,
                viewer_items,
                referer=resolved,
            )

            if (
                gallery_target
                and success_count < int(gallery_target)
                and int(gallery_target) > 1
                and (
                    plus_count_before
                    or str(dominant_pcb_key or "").startswith("pcb:")
                    or len(viewer_items or []) >= int(gallery_target)
                )
            ):
                success_count, ordered_output_files = _fb_v1228_fill_outputs_from_captures(
                    ordered_output_files,
                    success_count=success_count,
                    expected_photo_count=int(gallery_target),
                )

            if success_count <= 0:
                if gallery_target and gallery_target > 1:
                    return "RETRY", (
                        "Facebook gallery candidates were collected but all failed "
                        "resolution/download validation; retry fresh context"
                    )
                return "FAILED", "Facebook Playwright 有抓到媒體 URL，但全部下載失敗"

            # v13.0 Centralized gallery contract.  This preserves the legacy strict
            # behavior, but the decision now lives in one pure/tested contract instead
            # of being reimplemented by successive patches.
            _gallery_audit = _contract_audit_gallery_completion(
                expected_count=gallery_target,
                unique_output_count=success_count,
            )
            if not _gallery_audit.complete:
                logger.warning(
                    f"FB v14.1 strict completeness guard: "
                    f"output={_gallery_audit.unique_output_count}/target={_gallery_audit.expected_count}; "
                    f"reason={_gallery_audit.reason}; return RETRY and block yt-dlp fallback"
                )
                clear_temp()
                return "RETRY", (
                    f"Facebook gallery incomplete {_gallery_audit.unique_output_count}/"
                    f"{_gallery_audit.expected_count}; retry fresh context"
                )

            if gallery_target and success_count >= gallery_target and ordered_output_files:
                logger.info(
                    f"FB v12.02 ordered move exact-count completion: "
                    f"outputs={len(ordered_output_files)}, expected={gallery_target}"
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


def _download_via_ytdlp(url: str):
    clear_temp()
    resolved = _resolve_share_url(url)

    ffmpeg_path = _find_ffmpeg()

    if ffmpeg_path:
        formats = [
            "bestvideo+bestaudio/best",
            "best",
        ]
    else:
        logger.warning("未找到 ffmpeg，FB yt-dlp 將使用單檔格式，避免合併失敗")

        formats = [
            "best[ext=mp4]/best[protocol^=http]/best",
            "mp4/best",
            "best",
        ]

    variants = []

    if os.path.exists(COOKIES_FILE):
        variants.append({
            "cookiefile": os.path.abspath(COOKIES_FILE),
        })

    variants.append({})

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

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(
                        resolved,
                        download=True,
                    )

                    fallback_title = (
                        _fb_reel_fallback_title(resolved)
                        if (_is_fb_reel_url(url) or _is_fb_reel_url(resolved))
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

        explicit_video_url = is_reel_like_url or any(x in original_low or x in resolved_low for x in [
            "/watch",
            "/videos/",
            "video.php",
            "/reel",
            "/reels/",
            "fb.watch",
        ])

        if explicit_video_url and not force_playwright_first:
            # Exact-scope Reel safety: Playwright sees the active visible Reel and
            # canonical identity. yt-dlp on share/v or Reel pages can resolve/preload
            # a sibling video and return a false SUCCESS, so it is not used first.
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

    # v12.37:
    # The worker itself already runs downloads off the GUI thread.  The old
    # outer daemon timeout could return while Playwright was still harvesting a
    # large gallery, leaving the persistent FB profile in use and causing every
    # following FB task to fail at launch with "Target page/context/browser has
    # been closed".  Run the task synchronously under a module lock so a large
    # gallery either completes or fails cleanly before the next FB task starts.
    with _FB_DOWNLOAD_LOCK:
        try:
            _run()
        except Exception as e:
            logger.exception(f"FB v12.37 uncaught download error: {e}")
            result_box[0] = _classify_error(str(e))

    result = result_box[0] or ("FAILED", "未知錯誤")
    status, reason = result
    if status != "SUCCESS":
        _clear_temp_after_terminal_failure(status, reason)
    return result
