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
                    status, error = instagram.download(url)
                elif "facebook.com" in url or "fb.watch" in url:
                    status, error = facebook.download(url)
                else:
                    status, error = "FAILED", "不支援的平台"
                    break
            except Exception as e:
                error = str(e)
                status = queue_manager.map_status(error)

            if status in ("SUCCESS", "UNAVAILABLE", "BLOCKED"):
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
            sleep_sec = int(random.uniform(20, 40))
            logger.info(f"冷卻 {sleep_sec}s...")
            _cooldown_sleep(sleep_sec, url)

        elif status == "RETRY":
            sleep_sec = 60
            logger.info(f"RETRY 冷卻 {sleep_sec}s...")
            _cooldown_sleep(sleep_sec, url)

        elif status == "BLOCKED":
            _cooldown_sleep(5, url)

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