import random
import threading
import time

import queue_manager
from downloader import instagram, facebook
from utils.logger import get_logger

logger = get_logger("worker")

_stop_event = threading.Event()
_pause_event = threading.Event()
_thread = None


def start():
    global _thread
    if _thread and _thread.is_alive():
        return

    _stop_event.clear()
    _pause_event.set()

    _thread = threading.Thread(target=_worker_loop, name="DownloadWorker", daemon=True)
    _thread.start()

    logger.info("Worker 已啟動")
    queue_manager.update_runtime(
        phase="IDLE",
        message="就緒",
        active_url="",
        cooldown_remaining=0,
    )


def stop():
    _stop_event.set()
    _pause_event.set()
    logger.info("Worker 停止中...")
    queue_manager.update_runtime(
        phase="STOPPED",
        message="停止中，等待目前任務安全收尾...",
        active_url="",
        cooldown_remaining=0,
    )


def pause():
    if _stop_event.is_set():
        return
    _pause_event.clear()
    logger.info("Worker 已暫停")
    queue_manager.update_runtime(
        phase="PAUSED",
        message="已暫停",
        active_url="",
        cooldown_remaining=0,
    )


def resume():
    if _stop_event.is_set():
        return
    _pause_event.set()
    logger.info("Worker 已繼續")
    queue_manager.update_runtime(
        phase="IDLE",
        message="已繼續，等待任務中...",
        active_url="",
        cooldown_remaining=0,
    )


def is_paused() -> bool:
    return not _pause_event.is_set()


def is_stopped() -> bool:
    return _stop_event.is_set()


def _wait_if_paused():
    while not _pause_event.is_set() and not _stop_event.is_set():
        queue_manager.update_runtime(
            phase="PAUSED",
            message="已暫停",
            active_url="",
            cooldown_remaining=0,
        )
        time.sleep(0.2)



def _handle_instagram_profile_expand(url: str):
    """Expand an Instagram profile URL into individual post/reel queue tasks.

    The profile task itself does not download media.  It only scans the profile
    with IG Parser persistent profile and appends discovered posts back into the
    normal queue, so every child URL still uses the proven single-post downloader.
    """
    queue_manager.update_runtime(
        phase="DOWNLOADING",
        message="IG 主頁掃描中，正在收集貼文 / Reels...",
        active_url=url,
        cooldown_remaining=0,
    )

    status, post_urls, message = instagram.scan_profile_post_urls(url)
    if status != "SUCCESS":
        return status, message, False

    result = queue_manager.add_tasks(post_urls)
    added = int(result.get("added", 0) or 0)
    skipped_processed = int(result.get("skipped_processed", result.get("skipped", 0)) or 0)
    skipped_duplicate = int(result.get("skipped_duplicate", result.get("duplicated", 0)) or 0)

    summary = (
        f"IG 主頁已展開：掃到 {len(post_urls)} 筆，"
        f"新增 {added} 筆，略過已下載 {skipped_processed} 筆，略過重複 {skipped_duplicate} 筆"
    )
    if message:
        summary = f"{summary}；{message}"

    queue_manager.update_runtime(
        phase="DOWNLOADING",
        message=summary,
        active_url=url,
        cooldown_remaining=0,
    )
    logger.info(summary)
    return "SUCCESS", summary, True

