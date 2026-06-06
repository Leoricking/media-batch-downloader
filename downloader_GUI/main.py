import json
import math
import os
import re
import sys
import time
import threading
import ctypes
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
    "MISSING": "#607D8B",
}

_PLACEHOLDER = "在此貼上 URL（每行一個），或拖入 txt 檔案..."



def _enable_dpi_awareness():
    """Enable Windows DPI awareness before Tk is created.

    This keeps the GUI from becoming either huge and blurry or tiny on mixed
    FHD / 2K / 4K monitors.  Safe no-op on non-Windows systems.
    """
    if sys.platform != "win32":
        return
    try:
        # Windows 8.1+ per-monitor DPI awareness.
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _get_work_area(root: tk.Tk) -> tuple[int, int, int, int]:
    """Return usable desktop work area: left, top, width, height.

    On Windows this excludes the taskbar.  The fallback uses Tk's screen size.
    """
    try:
        if sys.platform == "win32":
            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = RECT()
            SPI_GETWORKAREA = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                w = max(800, int(rect.right - rect.left))
                h = max(600, int(rect.bottom - rect.top))
                return int(rect.left), int(rect.top), w, h
    except Exception:
        pass

    try:
        return 0, 0, int(root.winfo_screenwidth()), int(root.winfo_screenheight())
    except Exception:
        return 0, 0, 1280, 720


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
        self._configure_adaptive_window()

        self._active_filter = "ALL"
        self._refresh_id = None
        self._blocked_warned = False
        self._login_in_progress = False
        self._failed_window = None

        # Treeview row id -> full task url.
        # The visible URL column is shortened for readability, but right-click copy
        # and Ctrl+C must copy the original full URL.
        self._tree_url_by_iid = {}
        self._tree_context_iid = None
        self._tree_context_url_snapshot = ""
        self._tree_context_status_snapshot = ""
        self._tree_context_retry_snapshot = ""
        self._tree_context_menu = None
        self._tree_sort_col = None
        self._tree_sort_reverse = False
        self._tree_heading_base = {
            "url": "URL",
            "status": "狀態",
            "retry": "Retry",
        }

        self._elapsed_start_ts = None
        self._completion_notified = False
        self._last_completion_signature = ""

        self._build_ui()
        queue_manager.load_checkpoint()
        worker.start()

        self.root.after(300, self._init_session)
        self.root.after(600, self._refresh_table)


    def _configure_adaptive_window(self):
        """Size and scale the GUI for FHD / 2K / 4K monitors.

        v11.39 fixed the FHD bottom clipping by using almost the full work area,
        but on 4K that made the window enormous while fonts stayed visually too
        small.  This version caps the default window size on high-resolution
        monitors and uses correct Tk DPI scaling.
        """
        left, top, work_w, work_h = _get_work_area(self.root)

        # Use the real Tk point scaling.  At 96 DPI this is about 1.333.
        try:
            dpi = float(self.root.winfo_fpixels("1i") or 96.0)
            tk_scale = max(1.25, min(2.10, dpi / 72.0))
            self.root.tk.call("tk", "scaling", tk_scale)
        except Exception:
            tk_scale = 1.333

        # Default window size should be comfortable, not full-screen, on 4K.
        if work_w >= 3200 or work_h >= 1700:
            # 4K / ultra-wide: keep a readable desktop app sized window.
            width = min(1900, max(1500, int(work_w * 0.52)))
            height = min(1120, max(900, int(work_h * 0.56)))
            self._compact_ui = False
            self._text_input_height = 5
            self._top_pad_y = 10
            self._button_pady = 7
            self._title_font_size = 20
            self._base_font_size = 11
            self._small_font_size = 10
            self._tree_rowheight = 34
            self._ui_scale = 1.18
        elif work_w >= 2400 or work_h >= 1300:
            # 2K / high-DPI laptop external monitor.
            width = min(1650, max(1300, int(work_w * 0.66)))
            height = min(1000, max(820, int(work_h * 0.72)))
            self._compact_ui = False
            self._text_input_height = 5
            self._top_pad_y = 8
            self._button_pady = 6
            self._title_font_size = 19
            self._base_font_size = 10
            self._small_font_size = 9
            self._tree_rowheight = 30
            self._ui_scale = 1.06
        else:
            # FHD and smaller: fit within the work area and keep bottom buttons visible.
            width = min(max(1180, int(work_w * 0.96)), max(980, work_w - 24))
            height = min(max(720, int(work_h * 0.90)), max(620, work_h - 36))
            self._compact_ui = work_h <= 950 or work_w <= 1400
            self._text_input_height = 4 if self._compact_ui else 5
            self._top_pad_y = 6 if self._compact_ui else 8
            self._button_pady = 4 if self._compact_ui else 6
            self._title_font_size = 16 if self._compact_ui else 18
            self._base_font_size = 10
            self._small_font_size = 9
            if self._compact_ui:
                self._title_font_size = 15
                self._base_font_size = 9
                self._small_font_size = 8
                self._text_input_height = 3
                self._button_pady = 3
                self._tree_rowheight = 22
                self._ui_scale = 0.90
            else:
                self._tree_rowheight = 26
                self._ui_scale = 1.00

        width = max(980, min(width, work_w - 16 if work_w > 1000 else work_w))
        height = max(620, min(height, work_h - 16 if work_h > 700 else work_h))
        x = left + max(0, (work_w - width) // 2)
        y = top + max(0, (work_h - height) // 2)

        self.root.geometry(f"{width}x{height}+{x}+{y}")
        min_w = 1120 if self._compact_ui else 1180
        min_h = min(680, max(540, work_h - 80)) if self._compact_ui else min(700, max(560, work_h - 80))
        self.root.minsize(min_w, min_h)

        # ttk style tuning must happen after Tk exists.  It keeps Treeview rows
        # readable on 4K without bloating the whole window on FHD.
        # Centralized pixel metrics.  The UI uses these values instead of a mix
        # of fixed constants so fonts, button padding, frame padding and row
        # heights scale together on FHD / 2K / 4K / Windows 125%-175% setups.
        self._pad_x = self._ui_px(10)
        self._pad_y = self._ui_px(6)
        self._gap_x = self._ui_px(3)
        self._gap_y = self._ui_px(4)
        self._button_padx = self._ui_px(6)
        self._button_pady_main = self._ui_px(5)
        self._bottom_button_pady = self._ui_px(4)
        self._status_padx = self._ui_px(6)
        self._filter_width = 8 if self._compact_ui else 9
        self._filter_downloading_width = 10 if self._compact_ui else 12
        self._url_col_width = self._ui_px(620)
        self._status_col_width = self._ui_px(110)
        self._retry_col_width = self._ui_px(58)

        # ttk style tuning must happen after Tk exists.  It keeps Treeview rows
        # readable on 4K without bloating the whole window on FHD.
        try:
            style = ttk.Style(self.root)
            style.configure("Treeview", rowheight=self._tree_rowheight, font=("Microsoft JhengHei UI", self._small_font_size))
            style.configure("Treeview.Heading", font=("Microsoft JhengHei UI", self._small_font_size, "bold"))
        except Exception:
            pass

    def _ui_px(self, value: int, minimum: int = 1) -> int:
        """Scale integer pixel values with the current UI scale."""
        scale = float(getattr(self, "_ui_scale", 1.0) or 1.0)
        return max(minimum, int(round(value * scale)))

    def _build_ui(self):
        top = tk.Frame(self.root, padx=self._pad_x, pady=self._top_pad_y)
        top.pack(fill=tk.X)

        tk.Label(
            top,
            text="Media Batch Downloader",
            font=("Microsoft JhengHei UI", self._title_font_size, "bold"),
        ).pack(anchor="w")

        hint = "支援 Instagram / Facebook  •  可貼 URL 或拖入 .txt  •  匯入 txt 會自動預處理"
        if not _HAS_DND:
            hint += "  （安裝 tkinterdnd2 可啟用拖放）"
        tk.Label(
            top,
            text=hint,
            font=("Microsoft JhengHei UI", self._base_font_size),
            fg="#666666",
        ).pack(anchor="w")

        progress_frame = tk.Frame(top)
        progress_frame.pack(fill=tk.X, pady=(self._ui_px(8), self._ui_px(4)))

        self.progress_label_var = tk.StringVar(value="進度：0 / 0")
        tk.Label(
            progress_frame,
            textvariable=self.progress_label_var,
            font=("Microsoft JhengHei UI", self._base_font_size, "bold"),
            fg="#333333",
        ).pack(anchor="w")

        self.progress = ttk.Progressbar(
            progress_frame,
            orient="horizontal",
            mode="determinate",
            maximum=100,
            value=0,
        )
        self.progress.pack(fill=tk.X, pady=(self._ui_px(4), self._ui_px(2)))

        self.phase_var = tk.StringVar(value="目前狀態：就緒")
        tk.Label(
            progress_frame,
            textvariable=self.phase_var,
            font=("Microsoft JhengHei UI", self._small_font_size),
            fg="#444444",
        ).pack(anchor="w")

        self.active_url_var = tk.StringVar(value="目前 URL：")
        tk.Label(
            progress_frame,
            textvariable=self.active_url_var,
            font=("Consolas", self._small_font_size),
            fg="#666666",
        ).pack(anchor="w")

        self.cooldown_var = tk.StringVar(value="")
        tk.Label(
            progress_frame,
            textvariable=self.cooldown_var,
            font=("Microsoft JhengHei UI", self._base_font_size, "bold"),
            fg="#E65100",
        ).pack(anchor="w")

        time_frame = tk.Frame(progress_frame)
        time_frame.pack(anchor="w", pady=(self._ui_px(2), 0))

        self.elapsed_var = tk.StringVar(value="Elapsed: 00:00")
        self.remaining_var = tk.StringVar(value="Remaining: --:--")

        tk.Label(
            time_frame,
            textvariable=self.elapsed_var,
            font=("Microsoft JhengHei UI", self._base_font_size, "bold"),
            fg="#1976D2",
        ).pack(side=tk.LEFT, padx=(0, self._ui_px(20)))

        tk.Label(
            time_frame,
            textvariable=self.remaining_var,
            font=("Microsoft JhengHei UI", self._base_font_size, "bold"),
            fg="#2E7D32",
        ).pack(side=tk.LEFT)

        self.text_input = tk.Text(
            top,
            height=self._text_input_height,
            wrap=tk.WORD,
            font=("Consolas", self._base_font_size + 1),
            relief=tk.SOLID,
            bd=1,
            fg="#999999",
        )
        self.text_input.pack(fill=tk.X, pady=(self._ui_px(8), self._ui_px(6)))
        self.text_input.insert("1.0", _PLACEHOLDER)
        self.text_input.bind("<FocusIn>", self._on_focus_in)
        self.text_input.bind("<FocusOut>", self._on_focus_out)

        if _HAS_DND:
            self.text_input.drop_target_register(DND_FILES)
            self.text_input.dnd_bind("<<Drop>>", self._on_drop)

        btn_area = tk.Frame(top)
        btn_area.pack(fill=tk.X, pady=(0, self._ui_px(4)))
        btn_row = tk.Frame(btn_area)
        btn_row.pack(anchor="w", fill=tk.X)
        # Keep the original single-row toolbar look.  Instead of wrapping into
        # an ugly second row, use shorter labels and smaller padding so the
        # toolbar fits typical FHD windows without covering the Treeview.
        btn_row2 = btn_row

        tk.Button(
            btn_row,
            text="🚀 開始下載",
            command=self._start_download,
            bg="#1976D2",
            fg="white",
            padx=self._ui_px(12),
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._base_font_size, "bold"),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(
            btn_row,
            text="🧹 清空",
            command=self._clear_input,
            bg="#F57C00",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="📂 匯入txt",
            command=self._import_txt,
            bg="#6A1B9A",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="🧩 預處理",
            command=self._preprocess_links,
            bg="#00897B",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="📥 載入清單",
            command=self._load_preprocessed_downloads,
            bg="#5E35B1",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="📁 下載資料夾",
            command=self._open_downloads,
            bg="#F57C00",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row2,
            text="📂 預處理output",
            command=self._open_preprocess_output,
            bg="#37474F",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row2,
            text="🧼 清除紀錄",
            command=self._clear_processed_log,
            bg="#455A64",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.login_btn = tk.Button(
            btn_row2,
            text="🔑 登入 IG",
            command=self._manual_login,
            bg="#5C6BC0",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.login_btn.pack(side=tk.LEFT, padx=(0, self._gap_x))

        tk.Button(
            btn_row2,
            text="🌐 IG_Parser",
            command=self._open_ig_parser_profile,
            bg="#1565C0",
            fg="white",
            padx=self._button_padx,
            pady=self._button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=self._ui_px(8), pady=(0, self._ui_px(4)))

        mid = tk.Frame(self.root, padx=self._pad_x)
        mid.pack(fill=tk.BOTH, expand=True)

        cols = ("url", "status", "retry")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        self._bind_tree_sort_headings()
        self.tree.column("url", width=self._url_col_width, stretch=True, minwidth=self._ui_px(300))
        self.tree.column("status", width=self._status_col_width, stretch=False, anchor="center")
        self.tree.column("retry", width=self._retry_col_width, stretch=False, anchor="center")

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

        self._build_tree_context_menu()
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Control-c>", self._copy_selected_url)
        self.tree.bind("<Control-C>", self._copy_selected_url)
        self.tree.bind("<Double-Button-1>", self._copy_selected_url)

        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, padx=self._ui_px(8), pady=(self._ui_px(4), 0))

        bot = tk.Frame(self.root, padx=self._pad_x, pady=self._pad_y)
        bot.pack(fill=tk.X)

        filter_frame = tk.LabelFrame(bot, text="篩選", padx=self._ui_px(4), pady=self._ui_px(2))
        filter_frame.pack(side=tk.LEFT)

        filter_defs = [
            ("全部", "ALL"),
            ("SUCCESS", "SUCCESS"),
            ("FAILED", "FAILED"),
            ("BLOCKED", "BLOCKED"),
            ("MISSING", "MISSING"),
            ("RETRY", "RETRY"),
            ("DOWNLOADING", "DOWNLOADING"),
        ]
        for label, key in filter_defs:
            color = _STATUS_COLORS.get(key, "#333333")
            btn_width = self._filter_width
            if key == "DOWNLOADING":
                btn_width = self._filter_downloading_width
            tk.Button(
                filter_frame,
                text=label,
                width=btn_width,
                fg=color if key != "ALL" else "#333333",
                font=("Microsoft JhengHei UI", self._small_font_size),
                command=lambda k=key: self._set_filter(k),
                relief=tk.FLAT,
                cursor="hand2",
            ).pack(side=tk.LEFT, padx=self._ui_px(2), pady=self._ui_px(1))

        act_frame = tk.Frame(bot)
        act_frame.pack(side=tk.RIGHT)

        tk.Button(
            act_frame,
            text="📄 失敗",
            command=self._show_failed_links_window,
            bg="#6D4C41",
            fg="white",
            padx=self._button_padx,
            pady=self._bottom_button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=self._gap_x)

        tk.Button(
            act_frame,
            text="🚫 BLOCKED",
            command=lambda: self._copy_status_urls("BLOCKED"),
            bg="#EF6C00",
            fg="white",
            padx=self._button_padx,
            pady=self._bottom_button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=self._gap_x)

        self.pause_btn = tk.Button(
            act_frame,
            text="⏸ 暫停",
            command=self._pause_downloads,
            bg="#546E7A",
            fg="white",
            padx=self._button_padx,
            pady=self._bottom_button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.pause_btn.pack(side=tk.LEFT, padx=self._gap_x)

        self.resume_btn = tk.Button(
            act_frame,
            text="▶ 繼續",
            command=self._resume_downloads,
            bg="#2E7D32",
            fg="white",
            padx=self._button_padx,
            pady=self._bottom_button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.resume_btn.pack(side=tk.LEFT, padx=self._gap_x)

        self.stop_btn = tk.Button(
            act_frame,
            text="⏹ 停止",
            command=self._stop_downloads,
            bg="#8E24AA",
            fg="white",
            padx=self._button_padx,
            pady=self._bottom_button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        )
        self.stop_btn.pack(side=tk.LEFT, padx=self._gap_x)

        tk.Button(
            act_frame,
            text="🔁 重試",
            command=self._retry_failed,
            bg="#E65100",
            fg="white",
            padx=self._button_padx,
            pady=self._bottom_button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=self._gap_x)

        tk.Button(
            act_frame,
            text="🗑 清空",
            command=self._clear_tasks,
            bg="#C62828",
            fg="white",
            padx=self._button_padx,
            pady=self._bottom_button_pady,
            font=("Microsoft JhengHei UI", self._small_font_size),
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
            font=("Microsoft JhengHei UI", self._small_font_size),
            fg="#333333",
            padx=self._status_padx,
        ).pack(fill=tk.X, side=tk.BOTTOM)

    def _bind_tree_sort_headings(self):
        """Bind clickable Treeview column headings for display-only sorting."""
        for col, label in self._tree_heading_base.items():
            anchor = "center" if col in {"status", "retry"} else "w"
            self.tree.heading(
                col,
                text=label,
                anchor=anchor,
                command=lambda c=col: self._on_tree_heading_click(c),
            )
        self._update_tree_heading_labels()

    def _on_tree_heading_click(self, col: str):
        """Toggle Treeview sort column without changing queue execution order."""
        if self._tree_sort_col == col:
            self._tree_sort_reverse = not self._tree_sort_reverse
        else:
            self._tree_sort_col = col
            self._tree_sort_reverse = False
        self._update_tree_heading_labels()
        self._schedule_refresh(0)

    def _update_tree_heading_labels(self):
        """Show ▲ / ▼ on the active Treeview sort column."""
        if not hasattr(self, "tree"):
            return
        for col, label in self._tree_heading_base.items():
            suffix = ""
            if self._tree_sort_col == col:
                suffix = " ▼" if self._tree_sort_reverse else " ▲"
            anchor = "center" if col in {"status", "retry"} else "w"
            try:
                self.tree.heading(
                    col,
                    text=f"{label}{suffix}",
                    anchor=anchor,
                    command=lambda c=col: self._on_tree_heading_click(c),
                )
            except Exception:
                pass

    def _apply_tree_sort(self, rows: list[dict]) -> list[dict]:
        """Return a sorted copy of rows for display only; downloader queue order is untouched."""
        col = self._tree_sort_col
        if not col:
            return list(rows)

        status_rank = {
            "DOWNLOADING": 0,
            "PENDING": 1,
            "RETRY": 2,
            "FAILED": 3,
            "BLOCKED": 4,
            "MISSING": 5,
            "UNAVAILABLE": 6,
            "SUCCESS": 7,
        }

        def key_func(task: dict):
            if col == "retry":
                try:
                    return int(task.get("retry", 0) or 0)
                except Exception:
                    return 0
            if col == "status":
                status = str(task.get("status", "") or "").upper()
                return (status_rank.get(status, 99), status)
            if col == "url":
                return str(task.get("url", "") or "").lower()
            return str(task.get(col, "") or "").lower()

        try:
            return sorted(list(rows), key=key_func, reverse=bool(self._tree_sort_reverse))
        except Exception:
            return list(rows)

    def _build_tree_context_menu(self):
        """建立任務清單右鍵選單。"""
        self._tree_context_menu = tk.Menu(self.root, tearoff=0)
        self._tree_context_menu.add_command(
            label="複製連結",
            command=self._copy_context_url,
        )
        self._tree_context_menu.add_command(
            label="複製選取列狀態",
            command=self._copy_context_row,
        )
        self._tree_context_menu.add_separator()
        self._tree_context_menu.add_command(
            label="查看完整連結",
            command=self._show_context_url,
        )
        self._tree_context_menu.add_separator()
        self._tree_context_menu.add_command(
            label="複製目前篩選 URL",
            command=self._copy_current_filtered_urls,
        )

    def _on_tree_right_click(self, event):
        """右鍵點擊任務列時，立即保存完整 URL 快照並顯示選單。"""
        iid = self.tree.identify_row(event.y)
        if not iid:
            return "break"

        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self._tree_context_iid = iid

        vals = self.tree.item(iid, "values") or ("", "", "")
        self._tree_context_url_snapshot = self._tree_url_by_iid.get(iid, "") or (vals[0] if len(vals) > 0 else "")
        self._tree_context_status_snapshot = vals[1] if len(vals) > 1 else ""
        self._tree_context_retry_snapshot = vals[2] if len(vals) > 2 else ""

        try:
            self._tree_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._tree_context_menu.grab_release()

        return "break"

    def _get_selected_tree_iid(self):
        """取得目前選取的 Treeview row iid。"""
        sel = self.tree.selection()
        if sel:
            return sel[0]
        focus = self.tree.focus()
        return focus or None

    def _copy_to_clipboard(self, text: str, status_message: str):
        """安全複製文字到剪貼簿並更新狀態列。"""
        if text is None:
            text = ""
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.status_var.set(status_message)

    def _copy_context_url(self):
        """右鍵選單：複製完整 URL。右鍵瞬間已保存快照，不怕表格刷新。"""
        url = self._tree_context_url_snapshot or ""
        if not url:
            iid = self._tree_context_iid or self._get_selected_tree_iid()
            if iid:
                url = self._tree_url_by_iid.get(iid, "")
                if not url:
                    vals = self.tree.item(iid, "values")
                    url = vals[0] if vals else ""
        if not url:
            self.status_var.set("沒有可複製的連結")
            return
        self._copy_to_clipboard(url, "已複製連結")

    def _copy_context_row(self):
        """右鍵選單：複製該列 URL / 狀態 / Retry。"""
        url = self._tree_context_url_snapshot or ""
        status = self._tree_context_status_snapshot or ""
        retry = self._tree_context_retry_snapshot or ""

        if not url:
            iid = self._tree_context_iid or self._get_selected_tree_iid()
            if not iid:
                return
            vals = self.tree.item(iid, "values")
            url = self._tree_url_by_iid.get(iid, "")
            status = vals[1] if len(vals) > 1 else ""
            retry = vals[2] if len(vals) > 2 else ""

        text = f"{url}\t{status}\t{retry}"
        self._copy_to_clipboard(text, "已複製選取列狀態")

    def _show_context_url(self):
        """右鍵選單：顯示完整 URL。"""
        url = self._tree_context_url_snapshot or ""
        if not url:
            iid = self._tree_context_iid or self._get_selected_tree_iid()
            if iid:
                url = self._tree_url_by_iid.get(iid, "")
                if not url:
                    vals = self.tree.item(iid, "values")
                    url = vals[0] if vals else ""
        messagebox.showinfo("完整連結", url or "沒有可顯示的連結", parent=self.root)

    def _copy_selected_url(self, _event=None):
        """Ctrl+C / 雙擊：複製目前選取列完整 URL。"""
        iid = self._get_selected_tree_iid()
        if not iid:
            return "break"

        url = self._tree_url_by_iid.get(iid, "")
        if not url:
            vals = self.tree.item(iid, "values")
            url = vals[0] if vals else ""

        if url:
            self._copy_to_clipboard(url, "已複製連結")
        else:
            self.status_var.set("沒有可複製的連結")
        return "break"

    def _copy_current_filtered_urls(self):
        """複製目前表格篩選結果的完整 URL。"""
        snapshot = queue_manager.get_snapshot()
        if self._active_filter != "ALL":
            snapshot = [t for t in snapshot if t.get("status") == self._active_filter]
        urls = [t.get("url", "") for t in snapshot if t.get("url")]
        if not urls:
            self.status_var.set("目前篩選結果沒有 URL 可複製")
            return
        self._copy_to_clipboard("\n".join(urls), f"已複製目前篩選 URL：{len(urls)} 筆")

    def _copy_status_urls(self, status: str):
        """一鍵複製指定狀態 URL，例如 BLOCKED / MISSING / FAILED。"""
        status = (status or "").upper()
        urls = queue_manager.get_urls_by_status(status)
        if not urls:
            self.status_var.set(f"目前沒有 {status} URL")
            return
        self._copy_to_clipboard("\n".join(urls), f"已複製 {status} URL：{len(urls)} 筆")

    def _init_session(self):
        if os.path.exists(COOKIES_FILE):
            instagram.use_cookies(COOKIES_FILE)
            self.status_var.set("使用 cookies.txt 模式  •  限制貼文 fallback 會自動使用 data/chrome_ig_parser 專用 Profile")
        else:
            instagram.setup()
            self.status_var.set("匿名模式  •  建議先點「🌐 初始化 IG_Parser」登入專用 Profile")

    def _open_ig_parser_profile(self):
        """Open project-local IG_Parser Chrome profile for one-time login / trust setup."""
        try:
            profile_root = instagram.open_ig_parser_profile("https://www.instagram.com/")
            messagebox.showinfo(
                "IG_Parser 專用 Profile",
                "已開啟 IG_Parser 專用 Chrome Profile。\n\n"
                "請在該 Chrome 視窗中完成：\n"
                "1. 登入 Instagram\n"
                "2. 勾選 / 確認記住這台設備\n"
                "3. 打開限制貼文並完成未滿18歲 / 特定對象確認\n\n"
                "完成後關閉該 Chrome 視窗，再回到下載器繼續下載。\n\n"
                f"Profile 位置：\n{profile_root}",
                parent=self.root,
            )
            self.status_var.set(f"已開啟 IG_Parser 專用 Profile：{profile_root}")
        except Exception as e:
            messagebox.showerror(
                "無法開啟 IG_Parser Profile",
                f"開啟 IG_Parser 專用 Chrome Profile 失敗：\n{e}",
                parent=self.root,
            )
            self.status_var.set(f"IG_Parser Profile 開啟失敗：{e}")

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
        win.geometry(f"{self._ui_px(320)}x{self._ui_px(210)}")

        tk.Label(
            win,
            text="選擇要登入的帳號：",
            font=("Microsoft JhengHei UI", self._base_font_size),
            pady=self._ui_px(10),
        ).pack()

        lb = tk.Listbox(win, width=36, height=min(len(accounts), 6), font=("Consolas", self._small_font_size))
        for a in accounts:
            lb.insert(tk.END, a["username"])
        lb.select_set(0)
        lb.pack(padx=self._ui_px(20), pady=(0, self._ui_px(6)))

        def _confirm():
            sel = lb.curselection()
            if sel:
                self._do_login(accounts[sel[0]])
            win.destroy()

        btn_r = tk.Frame(win)
        btn_r.pack(pady=self._ui_px(6))
        tk.Button(
            btn_r,
            text="確認",
            command=_confirm,
            padx=self._ui_px(18),
            pady=self._ui_px(4),
            bg="#1976D2",
            fg="white",
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=self._gap_x)
        tk.Button(
            btn_r,
            text="取消",
            command=win.destroy,
            padx=self._ui_px(18),
            pady=self._ui_px(4),
            relief=tk.FLAT,
        ).pack(side=tk.LEFT, padx=self._gap_x)

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

        added = int(result.get("added", 0) or 0)
        skipped_processed = int(result.get("skipped_processed", result.get("skipped", 0)) or 0)
        skipped_duplicate = int(result.get("skipped_duplicate", result.get("duplicated", 0)) or 0)

        # 新增任務代表新批次，允許完成後再次跳出摘要通知。
        self._completion_notified = False
        self._last_completion_signature = ""

        self.status_var.set(
            f"新增 {added} 筆  |  "
            f"略過已下載 {skipped_processed} 筆  |  "
            f"略過重複 {skipped_duplicate} 筆"
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
        self._last_completion_signature = ""
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
        self._last_completion_signature = ""
        self._schedule_refresh(0)
        self.status_var.set("任務已清空，日誌已儲存至 data/")

    def _show_failed_links_window(self):
        if self._failed_window and self._failed_window.winfo_exists():
            self._failed_window.lift()
            self._failed_window.focus_force()
            self._refresh_failed_window_text()
            return

        self._failed_window = tk.Toplevel(self.root)
        self._failed_window.title("失敗 / 封鎖 / Missing URL 清單")
        self._failed_window.geometry(f"{self._ui_px(980)}x{self._ui_px(620)}")

        top = tk.Frame(self._failed_window, padx=self._pad_x, pady=self._pad_x)
        top.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            top,
            text="失敗 / 封鎖 / Missing / 不可用 連結",
            font=("Microsoft JhengHei UI", self._title_font_size - 2, "bold"),
        ).pack(anchor="w", pady=(0, self._ui_px(8)))

        filter_row = tk.Frame(top)
        filter_row.pack(fill=tk.X, pady=(0, self._ui_px(6)))

        tk.Label(filter_row, text="狀態篩選：", font=("Microsoft JhengHei UI", self._small_font_size)).pack(side=tk.LEFT)
        self.failed_filter_var = tk.StringVar(value="ALL")
        self.failed_filter_combo = ttk.Combobox(
            filter_row,
            textvariable=self.failed_filter_var,
            values=("ALL", "FAILED", "BLOCKED", "MISSING", "UNAVAILABLE", "RETRY"),
            state="readonly",
            width=16,
        )
        self.failed_filter_combo.pack(side=tk.LEFT, padx=(self._gap_x, self._button_padx))
        self.failed_filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._refresh_failed_window_text())

        tk.Button(
            filter_row,
            text="複製 BLOCKED URL",
            command=lambda: self._copy_status_urls("BLOCKED"),
            bg="#EF6C00",
            fg="white",
            padx=self._button_padx,
            pady=self._ui_px(3),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            filter_row,
            text="複製 MISSING URL",
            command=lambda: self._copy_status_urls("MISSING"),
            bg="#607D8B",
            fg="white",
            padx=self._button_padx,
            pady=self._ui_px(3),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=3)

        tk.Button(
            filter_row,
            text="開啟 data 資料夾",
            command=lambda: self._open_path(DATA_DIR),
            bg="#455A64",
            fg="white",
            padx=self._button_padx,
            pady=self._ui_px(3),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=3)

        self.failed_text = tk.Text(
            top,
            wrap=tk.WORD,
            font=("Consolas", self._small_font_size + 1),
            relief=tk.SOLID,
            bd=1,
        )
        self.failed_text.pack(fill=tk.BOTH, expand=True)

        btns = tk.Frame(top)
        btns.pack(fill=tk.X, pady=(self._ui_px(8), 0))

        tk.Button(
            btns,
            text="🔄 重新整理",
            command=self._refresh_failed_window_text,
            bg="#1976D2",
            fg="white",
            padx=self._button_padx,
            pady=self._ui_px(4),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=self._gap_x)

        tk.Button(
            btns,
            text="📋 複製目前清單",
            command=self._copy_failed_text,
            bg="#6A1B9A",
            fg="white",
            padx=self._button_padx,
            pady=self._ui_px(4),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=self._gap_x)

        tk.Button(
            btns,
            text="📋 只複製目前 URL",
            command=self._copy_failed_urls_only,
            bg="#00897B",
            fg="white",
            padx=self._button_padx,
            pady=self._ui_px(4),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=self._gap_x)

        tk.Button(
            btns,
            text="關閉",
            command=self._failed_window.destroy,
            padx=self._button_padx,
            pady=self._ui_px(4),
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=4)

        self._refresh_failed_window_text()

    def _get_failed_filter_key(self) -> str:
        if hasattr(self, "failed_filter_var"):
            return self.failed_filter_var.get() or "ALL"
        return "ALL"

    def _refresh_failed_window_text(self):
        if not hasattr(self, "failed_text"):
            return
        status_filter = self._get_failed_filter_key()
        content = queue_manager.get_failed_links_text(status_filter=status_filter, urls_only=False)
        self.failed_text.delete("1.0", tk.END)
        self.failed_text.insert("1.0", content)

    def _copy_failed_text(self):
        if not hasattr(self, "failed_text"):
            return
        content = self.failed_text.get("1.0", tk.END).strip()
        if not content:
            self.status_var.set("目前清單沒有內容可複製")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.root.update_idletasks()
        self.status_var.set("已複製目前失敗清單")

    def _copy_failed_urls_only(self):
        status_filter = self._get_failed_filter_key()
        content = queue_manager.get_failed_links_text(status_filter=status_filter, urls_only=True)
        if not content.strip():
            self.status_var.set(f"目前沒有 {status_filter} URL 可複製")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(content.strip())
        self.root.update_idletasks()
        self.status_var.set(f"已複製目前 URL 清單：{status_filter}")

    def _set_filter(self, key: str):
        self._active_filter = key
        self._schedule_refresh(0)

    def _schedule_refresh(self, delay: int = 1000):
        if self._refresh_id is not None:
            self.root.after_cancel(self._refresh_id)
            self._refresh_id = None
        self._refresh_id = self.root.after(delay, self._refresh_table)

    def _maybe_show_completion_popup(self, snapshot: list[dict], runtime: dict, counts: dict, elapsed: float):
        """批次完成後跳出一次非阻塞摘要視窗。"""
        total = len(snapshot)
        if total <= 0:
            self._completion_notified = False
            self._last_completion_signature = ""
            return

        active_count = counts.get("PENDING", 0) + counts.get("DOWNLOADING", 0)
        phase = runtime.get("phase", "IDLE")

        # 冷卻中代表 worker 還在跑下一筆，不視為完成。
        if active_count > 0 or phase in {"DOWNLOADING", "COOLDOWN", "PAUSED"}:
            return

        signature_parts = [str(total)]
        for status in ("SUCCESS", "FAILED", "BLOCKED", "MISSING", "RETRY", "UNAVAILABLE"):
            signature_parts.append(f"{status}:{counts.get(status, 0)}")
        signature = "|".join(signature_parts)

        if self._completion_notified and self._last_completion_signature == signature:
            return

        self._completion_notified = True
        self._last_completion_signature = signature
        self.root.after(120, lambda: self._show_completion_popup(total, counts, elapsed))

    def _show_completion_popup(self, total: int, counts: dict, elapsed: float):
        """顯示下載結果摘要。採非 modal 設計，避免阻塞 GUI refresh / worker。"""
        if not self.root.winfo_exists():
            return

        success = counts.get("SUCCESS", 0)
        failed = counts.get("FAILED", 0)
        blocked = counts.get("BLOCKED", 0)
        missing = counts.get("MISSING", 0)
        retry = counts.get("RETRY", 0)
        unavailable = counts.get("UNAVAILABLE", 0)
        problem_total = failed + blocked + missing + retry + unavailable

        title = "下載完成" if problem_total == 0 else "下載完成，部分任務需檢查"
        icon = "✅" if problem_total == 0 else "⚠️"
        title_color = "#2E7D32" if problem_total == 0 else "#E65100"

        win = tk.Toplevel(self.root)
        win.title(title)
        win.geometry(f"{self._ui_px(460)}x{self._ui_px(360)}")
        win.resizable(False, False)
        win.transient(self.root)

        try:
            win.attributes("-topmost", True)
            win.after(1200, lambda: win.attributes("-topmost", False) if win.winfo_exists() else None)
        except Exception:
            pass

        frame = tk.Frame(win, padx=self._ui_px(20), pady=self._ui_px(18))
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            frame,
            text=f"{icon} {title}",
            font=("Microsoft JhengHei UI", self._title_font_size, "bold"),
            fg=title_color,
        ).pack(anchor="w", pady=(0, self._ui_px(12)))

        rows = [
            ("總任務", total),
            ("成功", success),
            ("失敗", failed),
            ("封鎖", blocked),
            ("Missing", missing),
            ("待重試", retry),
            ("不可用", unavailable),
            ("耗時", _format_seconds(elapsed)),
        ]

        grid = tk.Frame(frame)
        grid.pack(fill=tk.X, pady=(0, self._ui_px(12)))
        for r, (k, v) in enumerate(rows):
            tk.Label(grid, text=f"{k}：", font=("Microsoft JhengHei UI", self._base_font_size, "bold"), anchor="w").grid(row=r, column=0, sticky="w", pady=self._ui_px(2))
            tk.Label(grid, text=str(v), font=("Microsoft JhengHei UI", self._base_font_size), anchor="w").grid(row=r, column=1, sticky="w", pady=self._ui_px(2))

        hint = "全部任務已成功完成。" if problem_total == 0 else "可點擊「查看失敗」檢查 FAILED / BLOCKED / MISSING / RETRY 清單。"
        tk.Label(
            frame,
            text=hint,
            font=("Microsoft JhengHei UI", self._base_font_size),
            fg="#555555",
            wraplength=410,
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(0, self._ui_px(12)))

        btn_row = tk.Frame(frame)
        btn_row.pack(fill=tk.X, pady=(self._ui_px(4), 0))

        tk.Button(
            btn_row,
            text="📁 下載資料夾",
            command=lambda: self._open_path(DOWNLOAD_DIR),
            bg="#F57C00",
            fg="white",
            padx=self._ui_px(12),
            pady=self._button_pady,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))

        if problem_total:
            tk.Button(
                btn_row,
                text="📄 失敗",
                command=self._show_failed_links_window,
                bg="#6D4C41",
                fg="white",
                padx=12,
                pady=self._button_pady,
                relief=tk.FLAT,
                cursor="hand2",
            ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Button(
            btn_row,
            text="關閉",
            command=win.destroy,
            padx=self._ui_px(14),
            pady=self._button_pady,
            relief=tk.FLAT,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        win.update_idletasks()
        x = self.root.winfo_x() + max(0, (self.root.winfo_width() - win.winfo_width()) // 2)
        y = self.root.winfo_y() + max(0, (self.root.winfo_height() - win.winfo_height()) // 2)
        win.geometry(f"+{x}+{y}")

    def _refresh_table(self):
        self._refresh_id = None
        snapshot = queue_manager.get_snapshot()
        runtime = queue_manager.get_runtime()
        processed_count = queue_manager.get_processed_count()

        display = snapshot if self._active_filter == "ALL" else [t for t in snapshot if t.get("status") == self._active_filter]
        display = self._apply_tree_sort(display)

        self._tree_url_by_iid.clear()

        for row in self.tree.get_children():
            self.tree.delete(row)

        for t in display:
            full_url = t.get("url", "")
            url_short = full_url[:120] + ("…" if len(full_url) > 120 else "")
            status = t["status"]
            tag = status if status in _STATUS_COLORS else "PENDING"
            iid = self.tree.insert("", tk.END, values=(url_short, status, t["retry"]), tags=(tag,))
            self._tree_url_by_iid[iid] = full_url

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
        for s in ("DOWNLOADING", "PENDING", "SUCCESS", "FAILED", "BLOCKED", "MISSING", "RETRY", "UNAVAILABLE"):
            if counts.get(s, 0):
                parts.append(f"{s}: {counts[s]}")

        self._maybe_show_completion_popup(snapshot, runtime, counts, elapsed)

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
    _enable_dpi_awareness()

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