import os
import sys


def get_base_dir() -> str:
    """
    唯讀資源目錄：
    - 原始碼模式：downloader_GUI/
    - PyInstaller：_MEIPASS
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_runtime_dir() -> str:
    """
    可寫入目錄：
    - 原始碼模式：downloader_GUI/
    - PyInstaller：exe 所在目錄
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def find_project_root(runtime_dir: str) -> str:
    """
    尋找專案根目錄。

    支援：
    - media-batch-downloader/downloader_GUI/
    - media-batch-downloader/downloader_v5/
    - media-batch-downloader/downloder/
    - PyInstaller exe 放在 downloader_GUI/
    - 從不同工作目錄啟動
    """
    cur = os.path.abspath(runtime_dir)

    # 先從 runtime_dir 往上找 pre-processing/link_sorter.py
    for _ in range(6):
        candidate = os.path.join(cur, "pre-processing", "link_sorter.py")
        if os.path.exists(candidate):
            return cur

        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent

    # fallback：常見資料夾名稱
    base_name = os.path.basename(os.path.abspath(runtime_dir)).lower()
    if base_name in {"downloader_gui", "downloader_v5", "downloder"}:
        return os.path.dirname(os.path.abspath(runtime_dir))

    return os.path.abspath(runtime_dir)


_RES = get_base_dir()
_RT = get_runtime_dir()
PROJECT_ROOT = find_project_root(_RT)

ACCOUNTS_FILE = os.path.join(_RES, "accounts.json")
COOKIES_FILE = os.path.join(_RES, "cookies.txt")

DOWNLOAD_DIR = os.path.join(_RT, "downloads")
TEMP_DIR = os.path.join(_RT, "temp_insta_dl")
DATA_DIR = os.path.join(_RT, "data")

# Parser dedicated persistent browser profiles.
# These are the preferred login/trust-state storage locations for Playwright / Chrome fallback.
# cookies.txt is kept only as a legacy emergency fallback.
IG_PARSER_PROFILE_DIR = os.path.join(DATA_DIR, "chrome_ig_parser")
FB_PARSER_PROFILE_DIR = os.path.join(DATA_DIR, "playwright_fb_profile")

# Backward-compatible aliases used by older downloader modules / environment overrides.
IG_CHROME_USER_DATA_DIR = IG_PARSER_PROFILE_DIR
FB_CHROME_USER_DATA_DIR = FB_PARSER_PROFILE_DIR

# Instagram profile auto-expand scanning settings.
# IG_PROFILE_SCAN_MAX_POSTS = 0 means no artificial limit; scan until stable/no new posts or max scroll safety limit.
IG_PROFILE_SCAN_MAX_POSTS = 0
IG_PROFILE_SCAN_MAX_SCROLLS = 250
IG_PROFILE_SCAN_SCROLL_WAIT_MS = 1200
IG_PROFILE_SCAN_STABLE_ROUNDS = 6


CHECKPOINT_FILE = os.path.join(DATA_DIR, "processed_links.log")
FAILED_LOG_FILE = os.path.join(DATA_DIR, "failed_links.log")
RETRY_NEEDED_FILE = os.path.join(DATA_DIR, "retry_needed.txt")
UNAVAILABLE_FILE = os.path.join(DATA_DIR, "unavailable_links.txt")

PREPROCESS_DIR = os.path.join(PROJECT_ROOT, "pre-processing")
PREPROCESS_OUTPUT_DIR = os.path.join(PREPROCESS_DIR, "output")
PREPROCESS_DEFAULT_DOWNLOAD = os.path.join(PREPROCESS_OUTPUT_DIR, "download_link.txt")
PREPROCESS_DEFAULT_UNDOWNLOAD = os.path.join(PREPROCESS_OUTPUT_DIR, "undownload_link.txt")

MAX_WORKERS = 1
FB_FILENAME_WITH_TITLE = False

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IG_PARSER_PROFILE_DIR, exist_ok=True)
os.makedirs(FB_PARSER_PROFILE_DIR, exist_ok=True)
os.makedirs(PREPROCESS_OUTPUT_DIR, exist_ok=True)