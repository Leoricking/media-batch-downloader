import os
import threading
from datetime import datetime

from config import (
    CHECKPOINT_FILE,
    DATA_DIR,
    FAILED_LOG_FILE,
    RETRY_NEEDED_FILE,
    UNAVAILABLE_FILE,
)

tasks: list[dict] = []
_lock = threading.Lock()

_runtime = {
    "phase": "IDLE",              # IDLE / DOWNLOADING / COOLDOWN / PAUSED / STOPPED
    "message": "就緒",
    "active_url": "",
    "cooldown_remaining": 0,
    "total": 0,
    "done": 0,
}

_processed_cache: set[str] = set()


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_checkpoint() -> set[str]:
    """
    讀取已成功下載過的 URL。
    """
    global _processed_cache
    _ensure_data_dir()

    if not os.path.exists(CHECKPOINT_FILE):
        _processed_cache = set()
        return _processed_cache

    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        _processed_cache = {line.strip() for line in f if line.strip()}

    return _processed_cache


def rewrite_checkpoint():
    """
    以目前 _processed_cache 全量重寫 processed_links.log
    """
    _ensure_data_dir()
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        for url in sorted(_processed_cache):
            f.write(url + "\n")


def clear_checkpoint():
    """
    清空 processed_links.log 與記憶體快取。
    """
    global _processed_cache
    _ensure_data_dir()
    _processed_cache = set()

    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        f.write("")

    return True


def save_checkpoint(url: str):
    """
    標記此 URL 已成功下載。
    """
    global _processed_cache
    if not _processed_cache:
        load_checkpoint()

    if url in _processed_cache:
        return

    _processed_cache.add(url)
    rewrite_checkpoint()


def remove_from_checkpoint(url: str):
    """
    若某 URL 被誤判成功，可從 processed_links.log 移除。
    """
    global _processed_cache
    if not _processed_cache:
        load_checkpoint()

    if url in _processed_cache:
        _processed_cache.remove(url)
        rewrite_checkpoint()


def get_processed_links() -> set[str]:
    if not _processed_cache:
        load_checkpoint()
    return set(_processed_cache)


def get_processed_count() -> int:
    if not _processed_cache:
        load_checkpoint()
    return len(_processed_cache)


def add_tasks(urls: list[str]) -> dict:
    """
    回傳：
    {
        "added": int,
        "skipped_processed": int,
        "skipped_duplicate": int,
    }
    """
    global _processed_cache
    if not _processed_cache:
        load_checkpoint()

    with _lock:
        existing = {t["url"] for t in tasks}
        added = 0
        skipped_processed = 0
        skipped_duplicate = 0

        for url in urls:
            url = url.strip()
            if not url:
                continue

            if url in _processed_cache:
                skipped_processed += 1
                continue

            if url in existing:
                skipped_duplicate += 1
                continue

            tasks.append({
                "url": url,
                "status": "PENDING",
                "retry": 0,
                "error": "",
            })
            existing.add(url)
            added += 1

        _update_runtime_counts_locked()

        return {
            "added": added,
            "skipped_processed": skipped_processed,
            "skipped_duplicate": skipped_duplicate,
        }


def get_task():
    with _lock:
        for t in tasks:
            if t["status"] == "PENDING":
                t["status"] = "DOWNLOADING"
                _update_runtime_counts_locked()
                return t
    return None


def map_status(error: str) -> str:
    e = (error or "").lower()

    if any(k in e for k in [
        "please wait a few minutes",
        "rate limit",
        "too many requests",
        "temporarily blocked",
        "try again later",
        "429",
        "timeout",
        "timed out",
    ]):
        return "RETRY"

    if any(k in e for k in [
        "403",
        "login",
        "age",
        "restricted",
        "only available to",
        "not available to everyone",
        "sign in",
        "private",
        "特定受眾無法查看此內容",
        "此內容並未開放所有人查看",
        "only available for registered users",
    ]):
        return "BLOCKED"

    if any(k in e for k in [
        "not found",
        "404",
        "unavailable",
        "deleted",
        "this content isn't available",
        "page not found",
        "內容不存在",
    ]):
        return "UNAVAILABLE"

    return "FAILED"


def retry_failed():
    with _lock:
        for t in tasks:
            if t["status"] in ("FAILED", "BLOCKED", "RETRY", "UNAVAILABLE"):
                t["status"] = "PENDING"
                t["retry"] += 1
                t["error"] = ""
        _update_runtime_counts_locked()

    rewrite_status_files()


def clear_tasks():
    with _lock:
        tasks.clear()
        _runtime["phase"] = "IDLE"
        _runtime["message"] = "就緒"
        _runtime["active_url"] = ""
        _runtime["cooldown_remaining"] = 0
        _runtime["total"] = 0
        _runtime["done"] = 0

    rewrite_status_files()


