import glob
import os
import re
from typing import Optional
from urllib.parse import urlparse, urlunparse


EXCLUDE_FILES = {
    "download_link.txt",
    "undownload_link.txt",
    "ig_links.txt",
    "fb_links.txt",
}


IG_PROFILE_RESERVED_PATHS = {
    "p",
    "reel",
    "reels",
    "tv",
    "stories",
    "explore",
    "accounts",
    "direct",
    "developer",
    "about",
    "legal",
    "terms",
    "privacy",
    "directory",
    "web",
    "graphql",
    "api",
    "challenge",
    "oauth",
    "emails",
    "settings",
}


def get_input_file(base_dir: str = ".", preferred_file: Optional[str] = None) -> Optional[str]:
    if preferred_file:
        if os.path.isabs(preferred_file):
            if os.path.exists(preferred_file):
                return preferred_file
        else:
            candidate = os.path.join(base_dir, preferred_file)
            if os.path.exists(candidate):
                return candidate

    txt_files = glob.glob(os.path.join(base_dir, "*.txt"))
    available_files = [
        f for f in txt_files
        if os.path.basename(f) not in EXCLUDE_FILES
    ]

    if not available_files:
        return None

    if len(available_files) == 1:
        return available_files[0]

    available_files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return available_files[0]


def extract_urls(content: str) -> list[str]:
    # Keep query strings such as ?igsh=..., but strip common trailing punctuation
    # produced by copied text / chat messages.
    urls = re.findall(r'https?://[^\s,，。]+', content)
    cleaned = []
    for u in urls:
        u = (u or "").strip()
        u = u.strip(" \t\r\n")
        u = u.rstrip("，。,.；;、")
        u = u.rstrip(")]}）】》")
        if u:
            cleaned.append(u)
    return list(dict.fromkeys(cleaned))


def _instagram_host(host: str) -> bool:
    host = (host or "").lower()
    return host in {"instagram.com", "www.instagram.com"}


def _is_instagram_profile_path(path: str) -> bool:
    parts = [p for p in (path or "").split("/") if p]
    if not parts:
        return False

    username = parts[0].strip()
    if not username:
        return False

    if username.lower() in IG_PROFILE_RESERVED_PATHS:
        return False

    # Supported profile inputs:
    # - /<username>
    # - /<username>/
    # - /<username>/reels/
    # - /<username>/tagged/
    if len(parts) >= 2:
        tab = (parts[1] or "").lower()
        if tab not in {"reels", "tagged"}:
            return False
        if len(parts) > 2:
            return False

    # Instagram username: letters, numbers, underscore, dot, max 30 chars.
    return bool(re.fullmatch(r"[A-Za-z0-9._]{1,30}", username))


def _normalize_instagram_url(url: str) -> str:
    """
    Normalize Instagram URLs for downloader input.

    Important for v11.36:
    - https://www.instagram.com/duolastudy?igsh=...
      becomes https://www.instagram.com/duolastudy/
    - /<username>/reels/ and /<username>/tagged/ keep their tab path.
    - Single /p/ and /reel/ URLs keep their path and query, so img_index=...
      and other post-level routing data are not lost.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    if not _instagram_host(parsed.netloc):
        return url

    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return url

    first = parts[0].lower()

    # Preserve single post / reel URLs.  Query can be meaningful for img_index.
    if first in {"p", "reel", "reels", "tv"}:
        return url

    if not _is_instagram_profile_path(parsed.path):
        return url

    username = parts[0]
    if len(parts) >= 2 and parts[1].lower() in {"reels", "tagged"}:
        norm_path = f"/{username}/{parts[1].lower()}/"
    else:
        norm_path = f"/{username}/"

    # Profile ?igsh=... is only share tracking. Drop it so processed_links and
    # queue dedupe use the canonical profile URL.
    return urlunparse(("https", "www.instagram.com", norm_path, "", "", ""))


def normalize_download_url(url: str) -> str:
    """Return the canonical URL to write into download_link.txt."""
    if "instagram.com" in (url or "").lower():
        return _normalize_instagram_url(url)
    return url


def is_instagram_media(url: str) -> bool:
    u = (url or "").lower()

    if "instagram.com" not in u:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if not _instagram_host(parsed.netloc):
        return False

    path = parsed.path or ""
    low_path = path.lower()

    # Existing media URL support.
    if (
        "/p/" in low_path or
        "/reel/" in low_path or
        "/reels/" in low_path or
        "/tv/" in low_path
    ):
        return True

    # v11.36: IG profile / Reels / tagged pages are valid downloader inputs
    # because worker expands them into child /p/ and /reel/ tasks.
    return _is_instagram_profile_path(path)


def is_facebook_media(url: str) -> bool:
    u = url.lower()

    is_fb_domain = (
        "facebook.com" in u or
        "fb.watch" in u or
        "m.facebook.com" in u or
        "www.facebook.com" in u
    )
    if not is_fb_domain:
        return False

    fb_patterns = [
        "/share/",
        "/share/r/",
        "/share/v/",
        "/watch/",
        "/watch?",
        "/reel/",
        "/videos/",
        "/posts/",
        "/story.php",
        "fb.watch/",
        "v=",
        "fbid=",
    ]

    return any(p in u for p in fb_patterns)


def sort_links(
    input_file: Optional[str] = None,
    base_dir: str = ".",
    output_dir: Optional[str] = None,
):
    input_path = get_input_file(base_dir=base_dir, preferred_file=input_file)
    if not input_path:
        raise FileNotFoundError("目錄下找不到任何可處理的 .txt 檔案。")

    if output_dir is None:
        output_dir = os.path.join(base_dir, "output")

    os.makedirs(output_dir, exist_ok=True)

    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read()

    urls = extract_urls(content)

    download_links = []
    undownload_links = []

    for url in urls:
        if is_instagram_media(url) or is_facebook_media(url):
            download_links.append(normalize_download_url(url))
        else:
            undownload_links.append(url)

    # Re-dedupe after normalization so:
    # https://www.instagram.com/user?igsh=...
    # https://www.instagram.com/user/
    # are not written twice.
    download_links = list(dict.fromkeys(download_links))
    undownload_links = list(dict.fromkeys(undownload_links))

    download_path = os.path.join(output_dir, "download_link.txt")
    undownload_path = os.path.join(output_dir, "undownload_link.txt")

    with open(download_path, "w", encoding="utf-8") as f:
        f.write("\n".join(download_links))

    with open(undownload_path, "w", encoding="utf-8") as f:
        f.write("\n".join(undownload_links))

    stats = {
        "total_urls": len(urls),
        "downloadable": len(download_links),
        "undownloadable": len(undownload_links),
    }

    return input_path, download_path, undownload_path, stats


def main():
    try:
        input_file, download_path, undownload_path, stats = sort_links()
        print("-" * 45)
        print(f"處理檔案: {input_file}")
        print("結果已儲存：")
        print(f"✅ 可下載: {download_path} ({stats['downloadable']} 筆)")
        print(f"❌ 不可下載: {undownload_path} ({stats['undownloadable']} 筆)")
        print("-" * 45)
    except Exception as e:
        print(f"執行失敗: {e}")


if __name__ == "__main__":
    main()
