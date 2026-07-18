# v12.11 Facebook Metadata Identity Fix
# v12.10 Instagram Metadata Identity Fix
# v12.01 Profile Parent Checkpoint Fix
# v12.00 Profile Batch Priority Queue
import os
import re
import threading
import time
from typing import Optional

from config import (
    DATA_DIR,
    CHECKPOINT_FILE,
    FAILED_LOG_FILE,
    RETRY_NEEDED_FILE,
    UNAVAILABLE_FILE,
)

_LOCK = threading.RLock()
_TASKS: list[dict] = []
_PROCESSED: set[str] = set()
_FAILED_EVENTS: list[dict] = []

_RUNTIME = {
    "phase": "IDLE",
    "message": "就緒",
    "active_url": "",
    "cooldown_remaining": 0,
    "total": 0,
    "done": 0,
}

_TERMINAL_STATUSES = {"SUCCESS", "FAILED", "BLOCKED", "MISSING", "UNAVAILABLE"}
_RETRYABLE_STATUSES = {"RETRY"}
_FAILURE_STATUSES = {"FAILED", "BLOCKED", "MISSING", "UNAVAILABLE", "RETRY"}


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)


def _append_unique_line(path: str, line: str):
    if not line:
        return
    _ensure_dirs()
    existing = set()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                existing = {x.strip() for x in f if x.strip()}
        except Exception:
            existing = set()
    if line in existing:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _append_line(path: str, line: str):
    if not line:
        return
    _ensure_dirs()
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _normalize_status(status: str) -> str:
    status = (status or "").upper().strip()
    aliases = {
        "OK": "SUCCESS",
        "DONE": "SUCCESS",
        "INVALID": "UNAVAILABLE",
        "MISSING_PAGE": "MISSING",
        "NOT_FOUND": "MISSING",
    }
    return aliases.get(status, status or "FAILED")


def _extract_urls_from_text(text: str) -> list[str]:
    """Extract plain URL values from failed_links.log style lines."""
    if not text:
        return []
    urls = []
    seen = set()
    for match in re.findall(r"https?://[^\s\t]+", text):
        url = match.strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls



def _task_identity_key(url: str) -> str:
    """Return a conservative identity key used for queue dedupe/metadata matching.

    v12.10:
    Instagram share URLs can differ by ?igsh=... while pointing to the same
    shortcode.  Profile expansion must not enqueue the same post twice merely
    because one copy is /p/<code>/ and another is /reel/<code>/ or has query
    parameters.

    v12.11:
    Facebook download stages may publish metadata using the original URL, a
    resolved/canonical URL, a fragment-stripped URL, or a media URL.  Exact string
    matching is therefore not enough for FB story/reel rows.  Use stable FB
    identities so update_task_title/update_task_account can update the visible
    GUI row after download.
    """
    clean = (url or "").strip()
    if not clean:
        return ""

    # URL fragment never identifies a different IG/FB media task.
    clean_no_fragment = clean.split("#", 1)[0].strip()
    low = clean_no_fragment.lower()

    if "instagram.com" in low:
        m = re.search(r"/(?:p|reel|reels)/([^/?#&]+)", clean_no_fragment, flags=re.I)
        if m:
            return f"instagram:{m.group(1)}"
        return clean_no_fragment

    if "facebook.com" in low or "fb.watch" in low:
        raw = clean_no_fragment

        # Explicit story/post URLs. story_fbid is the strongest identity for
        # story.php?story_fbid=... tasks.
        m = re.search(r"[?&]story_fbid=([0-9]{8,})", raw, flags=re.I)
        if m:
            return f"facebook:story:{m.group(1)}"

        # post_id can be pageid_storyid; preserve full value when present.
        m = re.search(r"[?&]post_id=([0-9_]{8,})", raw, flags=re.I)
        if m:
            return f"facebook:post:{m.group(1)}"

        # Photo URL identity.
        m = re.search(r"[?&](?:fbid|photo_id)=([0-9]{8,})", raw, flags=re.I)
        if m:
            return f"facebook:photo:{m.group(1)}"

        # Reel / Watch / Video identity.
        for pat in [
            r"/(?:reel|reels)/([0-9]{6,})",
            r"/watch/reel/([0-9]{6,})",
            r"[?&]v=([0-9]{6,})",
            r"/videos/([0-9]{6,})",
        ]:
            m = re.search(pat, raw, flags=re.I)
            if m:
                return f"facebook:video:{m.group(1)}"

        # Short share IDs are stable for the same submitted share task.
        for pat in [
            r"/share/(?:r|v|p)/([^/?#&]+)",
            r"/share/([^/?#&]+)",
        ]:
            m = re.search(pat, raw, flags=re.I)
            if m:
                return f"facebook:share:{m.group(1)}"

        return clean_no_fragment

    return clean_no_fragment


