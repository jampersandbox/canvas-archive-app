#!/usr/bin/env python3
"""
canvas_archive.py
=================
Canvas Archive — Save your course materials before you lose access.
"""

# ── --run-script dispatch (must be first) ─────────────────────────────────────
import sys
import os

if len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
    script_name = sys.argv[2]
    script_args = sys.argv[3:]

    if getattr(sys, "frozen", False):
        script_dir = sys._MEIPASS
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))

    data_dir_env = os.environ.get("CANVAS_ARCHIVE_DATA_DIR", "")
    if data_dir_env:
        data_dir = data_dir_env
    elif getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            data_dir = os.path.join(
                os.path.expanduser("~"),
                "Library", "Application Support", "Canvas Archive",
            )
        elif sys.platform.startswith("win"):
            data_dir = os.path.join(
                os.environ.get("APPDATA", os.path.expanduser("~")),
                "Canvas Archive",
            )
        else:
            data_dir = os.path.join(
                os.path.expanduser("~"), ".canvas-archive"
            )
    else:
        data_dir = script_dir

    os.makedirs(data_dir, exist_ok=True)

    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    os.chdir(data_dir)

    script_path = os.path.join(script_dir, script_name)
    if not os.path.exists(script_path):
        print(f"Script not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    import importlib.util
    spec   = importlib.util.spec_from_file_location(
        "_canvas_script", script_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.argv = [script_path] + script_args
    try:
        spec.loader.exec_module(module)
        module.main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)

# ── Normal GUI imports ─────────────────────────────────────────────────────────
import json
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk


# ─────────────────────────────────────────────────────────────────────────────
#  Paths
# ─────────────────────────────────────────────────────────────────────────────

def _get_script_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _get_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            d = Path.home() / "Library" / "Application Support" / "Canvas Archive"
        elif sys.platform.startswith("win"):
            d = Path(os.environ.get("APPDATA", str(Path.home()))) / "Canvas Archive"
        else:
            d = Path.home() / ".canvas-archive"
    else:
        d = Path(__file__).parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def _playwright_browsers_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    elif sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
    else:
        return Path.home() / ".cache" / "ms-playwright"


SCRIPT_DIR    = _get_script_dir()
DATA_DIR      = _get_data_dir()
CONFIG_FILE   = DATA_DIR / "canvas_config.json"
SENTINEL_FILE = DATA_DIR / "gui_login_ready.txt"
LOCK_FILE     = DATA_DIR / ".canvas_archive.lock"


# ─────────────────────────────────────────────────────────────────────────────
#  Single-instance lock
# ─────────────────────────────────────────────────────────────────────────────

def _acquire_lock() -> bool:
    """
    Returns True if this is the only running instance.
    Uses a PID lock file — automatically released when the process exits.
    """
    import fcntl
    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        # Keep the fd open — lock is released when process exits
        _acquire_lock._fd = lock_fd
        return True
    except (IOError, OSError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

COMMON_CANVAS_URLS = [
    "https://canvas.harvard.edu",
    "https://canvas.yale.edu",
    "https://canvas.mit.edu",
    "https://canvas.stanford.edu",
    "https://canvas.princeton.edu",
    "https://canvas.columbia.edu",
    "https://canvas.cornell.edu",
    "https://canvas.upenn.edu",
    "https://canvas.dartmouth.edu",
    "https://canvas.brown.edu",
    "https://canvas.uchicago.edu",
    "https://canvas.duke.edu",
    "https://canvas.northwestern.edu",
    "https://canvas.vanderbilt.edu",
    "https://canvas.emory.edu",
    "https://canvas.georgetown.edu",
    "https://canvas.bu.edu",
    "https://canvas.bc.edu",
    "https://canvas.tufts.edu",
    "https://canvas.nyu.edu",
    "https://canvas.usc.edu",
    "https://canvas.virginia.edu",
    "https://canvas.wustl.edu",
]

REQUIRED_SCRIPTS = [
    "canvas_auth.py",
    "canvas_downloader.py",
    "external_downloader.py",
    "panopto_downloader.py",
    "reserves_downloader.py",
]

_LOGIN_PHRASES = [
    "Press ENTER", "press ENTER", "press Enter", "Press Enter",
    "ENTER after you are logged in", "ENTER once signed in",
    "[Press ENTER", "Waiting for GUI login",
    "Canvas Login Required", "Login Required", "Login required",
]

# Design tokens matching the website
CREAM      = "#f5f0e8"
NAVY       = "#1a1a2e"
PURPLE     = "#4a0e8f"
PURPLE_LT  = "#6b21cc"
GREEN      = "#2d8a3e"
GREEN_HOV  = "#236b31"
DARK_LOG   = "#0d0d1a"
GREEN_LOG  = "#4ade80"
CARD_BG    = "#faf8f3"
BORDER     = "#1a1a2e"


# ─────────────────────────────────────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    defaults = {
        "canvas_url":   "https://canvas.harvard.edu",
        "panopto_url":  "https://harvard.hosted.panopto.com",
        "output_dir":   str(Path.home() / "Documents" / "canvas_downloads"),
        "skip_ongoing": True,
        "skip_videos":  False,
        "do_canvas":    True,
        "do_external":  True,
        "do_panopto":   True,
        "do_reserves":  True,
    }
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                defaults.update(saved)
        except Exception:
            pass
    return defaults


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def write_canvas_config(canvas_url: str, panopto_url: str) -> None:
    (DATA_DIR / "canvas_config.py").write_text(
        f"CANVAS_BASE_URL  = {canvas_url!r}\n"
        f"PANOPTO_BASE_URL = {panopto_url!r}\n",
        encoding="utf-8",
    )
    (DATA_DIR / "canvas_config.json").write_text(
        json.dumps({"canvas_url": canvas_url, "panopto_url": panopto_url},
                   indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Browser management
# ─────────────────────────────────────────────────────────────────────────────

def _chromium_exe() -> Path | None:
    """
    Find the actual Chromium executable — not just the folder.
    Returns the Path if found, None if not.
    """
    base = _playwright_browsers_dir()
    if not base.exists():
        return None

    patterns = [
        "chromium*/chrome-mac-arm64/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing",
        "chromium*/chrome-mac-x64/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing",
        "chromium*/chrome-win/chrome.exe",
        "chromium*/chrome-linux/chrome",
    ]
    for pat in patterns:
        matches = list(base.glob(pat))
        if matches:
            return matches[0]
    return None


def _browser_installed() -> bool:
    """Only returns True if the actual executable exists and is runnable."""
    exe = _chromium_exe()
    return exe is not None and exe.exists()


def _download_browser_thread(sv: tk.StringVar,
                              result: dict,
                              bar: ttk.Progressbar) -> None:
    """Run in a background thread — downloads Chromium."""
    try:
        env = os.environ.copy()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(_playwright_browsers_dir())
        proc = subprocess.Popen(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            env=env,
        )
        for line in proc.stdout:
            line = line.strip()
            if line:
                sv.set(line[:70])
        proc.wait()
        if proc.returncode == 0 and _browser_installed():
            result["ok"] = True
            sv.set("Browser ready!")
        else:
            result["ok"] = False
            sv.set("Download failed — please try again.")
    except Exception as e:
        result["ok"] = False
        sv.set(f"Error: {e}")
    finally:
        bar.stop()


def install_browser_dialog(parent) -> bool:
    win = tk.Toplevel(parent)
    win.title("First-time setup")
    win.configure(bg=CREAM)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.protocol("WM_DELETE_WINDOW", lambda: None)

    pw, ph = 520, 260
    x = (win.winfo_screenwidth()  - pw) // 2
    y = (win.winfo_screenheight() - ph) // 2
    win.geometry(f"{pw}x{ph}+{x}+{y}")

    # Title
    tk.Label(
        win, text="One-time setup",
        font=("Georgia", 18, "bold"),
        bg=CREAM, fg=NAVY,
    ).pack(pady=(24, 4))

    tk.Label(
        win,
        text=(
            "Downloading a browser for Canvas Archive to use.\n"
            "About 150 MB — only happens once.\n"
            "Please leave this window open."
        ),
        font=("Helvetica", 11),
        bg=CREAM, fg=NAVY,
        justify="center",
    ).pack(pady=(0, 12))

    bar = ttk.Progressbar(win, mode="indeterminate", length=440)
    bar.pack(pady=(0, 8), padx=30)
    bar.start(10)

    sv = tk.StringVar(value="Starting download…")
    tk.Label(
        win, textvariable=sv,
        font=("Helvetica", 10),
        fg=PURPLE, bg=CREAM,
    ).pack()

    result = {"ok": False}

    def _after_download():
        win.after(2000, win.destroy)

    def _run():
        _download_browser_thread(sv, result, bar)
        win.after(0, _after_download)

    threading.Thread(target=_run, daemon=True).start()
    win.wait_window()
    return result["ok"]


# ─────────────────────────────────────────────────────────────────────────────
#  Notebook line background
# ─────────────────────────────────────────────────────────────────────────────

def _make_notebook_bg(canvas_widget, width, height):
    """Draw horizontal notebook lines on a tk.Canvas widget."""
    canvas_widget.delete("lines")
    line_spacing = 28
    for y in range(0, height, line_spacing):
        canvas_widget.create_line(
            0, y, width, y,
            fill="#d4cfc4", width=1, tags="lines"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────────────────────────────────────

class CanvasArchiveApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Canvas Archive")
        self.root.resizable(True, True)
        self.root.configure(bg=CREAM)

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w  = min(860, int(sw * 0.88))
        h  = min(780, int(sh * 0.88))
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

        self._cfg = load_config()

        self.canvas_url   = tk.StringVar(value=self._cfg["canvas_url"])
        self.panopto_url  = tk.StringVar(
            value=self._cfg.get("panopto_url",
                                "https://harvard.hosted.panopto.com"))
        self.output_dir   = tk.StringVar(value=self._cfg["output_dir"])
        self.skip_ongoing = tk.BooleanVar(value=self._cfg["skip_ongoing"])
        self.skip_videos  = tk.BooleanVar(value=self._cfg["skip_videos"])
        self.do_canvas    = tk.BooleanVar(value=self._cfg["do_canvas"])
        self.do_external  = tk.BooleanVar(value=self._cfg["do_external"])
        self.do_panopto   = tk.BooleanVar(value=self._cfg["do_panopto"])
        self.do_reserves  = tk.BooleanVar(value=self._cfg["do_reserves"])

        self.running       = False
        self.process:      subprocess.Popen | None = None
        self.log_queue:    queue.Queue = queue.Queue()
        self.script_queue: list[tuple[str, list[str]]] = []
        self._login_popup: tk.Toplevel | None = None
        self._dot_job:     str | None = None

        if SENTINEL_FILE.exists():
            SENTINEL_FILE.unlink()

        self._build_ui()
        self._poll_log()

        # First-run browser check — ALWAYS verify the exe exists
        self.root.after(800, self._check_browser)

    # ── Browser check ─────────────────────────────────────────────────────────

    def _check_browser(self):
        if not _browser_installed():
            ok = install_browser_dialog(self.root)
            if not ok:
                messagebox.showwarning(
                    "Browser not installed",
                    "The browser download did not complete.\n\n"
                    "Canvas Archive needs a browser to log in to Canvas.\n"
                    "Please try starting the app again.",
                    parent=self.root,
                )

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Purple header bar ─────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=PURPLE, pady=0)
        header.pack(fill="x", side="top")

        header_inner = tk.Frame(header, bg=PURPLE, pady=14)
        header_inner.pack(fill="x", padx=24)

        tk.Label(
            header_inner,
            text="Canvas Archive",
            font=("Georgia", 22, "bold"),
            fg="white", bg=PURPLE,
        ).pack(side="left")

        tk.Label(
            header_inner,
            text="★ free for graduating students ★",
            font=("Helvetica", 10),
            fg="#c4a8ff", bg=PURPLE,
        ).pack(side="right", pady=(4, 0))

        # ── Fixed bottom bar ──────────────────────────────────────────────────
        bottom = tk.Frame(self.root, bg=CREAM, pady=14, padx=24)
        bottom.pack(fill="x", side="bottom")

        self.start_btn = tk.Button(
            bottom,
            text="Start Download  ▶",
            font=("Helvetica", 14, "bold"),
            bg=GREEN, fg="white",
            activebackground=GREEN_HOV,
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=0, pady=10,
            bd=0,
            command=self._start,
        )
        self.start_btn.pack(fill="x", pady=(0, 8))
        self._round_btn(self.start_btn, GREEN)

        self.stop_btn = tk.Button(
            bottom,
            text="Stop",
            font=("Helvetica", 11),
            bg="#e0d8cc", fg=NAVY,
            activebackground="#ccc4b8",
            relief="flat",
            cursor="hand2",
            padx=0, pady=6,
            bd=0,
            state="disabled",
            command=self._stop,
        )
        self.stop_btn.pack(fill="x")

        # Status label
        self.status_var = tk.StringVar(
            value="Ready — click Start Download to begin."
        )
        self.status_lbl = tk.Label(
            bottom,
            textvariable=self.status_var,
            font=("Helvetica", 10),
            fg=PURPLE, bg=CREAM,
            anchor="center",
        )
        self.status_lbl.pack(pady=(8, 0))

        # ── Scrollable middle ─────────────────────────────────────────────────
        sc = tk.Frame(self.root, bg=CREAM)
        sc.pack(fill="both", expand=True, side="top")

        self._cv = tk.Canvas(sc, bg=CREAM, highlightthickness=0)
        sb = ttk.Scrollbar(sc, orient="vertical", command=self._cv.yview)
        self._cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._cv.pack(side="left", fill="both", expand=True)

        self.main = tk.Frame(self._cv, bg=CREAM, padx=24, pady=16)
        self._cw = self._cv.create_window((0, 0), window=self.main, anchor="nw")
        self._cv.bind("<Configure>", lambda e: (
            self._cv.itemconfig(self._cw, width=e.width),
            _make_notebook_bg(self._cv, e.width, e.height),
        ))
        self.main.bind("<Configure>", lambda e:
            self._cv.configure(scrollregion=self._cv.bbox("all"))
        )
        self._cv.bind_all("<MouseWheel>", self._on_scroll)
        self._cv.bind_all("<Button-4>",   self._on_scroll)
        self._cv.bind_all("<Button-5>",   self._on_scroll)

        self._build_settings_card()
        self._build_what_card()
        self._build_options_card()
        self._build_log_card()

    def _round_btn(self, btn, bg):
        """Approximate rounded pill button using padx and a frame border."""
        btn.configure(padx=20)

    def _card(self, parent, title=None):
        """Create a card with the website's hand-drawn border style."""
        outer = tk.Frame(
            parent, bg=NAVY,
            padx=2, pady=2,
        )
        outer.pack(fill="x", pady=(0, 14))

        inner = tk.Frame(outer, bg=CARD_BG, padx=18, pady=14)
        inner.pack(fill="both", expand=True)

        if title:
            tk.Label(
                inner,
                text=title,
                font=("Georgia", 12, "italic"),
                fg=PURPLE, bg=CARD_BG,
            ).pack(anchor="w", pady=(0, 10))

        return inner

    def _build_settings_card(self):
        card = self._card(self.main, "Settings")

        r1 = tk.Frame(card, bg=CARD_BG)
        r1.pack(fill="x", pady=4)
        tk.Label(
            r1, text="Canvas URL",
            font=("Helvetica", 11, "bold"),
            fg=NAVY, bg=CARD_BG, width=12, anchor="w",
        ).pack(side="left")
        ttk.Combobox(
            r1, textvariable=self.canvas_url,
            values=COMMON_CANVAS_URLS,
            width=44, font=("Helvetica", 11),
        ).pack(side="left", padx=(8, 0))

        r2 = tk.Frame(card, bg=CARD_BG)
        r2.pack(fill="x", pady=4)
        tk.Label(
            r2, text="Save to",
            font=("Helvetica", 11, "bold"),
            fg=NAVY, bg=CARD_BG, width=12, anchor="w",
        ).pack(side="left")
        ttk.Entry(
            r2, textvariable=self.output_dir,
            width=38, font=("Helvetica", 11),
        ).pack(side="left", padx=(8, 6))
        tk.Button(
            r2, text="Browse…",
            font=("Helvetica", 10),
            bg=CREAM, fg=NAVY,
            relief="flat", cursor="hand2",
            command=self._browse,
        ).pack(side="left")

    def _build_what_card(self):
        card = self._card(self.main, "What would you like to download?")

        grid = tk.Frame(card, bg=CARD_BG)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        items = [
            (self.do_canvas,   "📄  Course files",
             "PDFs, slides, videos, documents"),
            (self.do_external, "🔗  External readings",
             "JSTOR, Google Drive, linked content"),
            (self.do_panopto,  "🎬  Lecture recordings",
             "Panopto videos, sorted by course"),
            (self.do_reserves, "📚  Library reserves",
             "Articles & book chapters on reserve"),
        ]

        for i, (var, label, desc) in enumerate(items):
            row, col = divmod(i, 2)
            self._checkbox_pill(grid, var, label, desc, row, col)

    def _checkbox_pill(self, parent, var, label, desc, row, col):
        """Pill-shaped checkbox card matching the website design."""
        outer = tk.Frame(parent, bg=NAVY, padx=2, pady=2)
        outer.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")

        inner = tk.Frame(outer, bg=CREAM, padx=12, pady=10, cursor="hand2")
        inner.pack(fill="both", expand=True)

        def toggle():
            var.set(not var.get())
            _refresh()

        def _refresh():
            if var.get():
                inner.configure(bg="#ede8ff")
                check_lbl.configure(
                    bg="#ede8ff", text="✓",
                    fg=PURPLE,
                )
                title_lbl.configure(bg="#ede8ff")
                desc_lbl.configure(bg="#ede8ff")
            else:
                inner.configure(bg=CREAM)
                check_lbl.configure(bg=CREAM, text="○", fg="#aaa")
                title_lbl.configure(bg=CREAM)
                desc_lbl.configure(bg=CREAM)

        row_f = tk.Frame(inner, bg=CREAM)
        row_f.pack(fill="x")

        check_lbl = tk.Label(
            row_f,
            text="✓" if var.get() else "○",
            font=("Helvetica", 16, "bold"),
            fg=PURPLE if var.get() else "#aaa",
            bg=CREAM, width=2,
        )
        check_lbl.pack(side="left")

        title_lbl = tk.Label(
            row_f, text=label,
            font=("Helvetica", 11, "bold"),
            fg=NAVY, bg=CREAM, anchor="w",
        )
        title_lbl.pack(side="left", padx=(4, 0))

        desc_lbl = tk.Label(
            inner, text=desc,
            font=("Helvetica", 9),
            fg="#666", bg=CREAM, anchor="w",
        )
        desc_lbl.pack(fill="x", pady=(2, 0))

        # Bind click to everything in the card
        for widget in [inner, row_f, check_lbl, title_lbl, desc_lbl]:
            widget.bind("<Button-1>", lambda e: toggle())

        # Trigger initial colour
        var.trace_add("write", lambda *_: _refresh())
        _refresh()

    def _build_options_card(self):
        card = self._card(self.main)

        opts = tk.Frame(card, bg=CARD_BG)
        opts.pack(fill="x")

        for var, label in [
            (self.skip_ongoing, "Skip administrative / ongoing courses"),
            (self.skip_videos,  "Skip video files  (saves disk space)"),
        ]:
            row = tk.Frame(opts, bg=CARD_BG)
            row.pack(fill="x", pady=3)
            ttk.Checkbutton(
                row, text=label, variable=var,
            ).pack(side="left")

    def _build_log_card(self):
        outer = tk.Frame(self.main, bg=NAVY, padx=2, pady=2)
        outer.pack(fill="both", expand=True, pady=(0, 6))

        self.log_text = scrolledtext.ScrolledText(
            outer,
            height=12,
            font=("Courier", 10),
            bg=DARK_LOG,
            fg=GREEN_LOG,
            insertbackground=GREEN_LOG,
            state="disabled",
            relief="flat",
            padx=12,
            pady=10,
        )
        self.log_text.pack(fill="both", expand=True)

        for tag, colour in [
            ("success", GREEN_LOG),
            ("error",   "#ff6b6b"),
            ("warn",    "#ffd93d"),
            ("info",    "#74b9ff"),
            ("header",  "#c4a8ff"),
            ("dim",     "#555577"),
            ("login",   "#ffd93d"),
        ]:
            self.log_text.tag_config(tag, foreground=colour)

    def _on_scroll(self, e):
        if e.num == 4:
            self._cv.yview_scroll(-1, "units")
        elif e.num == 5:
            self._cv.yview_scroll(1, "units")
        else:
            self._cv.yview_scroll(int(-1*(e.delta/120)), "units")

    # ── Login popup ───────────────────────────────────────────────────────────

    def _show_login_popup(self):
        if self._login_popup is not None:
            try:
                self._login_popup.lift()
                self._login_popup.focus_force()
            except Exception:
                pass
            return

        popup = tk.Toplevel(self.root)
        popup.title("Log in to Canvas")
        popup.configure(bg=CREAM)
        popup.resizable(False, False)
        pw, ph = 540, 300
        x = (popup.winfo_screenwidth()  - pw) // 2
        y = (popup.winfo_screenheight() - ph) // 2
        popup.geometry(f"{pw}x{ph}+{x}+{y}")
        popup.attributes("-topmost", True)
        popup.lift()
        popup.protocol("WM_DELETE_WINDOW", lambda: None)
        self._login_popup = popup

        # Border frame
        border = tk.Frame(popup, bg=NAVY, padx=3, pady=3)
        border.pack(fill="both", expand=True, padx=20, pady=20)
        inner = tk.Frame(border, bg=CREAM, padx=24, pady=20)
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner,
            text="Log in to Canvas",
            font=("Georgia", 16, "bold"),
            bg=CREAM, fg=NAVY,
        ).pack(pady=(0, 8))

        tk.Label(
            inner,
            text=(
                "A browser window has opened.\n"
                "Log in with your university credentials as normal.\n\n"
                "Once you can see your Canvas dashboard,\n"
                "click the button below."
            ),
            font=("Helvetica", 11),
            bg=CREAM, fg=NAVY,
            justify="center",
        ).pack(pady=(0, 16))

        tk.Button(
            inner,
            text="I'm logged in  ✓",
            font=("Helvetica", 13, "bold"),
            bg=GREEN, fg="white",
            activebackground=GREEN_HOV,
            activeforeground="white",
            relief="flat",
            cursor="hand2",
            padx=24, pady=10,
            command=self._confirm_login,
        ).pack()

        popup.focus_force()
        self.status_var.set("Waiting for login — click the button above")
        self.status_lbl.config(fg="#856404")

    def _close_login_popup(self):
        if self._login_popup is not None:
            try:
                self._login_popup.destroy()
            except Exception:
                pass
            self._login_popup = None

    def _confirm_login(self):
        try:
            SENTINEL_FILE.write_text("ready", encoding="utf-8")
        except Exception as exc:
            self._log(f"  Could not write sentinel: {exc}\n", "warn")
        self._close_login_popup()
        self._log("  Logged in — continuing…\n\n", "success")
        self.status_var.set("Continuing download…")
        self.status_lbl.config(fg=PURPLE)

    # ── Status / dots ─────────────────────────────────────────────────────────

    def _start_dots(self, base):
        self._dot_base  = base
        self._dot_count = 0
        self._animate_dots()

    def _animate_dots(self):
        if not self.running or self._login_popup is not None:
            return
        self._dot_count = (self._dot_count + 1) % 4
        self.status_var.set(
            f"{self._dot_base}{'.' * self._dot_count}"
        )
        self._dot_job = self.root.after(600, self._animate_dots)

    def _stop_dots(self):
        if self._dot_job:
            self.root.after_cancel(self._dot_job)
            self._dot_job = None

    # ── Actions ───────────────────────────────────────────────────────────────

    def _browse(self):
        d = filedialog.askdirectory(
            title="Choose where to save your files",
            initialdir=self.output_dir.get(),
        )
        if d:
            self.output_dir.set(d)

    def _start(self):
        if self.running:
            return

        # Check browser again before starting
        if not _browser_installed():
            ok = install_browser_dialog(self.root)
            if not ok:
                messagebox.showerror(
                    "Browser required",
                    "Canvas Archive needs a browser to work.\n"
                    "Please try again.",
                    parent=self.root,
                )
                return

        canvas_url = self.canvas_url.get().strip().rstrip("/")
        if not canvas_url.startswith("http"):
            messagebox.showerror(
                "Invalid URL",
                "Please enter a valid Canvas URL starting with https://",
            )
            return

        cfg = {
            "canvas_url":   canvas_url,
            "panopto_url":  self.panopto_url.get().strip().rstrip("/"),
            "output_dir":   self.output_dir.get().strip(),
            "skip_ongoing": self.skip_ongoing.get(),
            "skip_videos":  self.skip_videos.get(),
            "do_canvas":    self.do_canvas.get(),
            "do_external":  self.do_external.get(),
            "do_panopto":   self.do_panopto.get(),
            "do_reserves":  self.do_reserves.get(),
        }
        save_config(cfg)
        write_canvas_config(cfg["canvas_url"], cfg["panopto_url"])

        out     = cfg["output_dir"]
        ongoing = ["--skip-ongoing"] if cfg["skip_ongoing"] else []
        novid   = ["--skip-videos"]  if cfg["skip_videos"]  else []

        self.script_queue = []
        if cfg["do_canvas"]:
            self.script_queue.append((
                "canvas_downloader.py",
                ["--dir", out] + ongoing + novid,
            ))
        if cfg["do_external"]:
            self.script_queue.append((
                "external_downloader.py", ["--dir", out],
            ))
        if cfg["do_panopto"]:
            self.script_queue.append((
                "panopto_downloader.py",
                ["--dir", out] + ongoing,
            ))
        if cfg["do_reserves"]:
            self.script_queue.append((
                "reserves_downloader.py",
                ["--dir", out] + ongoing,
            ))

        if not self.script_queue:
            messagebox.showwarning(
                "Nothing selected",
                "Please select at least one type to download.",
            )
            return

        Path(out).mkdir(parents=True, exist_ok=True)

        if SENTINEL_FILE.exists():
            SENTINEL_FILE.unlink()

        self.running = True
        self._close_login_popup()
        self._clear_log()

        self.start_btn.config(state="disabled", bg="#888")
        self.stop_btn.config(state="normal", bg="#c0392b", fg="white")

        self._log("━" * 52 + "\n", "header")
        self._log("  Canvas Archive — Starting\n", "header")
        self._log(f"  Saving to: {out}\n", "info")
        self._log("━" * 52 + "\n\n", "header")

        self._run_next_script()

    def _stop(self):
        self.running = False
        self._stop_dots()
        self._close_login_popup()
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None
        if SENTINEL_FILE.exists():
            try:
                SENTINEL_FILE.unlink()
            except Exception:
                pass
        self.start_btn.config(
            state="normal", bg=GREEN, fg="white",
            text="Start Download  ▶",
        )
        self.stop_btn.config(
            state="disabled", bg="#e0d8cc", fg=NAVY,
        )
        self.status_var.set("Stopped — click Start Download to begin again.")
        self.status_lbl.config(fg="#c0392b")
        self._log("\n  Stopped.\n", "warn")

    def _run_next_script(self):
        if not self.running:
            return
        if not self.script_queue:
            self._all_done()
            return

        script_name, args = self.script_queue.pop(0)

        if not (SCRIPT_DIR / script_name).exists():
            self._log(f"  {script_name} not found — skipping.\n", "warn")
            self.root.after(200, self._run_next_script)
            return

        friendly = {
            "canvas_downloader.py":   "Downloading course files",
            "external_downloader.py": "Downloading external readings",
            "panopto_downloader.py":  "Downloading lecture recordings",
            "reserves_downloader.py": "Downloading library reserves",
        }.get(script_name, script_name)

        self._stop_dots()
        self._start_dots(friendly)

        self._log(f"\n  {friendly}…\n", "header")

        env = os.environ.copy()
        env["CANVAS_ARCHIVE_GUI"]       = "1"
        env["PYTHONUNBUFFERED"]         = "1"
        env["PYTHONDONTWRITEBYTECODE"]  = "1"
        env["CANVAS_ARCHIVE_DATA_DIR"]  = str(DATA_DIR)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(_playwright_browsers_dir())

        cmd = [sys.executable, "--run-script", script_name] + args

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                bufsize=1,
                universal_newlines=True,
                cwd=str(DATA_DIR),
                env=env,
            )
        except Exception as exc:
            self._log(f"  Could not start {script_name}: {exc}\n", "error")
            self.root.after(500, self._run_next_script)
            return

        self._log(f"  Started (PID {self.process.pid})\n", "dim")

        def _reader():
            try:
                for line in self.process.stdout:
                    self.log_queue.put(("line", line))
            except Exception:
                pass
            self.process.wait()
            self.log_queue.put(("done", self.process.returncode))

        threading.Thread(target=_reader, daemon=True).start()

    def _all_done(self):
        self.running = False
        self.process = None
        self._stop_dots()
        self._close_login_popup()
        if SENTINEL_FILE.exists():
            try:
                SENTINEL_FILE.unlink()
            except Exception:
                pass
        self.start_btn.config(
            state="normal", bg=GREEN, fg="white",
            text="Start Download  ▶",
        )
        self.stop_btn.config(
            state="disabled", bg="#e0d8cc", fg=NAVY,
        )
        out = self.output_dir.get()
        self.status_var.set("All done! Click Start to run again.")
        self.status_lbl.config(fg=GREEN)
        self._log("\n" + "━" * 52 + "\n", "success")
        self._log("  All downloads complete!\n", "success")
        self._log(f"  Files saved to:\n  {out}\n", "success")
        self._log("━" * 52 + "\n", "success")
        messagebox.showinfo(
            "Download Complete!",
            f"All done!\n\nYour files have been saved to:\n\n{out}",
            parent=self.root,
        )

    # ── Log ───────────────────────────────────────────────────────────────────

    def _clear_log(self):
        self.log_text.config(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.config(state="disabled")

    def _log(self, text, tag=None):
        self.log_text.config(state="normal")
        self.log_text.insert("end", text, (tag,) if tag else ())
        self.log_text.see("end")
        self.log_text.config(state="disabled")

    def _poll_log(self):
        try:
            while True:
                kind, data = self.log_queue.get_nowait()
                if kind == "line":
                    line = data
                    if any(c in line for c in ("✓", "Downloaded", "complete")):
                        tag = "success"
                    elif any(c in line for c in ("✗", "FAILED", "Error", "Traceback")):
                        tag = "error"
                    elif any(c in line for c in ("WARNING", "timed out", "Waiting")):
                        tag = "warn"
                    elif any(c in line for c in ("===", "---", "━", "Starting", "Saving")):
                        tag = "header"
                    elif any(p in line for p in _LOGIN_PHRASES):
                        tag = "login"
                    else:
                        tag = None
                    self._log(line, tag)
                    if any(p in line for p in _LOGIN_PHRASES):
                        if self._login_popup is None:
                            self._stop_dots()
                            self.root.after(300, self._show_login_popup)
                elif kind == "done":
                    rc = data
                    self._stop_dots()
                    self._close_login_popup()
                    self._log(
                        "  Step complete.\n" if rc == 0
                        else f"  Finished with exit code {rc}.\n",
                        "success" if rc == 0 else "warn",
                    )
                    self.root.after(600, self._run_next_script)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)


# ─────────────────────────────────────────────────────────────────────────────
#  Startup checks
# ─────────────────────────────────────────────────────────────────────────────

def check_scripts() -> bool:
    missing = [s for s in REQUIRED_SCRIPTS
               if not (SCRIPT_DIR / s).exists()]
    if missing:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Missing files",
            "Some required files are missing:\n\n"
            + "\n".join(f"  • {s}" for s in missing)
            + "\n\nMake sure all files are in the same folder.",
        )
        root.destroy()
        return False
    return True


def check_packages() -> bool:
    missing = []
    for pkg in ["requests", "playwright", "yt_dlp", "tqdm"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg.replace("_", "-"))
    if missing and not getattr(sys, "frozen", False):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Setup incomplete",
            "Some packages are not installed.\n\n"
            "Please run:\n  pip install " + " ".join(missing),
        )
        root.destroy()
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not check_scripts():
        sys.exit(1)
    if not check_packages():
        sys.exit(1)

    # Single instance check — prevents multiple windows
    if not _acquire_lock():
        # Another instance is running — bring it to front and exit
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Already running",
            "Canvas Archive is already open!\n\n"
            "Check your Dock or taskbar.",
        )
        root.destroy()
        sys.exit(0)

    root = tk.Tk()
    root.update_idletasks()
    CanvasArchiveApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
