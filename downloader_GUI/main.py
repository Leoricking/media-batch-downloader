import json
import math
import os
import re
import sys
import time
import threading
import tkinter as tk
from datetime import timedelta
from tkinter import filedialog, messagebox, simpledialog, ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except ImportError:
    _HAS_DND = False
    DND_FILES = None
    TkinterDnD = None

import queue_manager
import worker
from config import (
    ACCOUNTS_FILE,
    COOKIES_FILE,
    DOWNLOAD_DIR,
    DATA_DIR,
    PREPROCESS_DEFAULT_DOWNLOAD,
    PREPROCESS_DIR,
    PREPROCESS_OUTPUT_DIR,
)
from downloader import instagram
from utils.logger import get_logger

if PREPROCESS_DIR not in sys.path:
    sys.path.insert(0, PREPROCESS_DIR)

try:
    import link_sorter
except Exception:
    link_sorter = None

logger = get_logger("main")

_STATUS_COLORS = {
    "PENDING": "#888888",
    "DOWNLOADING": "#2196F3",
    "SUCCESS": "#4CAF50",
    "FAILED": "#F44336",
    "BLOCKED": "#FF9800",
    "RETRY": "#9C27B0",
    "UNAVAILABLE": "#795548",
}

_PLACEHOLDER = "在此貼上 URL（每行一個），或拖入 txt 檔案..."


def _format_seconds(sec) -> str:
    if sec is None:
        return "--:--"
    if isinstance(sec, float):
        if math.isnan(sec) or math.isinf(sec):
            return "--:--"
    sec = max(0, int(sec))
    return str(timedelta(seconds=sec))