def insert_tasks_after(anchor_url: str, urls: list[str], batch_parent: str = "") -> dict:
    """Insert child tasks immediately after *anchor_url*.

    This is used by Instagram profile expansion only. Existing add_tasks()
    behavior remains unchanged for normal GUI imports.

    Profile child tasks carry batch_parent metadata so worker.py can process
    them consecutively with a short cooldown without changing normal task
    throttling.
    """
    added = 0
    skipped = 0
    duplicated = 0

    clean_anchor = (anchor_url or "").strip()
    clean_parent = (batch_parent or clean_anchor).strip()

    with _LOCK:
        existing_keys = {
            _task_identity_key(t.get("url", ""))
            for t in _TASKS
            if t.get("url")
        }
        processed_keys = {_task_identity_key(u) for u in _PROCESSED if u}

        anchor_index = -1
        for i, task in enumerate(_TASKS):
            if task.get("url") == clean_anchor:
                anchor_index = i
                break

        insert_at = anchor_index + 1 if anchor_index >= 0 else len(_TASKS)
        new_tasks = []

        for raw in urls or []:
            url = (raw or "").strip()
            if not url:
                continue

            key = _task_identity_key(url)
            if not key:
                continue

            if key in existing_keys:
                duplicated += 1
                continue

            now = time.time()
            if key in processed_keys:
                skipped += 1
                task = {
                    "url": url,
                    "status": "SUCCESS",
                    "retry": 0,
                    "title": "",
                    "account": "",
                    "error": "已在 processed_links.log 中",
                    "profile_batch_parent": clean_parent,
                    "created_at": now,
                    "updated_at": now,
                }
            else:
                added += 1
                task = {
                    "url": url,
                    "status": "PENDING",
                    "retry": 0,
                    "title": "",
                    "account": "",
                    "error": "",
                    "profile_batch_parent": clean_parent,
                    "created_at": now,
                    "updated_at": now,
                }

            new_tasks.append(task)
            existing_keys.add(key)

        if new_tasks:
            _TASKS[insert_at:insert_at] = new_tasks

        _recompute_runtime_counts_locked()

    return {
        "added": added,
        "skipped": skipped,
        "duplicated": duplicated,
        "skipped_processed": skipped,
        "skipped_duplicate": duplicated,
        "inserted": len(new_tasks),
        "total": len(_TASKS),
    }


def _find_task(url: str) -> Optional[dict]:
    """Find a task by exact URL, then by conservative media identity.

    Instagram download stages normalize share URLs by removing ``?igsh=...``.
    Queue rows keep the user's original URL, so metadata publishing must still
    resolve both forms to the same shortcode-backed task.
    """
    clean_url = (url or "").strip()
    if not clean_url:
        return None

    for task in _TASKS:
        if task.get("url") == clean_url:
            return task

    identity = _task_identity_key(clean_url)
    if not identity:
        return None

    for task in _TASKS:
        task_url = (task.get("url") or "").strip()
        if task_url and _task_identity_key(task_url) == identity:
            return task

    return None


def update_task_title(url: str, title: str) -> bool:
    """Update one task's display title without changing queue order or status."""
    clean_url = (url or "").strip()
    clean_title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not clean_url or not clean_title:
        return False

    with _LOCK:
        task = _find_task(clean_url)
        if task is None:
            return False
        task["title"] = clean_title
        task["updated_at"] = time.time()

        if task.get("status") == "DOWNLOADING":
            _RUNTIME["message"] = f"下載中：{clean_title}"
            _RUNTIME["active_url"] = task.get("url") or clean_url

        return True