def _worker_loop():
    while not _stop_event.is_set():
        _wait_if_paused()
        if _stop_event.is_set():
            break

        task = queue_manager.get_task()
        if not task:
            queue_manager.update_runtime(
                phase="IDLE",
                message="等待任務中...",
                active_url="",
                cooldown_remaining=0,
            )
            time.sleep(0.3)
            continue

        url = task["url"]
        logger.info(f"開始: {url}")

        queue_manager.update_runtime(
            phase="DOWNLOADING",
            message="下載中...",
            active_url=url,
            cooldown_remaining=0,
        )

        status, error = "FAILED", ""
        profile_expanded = False
        max_attempts = 2

        for attempt in range(max_attempts):
            if _stop_event.is_set():
                status, error = "RETRY", "使用者停止"
                break

            _wait_if_paused()
            if _stop_event.is_set():
                status, error = "RETRY", "使用者停止"
                break

            try:
                if "instagram.com" in url:
                    username = ""
                    try:
                        username = instagram.is_instagram_profile_url(url)
                    except Exception:
                        username = ""

                    if username:
                        status, error, profile_expanded = _handle_instagram_profile_expand(url)
                    else:
                        queue_manager.update_runtime(
                            phase="DOWNLOADING",
                            message="正在讀取 Post Title...",
                            active_url=url,
                            cooldown_remaining=0,
                        )
                        try:
                            prefetched_title, title_error = instagram.prefetch_post_title(url)
                            if prefetched_title:
                                logger.info(f"Post Title 已填入，開始下載: {prefetched_title}")
                                queue_manager.update_runtime(
                                    phase="DOWNLOADING",
                                    message=f"已取得標題，開始下載：{prefetched_title}",
                                    active_url=url,
                                    cooldown_remaining=0,
                                )
                            else:
                                logger.info(f"Post Title 預取略過，沿用下載階段解析: {title_error}")
                                queue_manager.update_runtime(
                                    phase="DOWNLOADING",
                                    message="未預取到標題，開始下載並於下載階段補抓...",
                                    active_url=url,
                                    cooldown_remaining=0,
                                )
                        except Exception as e:
                            logger.info(f"Post Title 預取失敗，繼續正常下載: {e}")
                        status, error = instagram.download(url)
                elif "facebook.com" in url or "fb.watch" in url:
                    status, error = facebook.download(url)
                else:
                    status, error = "FAILED", "不支援的平台"
                    break
            except Exception as e:
                error = str(e)
                status = queue_manager.map_status(error)

            if status in ("SUCCESS", "UNAVAILABLE", "MISSING", "BLOCKED"):
                break

            if attempt < max_attempts - 1:
                retry_msg = f"下載未成功（{status}），準備重試 {attempt + 2}/{max_attempts}..."
                logger.info(retry_msg)
                queue_manager.update_runtime(
                    phase="DOWNLOADING",
                    message=retry_msg,
                    active_url=url,
                    cooldown_remaining=0,
                )
                _interruptible_sleep(3)

        # 統一由 queue_manager 處理 checkpoint / failed / unavailable / retry 檔案同步
        queue_manager.set_task_result(url, status, error)

        # 額外保留歷史事件流水帳（只要不是成功就寫一筆）
        if status != "SUCCESS":
            queue_manager.append_failed_event(url, error or status)

        logger.info(f"完成: {url} → {status}")

        queue_manager.update_runtime(
            phase="DOWNLOADING",
            message=f"完成：{status}",
            active_url=url,
            cooldown_remaining=0,
        )

        if _stop_event.is_set():
            break

        if status == "SUCCESS":
            if profile_expanded:
                logger.info("IG 主頁展開完成，略過一般下載冷卻，繼續處理展開後的貼文任務")
                _cooldown_sleep(2, url)
            else:
                sleep_sec = int(random.uniform(20, 40))
                logger.info(f"冷卻 {sleep_sec}s...")
                _cooldown_sleep(sleep_sec, url)

        elif status == "RETRY":
            sleep_sec = 60
            logger.info(f"RETRY 冷卻 {sleep_sec}s...")
            _cooldown_sleep(sleep_sec, url)

        elif status == "BLOCKED":
            _cooldown_sleep(5, url)

        elif status == "MISSING":
            _cooldown_sleep(2, url)

    queue_manager.update_runtime(
        phase="STOPPED",
        message="Worker 已停止",
        active_url="",
        cooldown_remaining=0,
    )


def _cooldown_sleep(seconds: int, url: str):
    end = time.monotonic() + seconds

    while time.monotonic() < end and not _stop_event.is_set():
        _wait_if_paused()
        if _stop_event.is_set():
            break

        remain = max(0, int(end - time.monotonic()) + 1)
        queue_manager.update_runtime(
            phase="COOLDOWN",
            message=f"冷卻中，剩餘 {remain} 秒",
            active_url=url,
            cooldown_remaining=remain,
        )
        time.sleep(1)

    if not _stop_event.is_set():
        queue_manager.update_runtime(
            phase="IDLE",
            message="冷卻結束，等待下一個任務...",
            active_url="",
            cooldown_remaining=0,
        )


def _interruptible_sleep(seconds: float):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not _stop_event.is_set():
        _wait_if_paused()
        if _stop_event.is_set():
            break
        time.sleep(min(1.0, end - time.monotonic()))