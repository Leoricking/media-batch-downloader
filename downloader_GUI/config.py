import os
import sys


def get_base_dir() -> str:
    """
    唯讀資源目錄：
    - 原始碼模式：downloader_v5/
    - PyInstaller：_MEIPASS
    """
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def get_runtime_dir() -> str:
    """
    可寫入目錄：
    - 原始碼模式：downloader_v5/
    - PyInstaller：exe 所在目錄
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_RES = get_base_dir()
_RT = get_runtime_dir()

ACCOUNTS_FILE = os.path.join(_RES, "accounts.json")
COOKIES_FILE = os.path.join(_RES, "cookies.txt")

DOWNLOAD_DIR = os.path.join(_RT, "downloads")
TEMP_DIR = os.path.join(_RT, "temp_insta_dl")
DATA_DIR = os.path.join(_RT, "data")

CHECKPOINT_FILE = os.path.join(DATA_DIR, "processed_links.log")
FAILED_LOG_FILE = os.path.join(DATA_DIR, "failed_links.log")
RETRY_NEEDED_FILE = os.path.join(DATA_DIR, "retry_needed.txt")
UNAVAILABLE_FILE = os.path.join(DATA_DIR, "unavailable_links.txt")

PROJECT_ROOT = os.path.dirname(_RT) if os.path.basename(_RT).lower() == "downloader_v5" else _RT
PREPROCESS_DIR = os.path.join(PROJECT_ROOT, "pre-processing")
PREPROCESS_OUTPUT_DIR = os.path.join(PREPROCESS_DIR, "output")
PREPROCESS_DEFAULT_DOWNLOAD = os.path.join(PREPROCESS_OUTPUT_DIR, "download_link.txt")
PREPROCESS_DEFAULT_UNDOWNLOAD = os.path.join(PREPROCESS_OUTPUT_DIR, "undownload_link.txt")

MAX_WORKERS = 1
# 想維持原本： 1.jpg / 2.jpg / 3.jpg, 就在 config.py
FB_FILENAME_WITH_TITLE = False

# Facebook Playwright settings
# False = 顯示瀏覽器視窗；True = 背景執行
FB_HEADLESS = False

# True = 額外保存 FB harvest/debug 圖，方便檢查是否有抓到但未搬出
FB_DEBUG_CAPTURE = True

os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(PREPROCESS_OUTPUT_DIR, exist_ok=True)