def update_task_account(url: str, account: str) -> bool:
    """Update one task's Instagram/Facebook post account without changing status."""
    clean_url = (url or "").strip()
    clean_account = re.sub(r"\s+", " ", str(account or "")).strip().lstrip("@")
    if not clean_url or not clean_account:
        return False

    with _LOCK:
        task = _find_task(clean_url)
        if task is None:
            return False
        task["account"] = clean_account
        task["updated_at"] = time.time()
        return True


def _recompute_runtime_counts_locked():
    total = len(_TASKS)
    done = sum(1 for t in _TASKS if t.get("status") in _TERMINAL_STATUSES)
    _RUNTIME["total"] = total
    _RUNTIME["done"] = done



def _is_instagram_profile_queue_url(url: str) -> bool:
    """Return True only for an Instagram account/profile URL.

    Profile URLs are queue expanders, not downloaded media. They must never be
    treated as permanently completed checkpoint entries because their child
    posts may be only partially downloaded when the app is stopped.
    """
    clean = (url or "").strip()
    if not clean:
        return False

    try:
        from urllib.parse import urlparse
        parsed = urlparse(clean)
    except Exception:
        return False

    host = (parsed.netloc or "").lower()
    if host not in {"instagram.com", "www.instagram.com"}:
        return False

    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return False

    reserved = {
        "p", "reel", "reels", "tv", "stories", "explore", "accounts",
        "direct", "graphql", "api", "challenge", "oauth", "settings",
    }

    if parts[0].lower() in reserved:
        return False

    if len(parts) == 1:
        return bool(re.fullmatch(r"[A-Za-z0-9._]{1,30}", parts[0]))

    if len(parts) == 2 and parts[1].lower() in {"reels", "tagged"}:
        return bool(re.fullmatch(r"[A-Za-z0-9._]{1,30}", parts[0]))

    return False


def _rewrite_checkpoint_locked():
    """Rewrite checkpoint from the in-memory processed set atomically enough for GUI use."""
    _ensure_dirs()
    tmp_path = CHECKPOINT_FILE + ".tmp"

    with open(tmp_path, "w", encoding="utf-8") as f:
        for item in sorted(_PROCESSED):
            if item:
                f.write(item + "\n")

    try:
        os.replace(tmp_path, CHECKPOINT_FILE)
    except Exception:
        try:
            if os.path.exists(CHECKPOINT_FILE):
                os.remove(CHECKPOINT_FILE)
        except Exception:
            pass
        os.rename(tmp_path, CHECKPOINT_FILE)


def load_checkpoint():
    _ensure_dirs()
    with _LOCK:
        _PROCESSED.clear()
        removed_profile_entries = 0

        if os.path.exists(CHECKPOINT_FILE):
            try:
                with open(CHECKPOINT_FILE, "r", encoding="utf-8", errors="ignore") as f:
                    for raw in f:
                        url = raw.strip()
                        if not url:
                            continue

                        # v12.01 migration:
                        # Older versions permanently checkpointed Instagram
                        # profile expanders as SUCCESS. Remove those stale parent
                        # entries while preserving every completed post/reel URL.
                        if _is_instagram_profile_queue_url(url):
                            removed_profile_entries += 1
                            continue

                        _PROCESSED.add(url)
            except Exception:
                pass

        if removed_profile_entries:
            try:
                _rewrite_checkpoint_locked()
            except Exception:
                pass

        _recompute_runtime_counts_locked()


def get_processed_count() -> int:
    with _LOCK:
        return len(_PROCESSED)


def clear_checkpoint():
    _ensure_dirs()
    with _LOCK:
        _PROCESSED.clear()
        if os.path.exists(CHECKPOINT_FILE):
            try:
                os.remove(CHECKPOINT_FILE)
            except Exception:
                open(CHECKPOINT_FILE, "w", encoding="utf-8").close()
        for task in _TASKS:
            if task.get("status") == "SUCCESS":
                task["status"] = "PENDING"
                task["error"] = ""
        _recompute_runtime_counts_locked()