def get_snapshot() -> list[dict]:
    with _lock:
        return [dict(t) for t in tasks]


def update_runtime(
    *,
    phase: str | None = None,
    message: str | None = None,
    active_url: str | None = None,
    cooldown_remaining: int | None = None,
):
    with _lock:
        if phase is not None:
            _runtime["phase"] = phase
        if message is not None:
            _runtime["message"] = message
        if active_url is not None:
            _runtime["active_url"] = active_url
        if cooldown_remaining is not None:
            _runtime["cooldown_remaining"] = cooldown_remaining
        _update_runtime_counts_locked()


def get_runtime() -> dict:
    with _lock:
        return dict(_runtime)


def _update_runtime_counts_locked():
    _runtime["total"] = len(tasks)
    _runtime["done"] = sum(
        1 for t in tasks
        if t["status"] in ("SUCCESS", "FAILED", "BLOCKED", "UNAVAILABLE")
    )


def set_task_result(url: str, status: str, error: str = ""):
    """
    統一更新任務結果：
    - SUCCESS -> 寫入 processed checkpoint
    - 非 SUCCESS -> 從 checkpoint 移除（避免誤判成功後被跳過）
    - 每次更新後重寫 failed / retry / unavailable 檔案
    """
    found = False

    with _lock:
        for t in tasks:
            if t["url"] == url:
                t["status"] = status
                t["error"] = error or ""
                found = True
                break
        _update_runtime_counts_locked()

    if not found:
        return

    if status == "SUCCESS":
        save_checkpoint(url)
    else:
        remove_from_checkpoint(url)

    rewrite_status_files()


def append_failed_event(url: str, reason: str):
    """
    保留歷史事件流水帳。
    這個檔案是 append 模式，用來追蹤所有失敗事件。
    """
    _ensure_data_dir()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    history_file = os.path.join(DATA_DIR, "failed_history.log")
    with open(history_file, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {url}\n")
        f.write(f"原因: {reason or '未知錯誤'}\n\n")


def rewrite_status_files():
    """
    依『目前任務快照』重寫：
    - failed_links.log
    - retry_needed.txt
    - unavailable_links.txt

    這樣成功後就會自動從這三個檔案消失，不殘留舊狀態。
    """
    _ensure_data_dir()

    snapshot = get_snapshot()

    failed_items = [t for t in snapshot if t["status"] == "FAILED"]
    retry_items = [t for t in snapshot if t["status"] == "RETRY"]
    unavailable_items = [t for t in snapshot if t["status"] in ("BLOCKED", "UNAVAILABLE")]

    with open(FAILED_LOG_FILE, "w", encoding="utf-8") as f:
        if failed_items:
            f.write("=== CURRENT FAILED TASKS ===\n\n")
            for i, t in enumerate(failed_items, 1):
                f.write(f"[{i}] FAILED\n")
                f.write(f"{t['url']}\n")
                f.write(f"原因: {t.get('error') or '未知錯誤'}\n\n")
        else:
            f.write("目前沒有 FAILED 任務。\n")

    with open(RETRY_NEEDED_FILE, "w", encoding="utf-8") as f:
        if retry_items:
            f.write("=== CURRENT RETRY TASKS ===\n\n")
            for i, t in enumerate(retry_items, 1):
                f.write(f"[{i}] RETRY\n")
                f.write(f"{t['url']}\n")
                f.write(f"原因: {t.get('error') or '未知錯誤'}\n\n")
        else:
            f.write("目前沒有 RETRY 任務。\n")

    with open(UNAVAILABLE_FILE, "w", encoding="utf-8") as f:
        if unavailable_items:
            f.write("=== CURRENT BLOCKED / UNAVAILABLE TASKS ===\n\n")
            for i, t in enumerate(unavailable_items, 1):
                f.write(f"[{i}] {t['status']}\n")
                f.write(f"{t['url']}\n")
                f.write(f"原因: {t.get('error') or '未知錯誤'}\n\n")
        else:
            f.write("目前沒有 BLOCKED / UNAVAILABLE 任務。\n")


def write_logs():
    rewrite_status_files()


def get_failed_links_text() -> str:
    snapshot = get_snapshot()
    failed_items = [t for t in snapshot if t["status"] in ("FAILED", "BLOCKED", "RETRY", "UNAVAILABLE")]

    if not failed_items:
        return "目前沒有失敗連結。"

    lines = []
    for i, t in enumerate(failed_items, 1):
        lines.append(f"[{i}] {t['status']}")
        lines.append(t["url"])
        if t.get("error"):
            lines.append(f"原因: {t['error']}")
        lines.append("")

    return "\n".join(lines)