def _extract_urls(text: str) -> list[str]:
    if not text:
        return []
    urls = re.findall(r'https?://[^\s\,\"\'\)\]]+', text)
    out = []
    seen = set()
    for u in urls:
        u = u.strip()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Media Batch Downloader")
        self.root.geometry("1180x800")
        self.root.minsize(980, 660)

        self._active_filter = "ALL"
        self._refresh_id = None
        self._blocked_warned = False
        self._login_in_progress = False
        self._failed_window = None

        self._elapsed_start_ts = None

        # v5.1 completion popup state
        # 下載批次完成後只彈一次，避免 _refresh_table 每秒重複彈出。
        self._completion_notified = False
        self._last_total_for_completion = 0

        self._build_ui()
        queue_manager.load_checkpoint()
        worker.start()

        self.root.after(300, self._init_session)
        self.root.after(600, self._refresh_table)

    def _build_ui(self):
        top = tk.Frame(self.root, padx=10, pady=8)
        top.pack(fill=tk.X)

        tk.Label(
            top,
            text="Media Batch Downloader",
            font=("Microsoft JhengHei UI", 18, "bold"),
        ).pack(anchor="w")

        hint = "支援 Instagram / Facebook  •  可貼 URL 或拖入 .txt  •  匯入 txt 會自動預處理"
        if not _HAS_DND:
            hint += "  （安裝 tkinterdnd2 可啟用拖放）"
        tk.Label(
            top,
            text=hint,
            font=("Microsoft JhengHei UI", 10),
            fg="#666666",
        ).pack(anchor="w")

        progress_frame = tk.Frame(top)
        progress_frame.pack(fill=tk.X, pady=(8, 4))

        self.progress_label_var = tk.StringVar(value="進度：0 / 0")
        tk.Label(
            progress_frame,
            textvariable=self.progress_label_var,
            font=("Microsoft JhengHei UI", 10, "bold"),
            fg="#333333",
        ).pack(anchor="w")

        self.progress = ttk.Progressbar(
            progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.progress.pack(fill=tk.X, pady=(4, 2))

        self.phase_var = tk.StringVar(value="目前狀態：就緒")
        tk.Label(
            progress_frame,
            textvariable=self.phase_var,
            font=("Microsoft JhengHei UI", 9),
            fg="#444444",
        ).pack(anchor="w")

        self.active_url_var = tk.StringVar(value="目前 URL：")
        tk.Label(
            progress_frame,
            textvariable=self.active_url_var,
            font=("Consolas", 9),
            fg="#666666",
        ).pack(anchor="w")

        self.cooldown_var = tk.StringVar(value="")
        tk.Label(
            progress_frame,
            textvariable=self.cooldown_var,
            font=("Microsoft JhengHei UI", 10, "bold"),
            fg="#E65100",
        ).pack(anchor="w")

        time_frame = tk.Frame(progress_frame)
        time_frame.pack(anchor="w", pady=(2, 0))

        self.elapsed_var = tk.StringVar(value="Elapsed: 00:00")
        self.remaining_var = tk.StringVar(value="Remaining: --:--")

        tk.Label(
            time_frame,
            textvariable=self.elapsed_var,
            font=("Microsoft JhengHei UI", 10, "bold"),
            fg="#1976D2",
        ).pack(side=tk.LEFT, padx=(0, 20))

        tk.Label(
            time_frame,
            textvariable=self.remaining_var,
            font=("Microsoft JhengHei UI", 10, "bold"),
            fg="#2E7D32",
        ).pack(side=tk.LEFT)

        self.text_input = tk.Text(
            top,
            height=6,
            wrap=tk.WORD,
            font=("Consolas", 11),
            relief=tk.SOLID,
            bd=1,
            fg="#999999",
        )
        self.text_input.pack(fill=tk.X, pady=(8, 6))
        self.text_input.insert("1.0", _PLACEHOLDER)
        self.text_input.bind("<FocusIn>", self._on_focus_in)
        self.text_input.bind("<FocusOut>", self._on_focus_out)

        if _HAS_DND:
            self.text_input.drop_target_register(DND_FILES)
            self.text_input.dnd_bind("<<Drop>>", self._on_drop)

        btn_row = tk.Frame(top)
        btn_row.pack(anchor="w", pady=(0, 4))

        tk.Button(
            btn_row,
            text="🚀 開始下載",
            command=self._start_download,
            bg="#1976D2",
            fg="white",
            padx=12,
            pady=6,
            font=("Microsoft JhengHei UI", 10, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            btn_row,
            text="🧹 清空輸入框",
            command=self._clear_input,
            padx=10,
            pady=6,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="📂 匯入 txt（自動預處理）",
            command=self._import_txt,
            bg="#6A1B9A",
            fg="white",
            padx=10,
            pady=6,
            font=("Microsoft JhengHei UI", 9),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="🧩 預處理分類",
            command=self._preprocess_links,
            bg="#00897B",
            fg="white",
            padx=10,
            pady=6,
            font=("Microsoft JhengHei UI", 9),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="📥 載入可下載清單",
            command=self._load_preprocessed_downloads,
            bg="#5E35B1",
            fg="white",
            padx=10,
            pady=6,
            font=("Microsoft JhengHei UI", 9),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="📁 開啟下載資料夾",
            command=self._open_downloads,
            padx=10,
            pady=6,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="📂 開啟預處理 output",
            command=self._open_preprocess_output,
            bg="#37474F",
            fg="white",
            padx=10,
            pady=6,
            font=("Microsoft JhengHei UI", 9),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_row,
            text="🧼 清除已下載紀錄",
            command=self._clear_processed_log,
            bg="#455A64",
            fg="white",
            padx=10,
            pady=6,
            font=("Microsoft JhengHei UI", 9),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.login_btn = tk.Button(
            btn_row,
            text="🔑 登入 IG",
            command=self._manual_login,
            bg="#5C6BC0",
            fg="white",
            padx=10,
            pady=6,
            font=("Microsoft JhengHei UI", 9),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.login_btn.pack(side=tk.LEFT)

        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=8, pady=(0, 4))

        mid = tk.Frame(self.root, padx=10)
        mid.pack(fill=tk.BOTH, expand=True)

        cols = ("url", "status", "retry")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("url", text="URL")
        self.tree.heading("status", text="狀態", anchor="center")
        self.tree.heading("retry", text="Retry", anchor="center")
        self.tree.column("url", width=720, stretch=True, minwidth=320)
        self.tree.column("status", width=160, stretch=False, anchor="center")
        self.tree.column("retry", width=70, stretch=False, anchor="center")

        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(mid, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        mid.grid_rowconfigure(0, weight=1)
        mid.grid_columnconfigure(0, weight=1)

        for status, color in _STATUS_COLORS.items():
            self.tree.tag_configure(status, foreground=color)

        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=8, pady=(4, 0))

        bot = tk.Frame(self.root, padx=10, pady=6)
        bot.pack(fill=tk.X)

        filter_frame = tk.LabelFrame(bot, text="篩選", padx=4, pady=2)
        filter_frame.pack(side=tk.LEFT)

        filter_defs = [
            ("全部", "ALL"),
            ("SUCCESS", "SUCCESS"),
            ("FAILED", "FAILED"),
            ("BLOCKED", "BLOCKED"),
            ("DOWNLOADING", "DOWNLOADING"),
        ]
        for label, key in filter_defs:
            color = _STATUS_COLORS.get(key, "#333333")
            btn_width = 11
            if key == "DOWNLOADING":
                btn_width = 14
            tk.Button(
                filter_frame,
                text=label,
                width=btn_width,
                fg=color if key != "ALL" else "#333333",
                font=("Microsoft JhengHei UI", 9),
                command=lambda k=key: self._set_filter(k),
                relief=tk.FLAT,
                cursor="hand2",
            ).pack(side=tk.LEFT, padx=2, pady=1)

        act_frame = tk.Frame(bot)
        act_frame.pack(side=tk.RIGHT)

        tk.Button(
            act_frame,
            text="📄 查看失敗",
            command=self._show_failed_links_window,
            bg="#6D4C41",
            fg="white",
            padx=10,
            pady=5,
            font=("Microsoft JhengHei UI", 10),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=4)

        self.pause_btn = tk.Button(
            act_frame,
            text="⏸ 暫停",
            command=self._pause_downloads,
            bg="#546E7A",
            fg="white",
            padx=10,
            pady=5,
            font=("Microsoft JhengHei UI", 10),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.pause_btn.pack(side=tk.LEFT, padx=4)

        self.resume_btn = tk.Button(
            act_frame,
            text="▶ 繼續",
            command=self._resume_downloads,
            bg="#2E7D32",
            fg="white",
            padx=10,
            pady=5,
            font=("Microsoft JhengHei UI", 10),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.resume_btn.pack(side=tk.LEFT, padx=4)

        self.stop_btn = tk.Button(
            act_frame,
            text="⏹ 停止",
            command=self._stop_downloads,
            bg="#8E24AA",
            fg="white",
            padx=10,
            pady=5,
            font=("Microsoft JhengHei UI", 10),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.stop_btn.pack(side=tk.LEFT, padx=4)

        tk.Button(
            act_frame,
            text="🔁 重試失敗",
            command=self._retry_failed,
            bg="#E65100",
            fg="white",
            padx=10,
            pady=5,
            font=("Microsoft JhengHei UI", 10),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            act_frame,
            text="🗑 清空任務",
            command=self._clear_tasks,
            bg="#C62828",
            fg="white",
            padx=10,
            pady=5,
            font=("Microsoft JhengHei UI", 10),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="就緒")
        tk.Label(
            self.root,
            textvariable=self.status_var,
            anchor="w",
            relief=tk.SUNKEN,
            bd=1,
            font=("Microsoft JhengHei UI", 9),
            fg="#333333",
            padx=6,
        ).pack(fill=tk.X, side=tk.BOTTOM)

    def _init_session(self):
        if os.path.exists(COOKIES_FILE):
            instagram.use_cookies(COOKIES_FILE)
            self.status_var.set("使用 cookies.txt 模式  •  點擊「🔑 登入 IG」可切換帳號登入")
        else:
            instagram.setup()
            self.status_var.set("匿名模式  •  點擊「🔑 登入 IG」可登入帳號")

    def _manual_login(self):
        if self._login_in_progress:
            messagebox.showinfo("提示", "目前正在登入中，請稍候。", parent=self.root)
            return

        accounts = _load_accounts()
        if not accounts:
            messagebox.showwarning(
                "找不到帳號",
                f"請先在以下檔案填入 IG 帳號密碼：\n{ACCOUNTS_FILE}",
                parent=self.root,
            )
            return

        if len(accounts) == 1:
            self._do_login(accounts[0])
            return

        win = tk.Toplevel(self.root)
        win.title("選擇帳號")
        win.grab_set()
        win.resizable(False, False)
        win.geometry("320x210")

        tk.Label(
            win,
            text="選擇要登入的帳號：",
            font=("Microsoft JhengHei UI", 11),
            pady=10,
        ).pack()

        lb = tk.Listbox(win, width=36, height=min(len(accounts), 6), font=("Consolas", 10))
        for a in accounts:
            lb.insert(tk.END, a["username"])
        lb.select_set(0)
        lb.pack(padx=20, pady=(0, 6))

        def _confirm():
            sel = lb.curselection()
            if sel:
                self._do_login(accounts[sel[0]])
            win.destroy()

        btn_r = tk.Frame(win)
        btn_r.pack(pady=6)
        tk.Button(
            btn_r,
            text="確認",
            command=_confirm,
            padx=18,
            pady=4,
            bg="#1976D2",
            fg="white",
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=4)
        tk.Button(
            btn_r,
            text="取消",
            command=win.destroy,
            padx=18,
            pady=4,
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=4)

    def _do_login(self, account: dict):
        if self._login_in_progress:
            return

        self._login_in_progress = True
        self.login_btn.config(state=tk.DISABLED)

        username = account["username"]
        password = account["password"]
        self.status_var.set(f"登入中：{username}...")
        self.root.update_idletasks()

        evt = threading.Event()
        code_box = [None]

        def get_code_fn():
            self.root.after(0, show_2fa_dialog)
            evt.wait(timeout=120)
            evt.clear()
            return code_box[0]

        def show_2fa_dialog():
            code = simpledialog.askstring(
                "兩步驟驗證",
                f"IG 要求驗證碼（{username}）\n"
                "請輸入收到的 6 位數代碼：\n\n"
                "⚠️ 請輸入最新收到的那一組\n"
                "⚠️ 若前面已經重送過驗證碼，舊碼會失效",
                parent=self.root,
            )
            code_box[0] = code
            evt.set()

        def run_login():
            try:
                instagram.login_with_retry(username, password, get_code_fn)
                self.root.after(0, lambda: self._on_login_success(username))
            except Exception as e:
                self.root.after(0, lambda: self._on_login_error(str(e)))
            finally:
                self.root.after(0, self._reset_login_state)

        threading.Thread(target=run_login, daemon=True).start()

    def _on_login_success(self, username: str):
        self.status_var.set(f"✅ 已登入：{username}")

    def _on_login_error(self, msg: str):
        messagebox.showerror(
            "登入失敗",
            "Instagram 登入失敗，請確認：\n"
            "1. 帳號密碼是否正確\n"
            "2. 請輸入最新收到的那一組驗證碼\n"
            "3. 若 IG 要求裝置驗證，需先在手機 App / 瀏覽器確認\n"
            "4. 若帳號可在瀏覽器正常登入，建議優先使用 cookies.txt，穩定度通常高於帳密 + 2FA。\n\n"
            f"詳細錯誤：{msg}",
            parent=self.root,
        )
        if os.path.exists(COOKIES_FILE):
            instagram.use_cookies(COOKIES_FILE)
            self.status_var.set("登入失敗，已回退至 cookies.txt 模式")

    def _reset_login_state(self):
        self._login_in_progress = False
        self.login_btn.config(state=tk.NORMAL)

    def _start_download(self):
        content = self.text_input.get("1.0", tk.END).strip()
        if not content or content == _PLACEHOLDER:
            messagebox.showwarning("提示", "請先貼入 URL 或匯入 txt 檔案。", parent=self.root)
            return

        urls = _extract_urls(content)
        if not urls:
            messagebox.showwarning("提示", "未找到有效的 URL（需以 http/https 開頭）。", parent=self.root)
            return

        result = queue_manager.add_tasks(urls)

        snapshot = queue_manager.get_snapshot()
        if snapshot and self._elapsed_start_ts is None:
            self._elapsed_start_ts = time.time()

        # 新增任務後重置完成提示狀態
        self._completion_notified = False
        self._last_total_for_completion = 0

        self.status_var.set(
            f"新增 {result['added']} 筆  |  "
            f"略過已下載 {result['skipped_processed']} 筆  |  "
            f"略過重複 {result['skipped_duplicate']} 筆"
        )
        self._refresh_table()

    def _clear_input(self):
        self.text_input.delete("1.0", tk.END)
        self.text_input.insert("1.0", _PLACEHOLDER)
        self.text_input.config(fg="#999999")

    def _clear_processed_log(self):
        count = queue_manager.get_processed_count()
        if count <= 0:
            messagebox.showinfo("提示", "processed_links.log 目前是空的。", parent=self.root)
            return

        if not messagebox.askyesno(
            "確認清除",
            f"確定要清除已下載紀錄嗎？\n\n目前共有 {count} 筆已下載 checkpoint。\n清除後，先前被略過的連結可重新加入下載。",
            parent=self.root,
        ):
            return

        queue_manager.clear_checkpoint()
        self.status_var.set("已清除 processed_links.log，之後可重新加入先前被略過的連結。")
        messagebox.showinfo(
            "完成",
            "已清除 processed_links.log。\n先前被略過的連結，現在可以重新加入下載。",
            parent=self.root,
        )

    def _import_txt(self):
        path = filedialog.askopenfilename(
            title="選擇 URL 清單",
            filetypes=[("文字檔", "*.txt"), ("所有檔案", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("錯誤", f"無法讀取檔案：{e}", parent=self.root)
            return

        urls = _extract_urls(content)

        if not urls:
            messagebox.showwarning("提示", "此檔案內沒有找到有效 URL。", parent=self.root)
            return

        raw_lines = [line.strip() for line in content.splitlines() if line.strip()]
        pure_url_lines = [line for line in raw_lines if re.fullmatch(r'https?://[^\s]+', line)]
        is_pure_url_file = len(raw_lines) > 0 and len(raw_lines) == len(pure_url_lines)

        if is_pure_url_file:
            self.text_input.delete("1.0", tk.END)
            self.text_input.insert("1.0", "\n".join(urls))
            self.text_input.config(fg="#000000")
            self.status_var.set(f"已直接載入純 URL 清單：{os.path.basename(path)}")
            return

        if link_sorter is None:
            self.text_input.delete("1.0", tk.END)
            self.text_input.insert("1.0", "\n".join(urls))
            self.text_input.config(fg="#000000")
            self.status_var.set(f"link_sorter.py 無法載入，已直接抽出 URL：{os.path.basename(path)}")
            return

        try:
            input_file, download_path, undownload_path, stats = link_sorter.sort_links(
                input_file=path,
                base_dir=PREPROCESS_DIR,
                output_dir=PREPROCESS_OUTPUT_DIR,
            )
        except Exception as e:
            messagebox.showerror("預處理失敗", str(e), parent=self.root)
            return

        try:
            with open(download_path, "r", encoding="utf-8", errors="ignore") as f:
                processed = f.read().strip()
        except Exception as e:
            messagebox.showerror("錯誤", f"無法讀取預處理結果：{e}", parent=self.root)
            return

        self.text_input.delete("1.0", tk.END)
        self.text_input.insert("1.0", processed if processed else "")
        self.text_input.config(fg="#000000")

        self.status_var.set(
            f"已完成預處理：可下載 {stats['downloadable']} 筆，不可下載 {stats['undownloadable']} 筆"
        )

        messagebox.showinfo(
            "匯入完成",
            f"已自動預處理並載入可下載清單。\n\n"
            f"來源：{os.path.basename(input_file)}\n"
            f"可下載：{stats['downloadable']} 筆\n"
            f"不可下載：{stats['undownloadable']} 筆\n\n"
            f"download_link.txt：\n{download_path}\n\n"
            f"undownload_link.txt：\n{undownload_path}",
            parent=self.root,
        )

    def _preprocess_links(self):
        if link_sorter is None:
            messagebox.showerror("錯誤", "無法載入 pre-processing/link_sorter.py", parent=self.root)
            return

        try:
            input_file, download_path, undownload_path, stats = link_sorter.sort_links(
                input_file=None,
                base_dir=PREPROCESS_DIR,
                output_dir=PREPROCESS_OUTPUT_DIR,
            )
        except Exception as e:
            messagebox.showerror("預處理失敗", str(e), parent=self.root)
            return

        self._load_preprocessed_downloads(silent=True)

        messagebox.showinfo(
            "預處理完成",
            f"來源檔案：{os.path.basename(input_file)}\n"
            f"可下載：{stats['downloadable']} 筆\n"
            f"不可下載：{stats['undownloadable']} 筆\n\n"
            f"download_link.txt：\n{download_path}\n\n"
            f"undownload_link.txt：\n{undownload_path}",
            parent=self.root,
        )

    def _load_preprocessed_downloads(self, silent=False):
        if not os.path.exists(PREPROCESS_DEFAULT_DOWNLOAD):
            if not silent:
                messagebox.showwarning(
                    "找不到檔案",
                    f"尚未產生 download_link.txt：\n{PREPROCESS_DEFAULT_DOWNLOAD}",
                    parent=self.root,
                )
            return

        try:
            with open(PREPROCESS_DEFAULT_DOWNLOAD, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read().strip()
            self.text_input.delete("1.0", tk.END)
            self.text_input.insert("1.0", content if content else "")
            self.text_input.config(fg="#000000")
            self.status_var.set(f"已載入可下載清單：{PREPROCESS_DEFAULT_DOWNLOAD}")
        except Exception as e:
            if not silent:
                messagebox.showerror("錯誤", f"無法載入 download_link.txt：{e}", parent=self.root)

    def _open_path(self, path: str):
        os.makedirs(path, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import subprocess
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def _open_downloads(self):
        self._open_path(DOWNLOAD_DIR)

    def _open_preprocess_output(self):
        self._open_path(PREPROCESS_OUTPUT_DIR)
        self.status_var.set(f"已開啟預處理 output：{PREPROCESS_OUTPUT_DIR}")

    def _pause_downloads(self):
        worker.pause()
        self.status_var.set("已暫停下載")

    def _resume_downloads(self):
        worker.resume()
        self.status_var.set("已繼續下載")

    def _stop_downloads(self):
        if not messagebox.askyesno("確認停止", "停止下載？\n正在下載中的這一筆會先安全收尾。", parent=self.root):
            return
        worker.stop()
        self.status_var.set("停止中，等待目前任務安全收尾...")

    def _retry_failed(self):
        queue_manager.retry_failed()
        self._completion_notified = False
        self._last_total_for_completion = 0
        self.status_var.set("已重置失敗任務，重新下載中...")
        self._schedule_refresh(0)

    def _clear_tasks(self):
        if not messagebox.askyesno("確認清空", "清空所有任務記錄？\n（已下載的檔案不受影響）", parent=self.root):
            return
        queue_manager.write_logs()
        queue_manager.clear_tasks()
        self._blocked_warned = False
        self._elapsed_start_ts = None
        self._completion_notified = False
        self._last_total_for_completion = 0
        self._schedule_refresh(0)
        self.status_var.set("任務已清空，日誌已儲存至 data/")

    def _show_failed_links_window(self):
        if self._failed_window and self._failed_window.winfo_exists():
            self._failed_window.lift()
            self._failed_window.focus_force()
            self._refresh_failed_window_text()
            return

        self._failed_window = tk.Toplevel(self.root)
        self._failed_window.title("失敗連結清單")
        self._failed_window.geometry("900x520")

        top = tk.Frame(self._failed_window, padx=10, pady=10)
        top.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            top,
            text="失敗 / 封鎖 / 不可用 連結",
            font=("Microsoft JhengHei UI", 14, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        self.failed_text = tk.Text(
            top,
            wrap=tk.WORD,
            font=("Consolas", 10),
            relief=tk.SOLID,
            bd=1,
        )
        self.failed_text.pack(fill=tk.BOTH, expand=True)

        btns = tk.Frame(top)
        btns.pack(fill=tk.X, pady=(8, 0))

        tk.Button(
            btns,
            text="🔄 重新整理",
            command=self._refresh_failed_window_text,
            bg="#1976D2",
            fg="white",
            padx=10,
            pady=4,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            btns,
            text="📋 複製全部",
            command=self._copy_failed_text,
            bg="#6A1B9A",
            fg="white",
            padx=10,
            pady=4,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=4)

        tk.Button(
            btns,
            text="關閉",
            command=self._failed_window.destroy,
            padx=10,
            pady=4,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        self._refresh_failed_window_text()

    def _refresh_failed_window_text(self):
        if not hasattr(self, "failed_text"):
            return
        content = queue_manager.get_failed_links_text()
        self.failed_text.delete("1.0", tk.END)
        self.failed_text.insert("1.0", content)

    def _copy_failed_text(self):
        if not hasattr(self, "failed_text"):
            return
        content = self.failed_text.get("1.0", tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_var.set("已複製失敗連結清單")

    def _set_filter(self, key: str):
        self._active_filter = key
        self._schedule_refresh(0)

    def _schedule_refresh(self, delay: int = 1000):
        if self._refresh_id is not None:
            self.root.after_cancel(self._refresh_id)
            self._refresh_id = None
        self._refresh_id = self.root.after(delay, self._refresh_table)

    def _maybe_show_completion_popup(self, total: int, done: int, counts: dict, elapsed: float, phase: str = ""):
        """批次完成後彈出一次完成視窗。"""
        if total <= 0:
            self._completion_notified = False
            self._last_total_for_completion = 0
            return

        # 如果這批任務數量變了，視為新批次，允許再次提示。
        if total != self._last_total_for_completion:
            self._last_total_for_completion = total
            self._completion_notified = False

        if self._completion_notified:
            return

        active_states = (
            counts.get("PENDING", 0)
            + counts.get("DOWNLOADING", 0)
        )
        # v5.2: 不在 DOWNLOADING/COOLDOWN 階段彈窗，避免干擾 worker 冷卻或造成 GUI 看似卡住。
        if done < total or active_states > 0 or phase in ("DOWNLOADING", "COOLDOWN", "PAUSED"):
            return

        self._completion_notified = True
        self.root.after(100, lambda: self._show_completion_popup(total, counts, elapsed))

    def _show_completion_popup(self, total: int, counts: dict, elapsed: float):
        """顯示下載完成摘要視窗。"""
        if not self.root.winfo_exists():
            return

        success = counts.get("SUCCESS", 0)
        failed = counts.get("FAILED", 0)
        blocked = counts.get("BLOCKED", 0)
        unavailable = counts.get("UNAVAILABLE", 0)
        retry = counts.get("RETRY", 0)

        msg = (
            "下載批次已完成。\n\n"
            f"總任務：{total}\n"
            f"成功：{success}\n"
            f"失敗：{failed}\n"
            f"封鎖：{blocked}\n"
            f"不可用：{unavailable}\n"
            f"待重試：{retry}\n"
            f"耗時：{_format_seconds(elapsed)}"
        )

        win = tk.Toplevel(self.root)
        win.title("下載完成")
        win.geometry("420x300")
        win.resizable(False, False)
        # v5.2: 非 modal 視窗，不 grab_set，避免彈窗搶焦點造成主程式看似 hang。
        win.transient(self.root)
        try:
            win.attributes("-topmost", True)
            win.after(1500, lambda: win.attributes("-topmost", False) if win.winfo_exists() else None)
        except Exception:
            pass

        frame = tk.Frame(win, padx=20, pady=18)
        frame.pack(fill=tk.BOTH, expand=True)

        title_color = "#2E7D32" if failed == 0 and blocked == 0 and unavailable == 0 and retry == 0 else "#E65100"

        tk.Label(
            frame,
            text="✅ 下載完成",
            font=("Microsoft JhengHei UI", 18, "bold"),
            fg=title_color,
        ).pack(anchor="w", pady=(0, 12))

        tk.Label(
            frame,
            text=msg,
            font=("Microsoft JhengHei UI", 11),
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)

        btn_row = tk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(18, 0))

        tk.Button(
            btn_row,
            text="📁 開啟下載資料夾",
            command=lambda: self._open_path(DOWNLOAD_DIR),
            bg="#1976D2",
            fg="white",
            padx=12,
            pady=6,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 8))

        if failed or blocked or unavailable or retry:
            tk.Button(
                btn_row,
                text="📄 查看失敗",
                command=self._show_failed_links_window,
                bg="#6D4C41",
                fg="white",
                padx=12,
                pady=6,
                relief=tk.FLAT,
                cursor="hand2",
            ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            btn_row,
            text="關閉",
            command=win.destroy,
            padx=14,
            pady=6,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.focus_force()

    def _refresh_table(self):
        self._refresh_id = None
        snapshot = queue_manager.get_snapshot()
        runtime = queue_manager.get_runtime()
        processed_count = queue_manager.get_processed_count()

        display = snapshot if self._active_filter == "ALL" else [t for t in snapshot if t["status"] == self._active_filter]

        for row in self.tree.get_children():
            self.tree.delete(row)

        for t in display:
            url_short = t["url"][:120] + ("…" if len(t["url"]) > 120 else "")
            status = t["status"]
            tag = status if status in _STATUS_COLORS else "PENDING"
            self.tree.insert("", tk.END, values=(url_short, status, t["retry"]), tags=(tag,))

        total = runtime.get("total", 0)
        done = runtime.get("done", 0)

        self.progress_label_var.set(f"進度：{done} / {total}")

        if total > 0:
            self.progress["mode"] = "determinate"
            self.progress["maximum"] = total
            self.progress["value"] = done
        else:
            self.progress["mode"] = "determinate"
            self.progress["maximum"] = 100
            self.progress["value"] = 0

        if total > 0 and self._elapsed_start_ts is None:
            self._elapsed_start_ts = time.time()

        if total == 0:
            self._elapsed_start_ts = None
            elapsed = 0
            remaining = None
        else:
            elapsed = time.time() - self._elapsed_start_ts if self._elapsed_start_ts else 0
            if done > 0:
                avg = elapsed / done
                remaining = avg * (total - done)
            else:
                remaining = None

        self.elapsed_var.set(f"Elapsed: {_format_seconds(elapsed)}")
        self.remaining_var.set(f"Remaining: {_format_seconds(remaining)}")

        phase = runtime.get("phase", "IDLE")
        message = runtime.get("message", "就緒")
        active_url = runtime.get("active_url", "")
        cooldown_remaining = runtime.get("cooldown_remaining", 0)

        self.phase_var.set(f"目前狀態：{message}")
        self.active_url_var.set(f"目前 URL：{active_url}" if active_url else "目前 URL：")

        if phase == "COOLDOWN" and cooldown_remaining > 0:
            self.cooldown_var.set(f"冷卻等待中：剩餘 {cooldown_remaining} 秒")
        else:
            self.cooldown_var.set("")

        counts = {}
        for t in snapshot:
            counts[t["status"]] = counts.get(t["status"], 0) + 1

        parts = [f"共 {len(snapshot)} 筆", f"已下載紀錄 {processed_count} 筆"]
        for s in ("DOWNLOADING", "PENDING", "SUCCESS", "FAILED", "BLOCKED", "RETRY", "UNAVAILABLE"):
            if counts.get(s, 0):
                parts.append(f"{s}: {counts[s]}")

        self._maybe_show_completion_popup(total, done, counts, elapsed, phase)

        blocked_n = counts.get("BLOCKED", 0)
        if blocked_n > 0 and not self._blocked_warned:
            self._blocked_warned = True
            parts.append("⚠️ BLOCKED 代表內容受限；即使有 cookies，若帳號本身沒權限仍會失敗")

        self.status_var.set("  |  ".join(parts))

        if self._failed_window and self._failed_window.winfo_exists():
            self._refresh_failed_window_text()

        self._schedule_refresh(1000)

    def _on_focus_in(self, _event):
        if self.text_input.get("1.0", tk.END).strip() == _PLACEHOLDER:
            self.text_input.delete("1.0", tk.END)
            self.text_input.config(fg="#000000")

    def _on_focus_out(self, _event):
        if not self.text_input.get("1.0", tk.END).strip():
            self.text_input.insert("1.0", _PLACEHOLDER)
            self.text_input.config(fg="#999999")

    def _on_drop(self, event):
        path = event.data.strip()
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]

        if path.lower().endswith(".txt"):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                self.text_input.delete("1.0", tk.END)
                self.text_input.insert("1.0", content)
                self.text_input.config(fg="#000000")
            except Exception as e:
                messagebox.showerror("錯誤", f"無法讀取拖入的檔案：{e}", parent=self.root)
        else:
            current = self.text_input.get("1.0", tk.END).strip()
            if current == _PLACEHOLDER:
                self.text_input.delete("1.0", tk.END)
                self.text_input.config(fg="#000000")
            self.text_input.insert(tk.END, ("\n" if current else "") + path)


def _load_accounts() -> list:
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return [
            a for a in raw
            if a.get("username")
            and a.get("password")
            and "your_ig_account" not in a["username"]
        ]
    except Exception:
        return []


def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PREPROCESS_OUTPUT_DIR, exist_ok=True)

    root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
    root.lift()
    root.focus_force()

    App(root)
    root.protocol("WM_DELETE_WINDOW", lambda: _on_close(root))
    root.mainloop()


def _on_close(root: tk.Tk):
    worker.stop()
    queue_manager.write_logs()
    root.destroy()


if __name__ == "__main__":
    main()