# Normal GUI import path. Profile expansion uses insert_tasks_after().
def add_tasks(urls: list[str]) -> dict:
    added = 0
    skipped = 0
    duplicated = 0
    with _LOCK:
        existing = {t.get("url") for t in _TASKS}
        existing_keys = {
            _task_identity_key(t.get("url", ""))
            for t in _TASKS
            if t.get("url")
        }
        processed_keys = {_task_identity_key(u) for u in _PROCESSED if u}

        for raw in urls or []:
            url = (raw or "").strip()
            if not url:
                continue

            key = _task_identity_key(url)

            if url in existing or (key and key in existing_keys):
                duplicated += 1
                continue

            if url in _PROCESSED or (key and key in processed_keys):
                skipped += 1
                _TASKS.append({
                    "url": url,
                    "status": "SUCCESS",
                    "retry": 0,
                    "title": "",
                    "account": "",
                    "error": "已在 processed_links.log 中",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                })
            else:
                added += 1
                _TASKS.append({
                    "url": url,
                    "status": "PENDING",
                    "retry": 0,
                    "error": "",
                    "title": "",
                    "account": "",
                    "created_at": time.time(),
                    "updated_at": time.time(),
                })
            existing.add(url)
            if key:
                existing_keys.add(key)
        _recompute_runtime_counts_locked()
    # 相容舊版 / 新版 GUI 鍵名：
    # - 舊 queue_manager 回傳 skipped / duplicated
    # - 部分 main.py 版本讀 skipped_processed / skipped_duplicate
    # 同時保留兩組鍵，避免 GUI 因 KeyError 閃退。
    return {
        "added": added,
        "skipped": skipped,
        "duplicated": duplicated,
        "skipped_processed": skipped,
        "skipped_duplicate": duplicated,
        "total": len(_TASKS),
    }


def get_task() -> Optional[dict]:
    with _LOCK:
        # RETRY 需要人工按「重試失敗」或下一輪才重排；這裡只取 PENDING。
        for task in _TASKS:
            if task.get("status") == "PENDING":
                task["status"] = "DOWNLOADING"
                task["updated_at"] = time.time()
                _recompute_runtime_counts_locked()
                return dict(task)
    return None


def set_task_result(url: str, status: str, error: str = "", checkpoint_success: bool = True):
    status = _normalize_status(status)
    error = error or ""
    _ensure_dirs()
    with _LOCK:
        task = _find_task(url)
        if task is None:
            task = {
                "url": url,
                "title": "",
                "account": "",
                "status": status,
                "retry": 0,
                "error": error,
                "created_at": time.time(),
                "updated_at": time.time(),
            }
            _TASKS.append(task)
        else:
            if status == "RETRY":
                task["retry"] = int(task.get("retry") or 0) + 1
            task["status"] = status
            task["error"] = error
            task["updated_at"] = time.time()

        if status == "SUCCESS":
            if checkpoint_success and not _is_instagram_profile_queue_url(url):
                _PROCESSED.add(url)
                _append_unique_line(CHECKPOINT_FILE, url)
            else:
                # Profile parent is only a session-level expansion success.
                # It must remain re-runnable after restart until all child media
                # tasks have independently entered the checkpoint.
                if url in _PROCESSED:
                    _PROCESSED.discard(url)
                    try:
                        _rewrite_checkpoint_locked()
                    except Exception:
                        pass
        elif status == "RETRY":
            _append_unique_line(RETRY_NEEDED_FILE, url)
            _append_unique_line(FAILED_LOG_FILE, f"[RETRY]\t{url}\t{error}")
        elif status in {"MISSING", "UNAVAILABLE"}:
            _append_unique_line(UNAVAILABLE_FILE, url)
            _append_unique_line(FAILED_LOG_FILE, f"[{status}]\t{url}\t{error}")
        elif status in {"FAILED", "BLOCKED"}:
            _append_unique_line(FAILED_LOG_FILE, f"[{status}]\t{url}\t{error}")
        _recompute_runtime_counts_locked()


def append_failed_event(url: str, reason: str = ""):
    reason = reason or ""
    status = map_status(reason)
    with _LOCK:
        task = _find_task(url)
        if task and task.get("status") in _FAILURE_STATUSES:
            status = task.get("status")
        event = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "url": url,
            "status": status,
            "reason": reason,
        }
        _FAILED_EVENTS.append(event)
    _append_line(os.path.join(DATA_DIR, "failed_history.log"), f"{event['ts']}\t[{status}]\t{url}\t{reason}")


def get_snapshot() -> list[dict]:
    with _LOCK:
        return [dict(t) for t in _TASKS]


def get_urls_by_status(status: str) -> list[str]:
    status = _normalize_status(status)
    with _LOCK:
        urls = [t.get("url", "") for t in _TASKS if t.get("status") == status and t.get("url")]

    if urls:
        return urls

    # Fallback for cases where the GUI was restarted and in-memory tasks are empty.
    # Keep this URL-only; do not return the whole failed log line.
    if os.path.exists(FAILED_LOG_FILE):
        try:
            with open(FAILED_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = [line for line in f.read().splitlines() if f"[{status}]" in line]
            return _extract_urls_from_text("\n".join(lines))
        except Exception:
            pass

    if status == "RETRY" and os.path.exists(RETRY_NEEDED_FILE):
        try:
            with open(RETRY_NEEDED_FILE, "r", encoding="utf-8", errors="ignore") as f:
                return [x.strip() for x in f if x.strip()]
        except Exception:
            pass

    if status in {"MISSING", "UNAVAILABLE"} and os.path.exists(UNAVAILABLE_FILE):
        try:
            with open(UNAVAILABLE_FILE, "r", encoding="utf-8", errors="ignore") as f:
                return [x.strip() for x in f if x.strip()]
        except Exception:
            pass

    return []


def get_tasks_by_status(status: str = "ALL") -> list[dict]:
    """
    取得指定狀態任務。
    status="ALL" 回傳所有失敗類任務：FAILED / BLOCKED / MISSING / UNAVAILABLE / RETRY。
    """
    status = (status or "ALL").upper().strip()
    with _LOCK:
        if status == "ALL":
            return [dict(t) for t in _TASKS if t.get("status") in _FAILURE_STATUSES]
        return [dict(t) for t in _TASKS if t.get("status") == status]


def get_failed_statuses() -> tuple[str, ...]:
    return ("ALL", "FAILED", "BLOCKED", "MISSING", "UNAVAILABLE", "RETRY")


def get_failed_links_text(status_filter: str = "ALL", url_only: bool = False, urls_only: bool | None = None) -> str:
    if urls_only is not None:
        url_only = bool(urls_only)
    status_filter = (status_filter or "ALL").upper().strip()
    rows = []
    with _LOCK:
        for t in _TASKS:
            status = t.get("status", "")
            if status not in _FAILURE_STATUSES:
                continue
            if status_filter != "ALL" and status != status_filter:
                continue
            url = t.get("url", "")
            if not url:
                continue
            if url_only:
                rows.append(url)
            else:
                retry = t.get("retry", 0)
                error = (t.get("error") or "").replace("\n", " ").strip()
                rows.append(f"[{status}] retry={retry}\t{url}" + (f"\t{error}" if error else ""))

    if rows:
        return "\n".join(rows)

    # fallback：若 GUI 重新啟動後記憶體沒有任務，仍可直接看 failed_links.log。
    if os.path.exists(FAILED_LOG_FILE):
        try:
            with open(FAILED_LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()
            if content:
                if status_filter == "ALL":
                    lines = content.splitlines()
                else:
                    lines = [line for line in content.splitlines() if f"[{status_filter}]" in line]
                if url_only:
                    urls = _extract_urls_from_text("\n".join(lines))
                    return "\n".join(urls) if urls else f"目前沒有 {status_filter} 紀錄。"
                return "\n".join(lines) if lines else f"目前沒有 {status_filter} 紀錄。"
        except Exception:
            pass

    return "目前沒有失敗 / BLOCKED / MISSING 紀錄。"


def get_runtime() -> dict:
    with _LOCK:
        _recompute_runtime_counts_locked()
        return dict(_RUNTIME)


def update_runtime(**kwargs):
    with _LOCK:
        _RUNTIME.update(kwargs)
        _recompute_runtime_counts_locked()


def mark_stop_requested():
    """
    GUI 按下停止時記錄狀態，並把尚未真正完成的 DOWNLOADING 任務轉成 RETRY。

    原因：worker.stop() 可能讓背景 thread 在目前下載流程中安全收尾；
    但若下載函式卡在網路 / Playwright / yt-dlp 等待，GUI 再按「繼續」時，
    該任務可能仍停在 DOWNLOADING，導致 queue 不會再取下一筆。
    轉為 RETRY 可讓畫面不再假裝仍在下載，並保留人工重試紀錄。
    """
    with _LOCK:
        changed = 0
        for task in _TASKS:
            if task.get("status") == "DOWNLOADING":
                task["status"] = "RETRY"
                task["error"] = task.get("error") or "使用者按下停止，中斷任務；可按繼續或重試失敗重新排程"
                task["updated_at"] = time.time()
                changed += 1
        if changed:
            _RUNTIME.update({
                "phase": "STOPPED",
                "message": "已停止；可按繼續重新喚醒 worker",
                "active_url": "",
                "cooldown_remaining": 0,
            })
        _recompute_runtime_counts_locked()
        return changed


def reset_interrupted_downloads():
    """
    按「繼續」時，把 STOP 造成的 RETRY 任務放回 PENDING。
    只重排本函式產生的停止中斷任務，不碰一般失敗 / BLOCKED / MISSING。
    """
    with _LOCK:
        changed = 0
        for task in _TASKS:
            if task.get("status") == "DOWNLOADING":
                task["status"] = "PENDING"
                task["error"] = ""
                task["updated_at"] = time.time()
                changed += 1
            elif task.get("status") == "RETRY" and "使用者按下停止" in (task.get("error") or ""):
                task["status"] = "PENDING"
                task["error"] = ""
                task["updated_at"] = time.time()
                changed += 1
        if changed:
            _RUNTIME.update({
                "phase": "RUNNING",
                "message": "繼續下載中",
                "cooldown_remaining": 0,
            })
        _recompute_runtime_counts_locked()
        return changed


def has_pending_tasks() -> bool:
    with _LOCK:
        return any(t.get("status") == "PENDING" for t in _TASKS)


def retry_failed():
    with _LOCK:
        for task in _TASKS:
            if task.get("status") in {"FAILED", "RETRY"}:
                task["status"] = "PENDING"
                task["error"] = ""
                task["updated_at"] = time.time()
        _recompute_runtime_counts_locked()


def clear_tasks():
    with _LOCK:
        _TASKS.clear()
        _FAILED_EVENTS.clear()
        _RUNTIME.update({
            "phase": "IDLE",
            "message": "就緒",
            "active_url": "",
            "cooldown_remaining": 0,
            "total": 0,
            "done": 0,
        })


def write_logs():
    _ensure_dirs()
    snapshot_path = os.path.join(DATA_DIR, "tasks_snapshot.tsv")
    with _LOCK:
        with open(snapshot_path, "w", encoding="utf-8") as f:
            f.write("status\tretry\turl\terror\n")
            for task in _TASKS:
                f.write(
                    f"{task.get('status','')}\t{task.get('retry',0)}\t{task.get('url','')}\t{(task.get('error') or '').replace(chr(10), ' ')}\n"
                )


def map_status(error: str) -> str:
    e = (error or "").lower()
    if any(k in e for k in [
        "missing", "page not found", "not found", "404", "已遭移除", "頁面無法使用", "此頁面無法使用", "link may be broken", "page may have been removed",
    ]):
        return "MISSING"
    if any(k in e for k in [
        "blocked", "private", "permission", "not available to everyone", "特定受眾", "無法查看", "requires login", "login required", "checkpoint", "challenge",
    ]):
        return "BLOCKED"
    if any(k in e for k in [
        "rate limit", "too many requests", "please wait", "429", "timeout", "timed out",
    ]):
        return "RETRY"
    if any(k in e for k in [
        "deleted", "unavailable", "內容不存在", "內容已刪除",
    ]):
        return "UNAVAILABLE"
    return "FAILED"
