#!/usr/bin/env python3
"""
canvas_archive.py  ·  app version
====================================
Canvas Archive — matching archive-your-canvas.lovable.app
Built with CustomTkinter.
"""

# ══════════════════════════════════════════════════════════════════════════════
#  --run-script dispatch   (MUST be first — no GUI imports above this block)
# ══════════════════════════════════════════════════════════════════════════════
import sys, os, re

if len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
    script_name = sys.argv[2]
    script_args = sys.argv[3:]

    if getattr(sys, "frozen", False):
        script_dir = sys._MEIPASS
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))

    env_data = os.environ.get("CANVAS_ARCHIVE_DATA_DIR", "")
    if env_data:
        data_dir = env_data
    elif getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            data_dir = os.path.join(os.path.expanduser("~"),
                                    "Library", "Application Support", "Canvas Archive")
        elif sys.platform.startswith("win"):
            data_dir = os.path.join(
                os.environ.get("APPDATA", os.path.expanduser("~")), "Canvas Archive")
        else:
            data_dir = os.path.join(os.path.expanduser("~"), ".canvas-archive")
    else:
        data_dir = script_dir

    os.makedirs(data_dir, exist_ok=True)

    # KEY FIX: data_dir first so the GUI's canvas_config.py takes precedence
    # over any stale bundled version — this fixes Canvas URL not being passed
    # correctly to the downloader scripts.
    for d in (data_dir, script_dir):
        if d not in sys.path:
            sys.path.insert(0, d)

    os.chdir(data_dir)

    script_path = os.path.join(script_dir, script_name)
    if not os.path.exists(script_path):
        print(f"[ERROR] Script not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    import importlib.util
    spec   = importlib.util.spec_from_file_location("_canvas_script", script_path)
    module = importlib.util.module_from_spec(spec)
    sys.argv = [script_path] + script_args
    try:
        spec.loader.exec_module(module)
        module.main()
    except SystemExit:
        raise
    except Exception:
        import traceback; traceback.print_exc()
        sys.exit(1)
    sys.exit(0)


# ══════════════════════════════════════════════════════════════════════════════
#  Normal GUI imports
# ══════════════════════════════════════════════════════════════════════════════
import json, queue, subprocess, threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter.ttk as ttk

import customtkinter as ctk

# Must be set before any CTk widget is created
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")   # we override everything manually below


# ──────────────────────────────────────────────────────────────────────────────
#  Design tokens  ·  matching archive-your-canvas.lovable.app
# ──────────────────────────────────────────────────────────────────────────────
CREAM     = "#f5f0e8"   # warm notebook paper
NAVY      = "#1a1a2e"   # text, borders, almost-black
PURPLE    = "#4a00b0"   # header, accents, checkmarks
PURPLE_L  = "#ede8ff"   # light purple — checked toggle fill
GREEN     = "#2d8a3e"   # Start button
GREEN_D   = "#236b31"   # Start button hover
RED       = "#c0392b"   # Stop button (active)
WHITE     = "#ffffff"   # card background
LOG_BG    = "#0d0d1a"   # terminal background
LOG_FG    = "#4ade80"   # terminal green text
LINE_CLR  = "#ddd8ce"   # notebook ruled-line colour


# ──────────────────────────────────────────────────────────────────────────────
#  Paths
# ──────────────────────────────────────────────────────────────────────────────
def _get_script_dir() -> Path:
    return Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).parent

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
    return Path.home() / ".cache" / "ms-playwright"

def _abs(p: str) -> str:
    return str(Path(p).expanduser().resolve())

SCRIPT_DIR    = _get_script_dir()
DATA_DIR      = _get_data_dir()
CONFIG_FILE   = DATA_DIR / "canvas_config.json"
SENTINEL_FILE = DATA_DIR / "gui_login_ready.txt"
LOCK_FILE     = DATA_DIR / ".canvas_archive.lock"


# ──────────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────────
COMMON_CANVAS_URLS = [
    "https://canvas.harvard.edu",   "https://canvas.yale.edu",
    "https://canvas.mit.edu",       "https://canvas.stanford.edu",
    "https://canvas.princeton.edu", "https://canvas.columbia.edu",
    "https://canvas.cornell.edu",   "https://canvas.upenn.edu",
    "https://canvas.dartmouth.edu", "https://canvas.brown.edu",
    "https://canvas.uchicago.edu",  "https://canvas.duke.edu",
    "https://canvas.northwestern.edu", "https://canvas.vanderbilt.edu",
    "https://canvas.emory.edu",     "https://canvas.georgetown.edu",
    "https://canvas.bu.edu",        "https://canvas.bc.edu",
    "https://canvas.tufts.edu",     "https://canvas.nyu.edu",
    "https://canvas.usc.edu",       "https://canvas.virginia.edu",
    "https://canvas.wustl.edu",
]

REQUIRED_SCRIPTS = [
    "canvas_auth.py", "canvas_downloader.py", "external_downloader.py",
    "panopto_downloader.py", "reserves_downloader.py",
]

_LOGIN_PHRASES = [
    "Press ENTER", "press ENTER", "press Enter", "Press Enter",
    "ENTER after you are logged in", "ENTER once signed in",
    "[Press ENTER", "Waiting for GUI login",
    "Canvas Login Required", "Login Required", "Login required",
]

_AUTH_OK_PHRASES = [
    "[CANVAS_AUTH_OK]", "Already logged in — saving session",
    "Logged in — session saved", "Using saved session cookies",
    "Using saved cookies",
]


# ──────────────────────────────────────────────────────────────────────────────
#  Config
# ──────────────────────────────────────────────────────────────────────────────
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

def write_canvas_config(canvas_url: str, panopto_url: str,
                        output_dir: str = "") -> None:
    (DATA_DIR / "canvas_config.py").write_text(
        f"CANVAS_BASE_URL  = {canvas_url!r}\n"
        f"PANOPTO_BASE_URL = {panopto_url!r}\n",
        encoding="utf-8",
    )
    cfg = {"canvas_url": canvas_url, "panopto_url": panopto_url}
    if output_dir:
        cfg["output_dir"] = _abs(output_dir)
    (DATA_DIR / "canvas_config.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8"
    )


# ──────────────────────────────────────────────────────────────────────────────
#  Browser helpers
# ──────────────────────────────────────────────────────────────────────────────
def _chromium_exe() -> Path | None:
    base = _playwright_browsers_dir()
    if not base.exists():
        return None
    for pat in [
        "chromium*/chrome-mac-arm64/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing",
        "chromium*/chrome-mac-x64/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing",
        "chromium*/chrome-win/chrome.exe",
        "chromium*/chrome-linux/chrome",
    ]:
        matches = list(base.glob(pat))
        if matches:
            return matches[0]
    return None

def _browser_installed() -> bool:
    exe = _chromium_exe()
    return exe is not None and exe.exists()

def install_browser_dialog(parent) -> bool:
    """First-time 150 MB Chromium download with progress."""
    win = tk.Toplevel(parent)
    win.title("One-time setup")
    win.configure(bg=CREAM)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.protocol("WM_DELETE_WINDOW", lambda: None)
    pw, ph = 520, 260
    win.geometry(
        f"{pw}x{ph}+"
        f"{(win.winfo_screenwidth()-pw)//2}+"
        f"{(win.winfo_screenheight()-ph)//2}"
    )

    border = tk.Frame(win, bg=NAVY, padx=2, pady=2)
    border.pack(fill="both", expand=True, padx=20, pady=20)
    card = tk.Frame(border, bg=CREAM, padx=24, pady=20)
    card.pack(fill="both", expand=True)

    tk.Label(card, text="One-time setup  🎓",
             font=("Helvetica", 16, "bold"), bg=CREAM, fg=NAVY).pack(pady=(0, 6))
    tk.Label(card,
             text="Downloading a browser for Canvas Archive to use.\n"
                  "About 150 MB — only happens once.\n"
                  "Please leave this window open.",
             font=("Helvetica", 11), bg=CREAM, fg=NAVY, justify="center",
             ).pack(pady=(0, 14))

    bar = ttk.Progressbar(card, mode="indeterminate", length=420)
    bar.pack(pady=(0, 8))
    bar.start(10)

    sv = tk.StringVar(value="Starting download…")
    tk.Label(card, textvariable=sv,
             font=("Helvetica", 10), fg=PURPLE, bg=CREAM).pack()

    result = {"ok": False}

    def _run():
        try:
            env = os.environ.copy()
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(_playwright_browsers_dir())
            proc = subprocess.Popen(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, env=env,
            )
            for line in proc.stdout:
                stripped = line.strip()
                if stripped:
                    sv.set(stripped[:68])
            proc.wait()
            result["ok"] = proc.returncode == 0 and _browser_installed()
            sv.set("Browser ready! ✓" if result["ok"]
                   else "Download failed — please try again.")
        except Exception as exc:
            sv.set(f"Error: {exc}")
        finally:
            bar.stop()
            win.after(1800, win.destroy)

    threading.Thread(target=_run, daemon=True).start()
    win.wait_window()
    return result["ok"]


# ──────────────────────────────────────────────────────────────────────────────
#  Single-instance lock
# ──────────────────────────────────────────────────────────────────────────────
def _acquire_lock() -> bool:
    try:
        import fcntl
        fd = open(LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid())); fd.flush()
        _acquire_lock._fd = fd
        return True
    except (IOError, OSError):
        return False
    except ImportError:
        try:
            if LOCK_FILE.exists():
                try:
                    pid = int(LOCK_FILE.read_text().strip())
                    import ctypes as _c
                    h = _c.windll.kernel32.OpenProcess(1, False, pid)
                    if h:
                        _c.windll.kernel32.CloseHandle(h)
                        return False
                except Exception:
                    pass
            LOCK_FILE.write_text(str(os.getpid()))
            return True
        except Exception:
            return True


# ──────────────────────────────────────────────────────────────────────────────
#  Main Application
# ──────────────────────────────────────────────────────────────────────────────
class CanvasArchiveApp:

    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("Canvas Archive")
        self.root.resizable(True, True)
        self.root.configure(fg_color=CREAM)

        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w  = min(860, int(sw * 0.88))
        h  = min(800, int(sh * 0.88))
        self.root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        cfg = load_config()
        self.canvas_url   = tk.StringVar(value=cfg["canvas_url"])
        self.panopto_url  = tk.StringVar(value=cfg.get("panopto_url",
                                         "https://harvard.hosted.panopto.com"))
        self.output_dir   = tk.StringVar(value=cfg["output_dir"])
        self.skip_ongoing = tk.BooleanVar(value=cfg["skip_ongoing"])
        self.skip_videos  = tk.BooleanVar(value=cfg["skip_videos"])
        self.do_canvas    = tk.BooleanVar(value=cfg["do_canvas"])
        self.do_external  = tk.BooleanVar(value=cfg["do_external"])
        self.do_panopto   = tk.BooleanVar(value=cfg["do_panopto"])
        self.do_reserves  = tk.BooleanVar(value=cfg["do_reserves"])

        self.running              = False
        self.process              = None
        self.log_queue            = queue.Queue()
        self.script_queue         = []
        self._login_popup         = None
        self._dot_job             = None
        self._last_was_progress   = False
        self._caffeinate_proc     = None
        self._url_combo           = None   # CTkComboBox reference

        if SENTINEL_FILE.exists():
            try: SENTINEL_FILE.unlink()
            except Exception: pass

        self._build_ui()
        self._poll_log()
        self.root.after(800, self._check_browser)

    # ── Browser check ─────────────────────────────────────────────────────────

    def _check_browser(self):
        if not _browser_installed():
            ok = install_browser_dialog(self.root)
            if not ok:
                messagebox.showwarning(
                    "Browser not installed",
                    "Canvas Archive needs a browser to log in to Canvas.\n"
                    "Please restart the app to try again.",
                )

    # ── Build UI ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Purple header ──────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=PURPLE, height=58)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)
        tk.Label(
            header, text="Canvas Archive",
            font=("Helvetica Neue", 19, "bold"),
            fg="white", bg=PURPLE,
        ).pack(side="left", padx=24, pady=16)
        tk.Label(
            header, text="★ free for graduating students ★",
            font=("Georgia", 9, "italic"),
            fg="#c4a8ff", bg=PURPLE,
        ).pack(side="right", padx=24, pady=20)

        # ── Fixed bottom bar ───────────────────────────────────────────────────
        bot = tk.Frame(self.root, bg=CREAM, pady=14, padx=24)
        bot.pack(fill="x", side="bottom")

        self.start_btn = ctk.CTkButton(
            bot,
            text="Start Download  ▶",
            font=ctk.CTkFont(family="Helvetica Neue", size=15, weight="bold"),
            fg_color=GREEN,
            hover_color=GREEN_D,
            text_color="white",
            corner_radius=10,
            height=48,
            command=self._start,
        )
        self.start_btn.pack(fill="x", pady=(0, 8))

        self.stop_btn = ctk.CTkButton(
            bot,
            text="⏹  Stop",
            font=ctk.CTkFont(family="Helvetica Neue", size=11),
            fg_color="#e8e3dc",
            hover_color=RED,
            text_color="#888888",
            corner_radius=8,
            height=36,
            state="disabled",
            command=self._stop,
        )
        self.stop_btn.pack(fill="x")

        self.status_var = tk.StringVar(value="Ready — click Start Download to begin.")
        self.status_lbl = tk.Label(
            bot, textvariable=self.status_var,
            font=("Helvetica", 10), fg=PURPLE, bg=CREAM,
        )
        self.status_lbl.pack(pady=(8, 0))

        # ── Scrollable middle with notebook-paper background ───────────────────
        sc = tk.Frame(self.root, bg=CREAM)
        sc.pack(fill="both", expand=True)

        self._bg = tk.Canvas(sc, bg=CREAM, highlightthickness=0)
        vsb = ctk.CTkScrollbar(
            sc, command=self._bg.yview,
            button_color="#555577", button_hover_color=PURPLE,
        )
        self._bg.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._bg.pack(side="left", fill="both", expand=True)

        self.main = tk.Frame(self._bg, bg=CREAM, padx=20, pady=16)
        self._cw  = self._bg.create_window((0, 0), window=self.main, anchor="nw")

        self._bg.bind("<Configure>", self._on_bg_resize)
        self.main.bind("<Configure>", self._on_content_resize)
        for evt in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self._bg.bind_all(evt, self._on_scroll)

        self._build_what()
        self._build_settings()
        self._build_options()
        self._build_log()

    # ── Canvas/scroll callbacks ───────────────────────────────────────────────

    def _on_bg_resize(self, e):
        self._bg.itemconfig(self._cw, width=e.width)
        self._redraw_lines()

    def _on_content_resize(self, e):
        self._bg.configure(scrollregion=self._bg.bbox("all"))
        self._redraw_lines()

    def _redraw_lines(self):
        """Draw notebook ruled lines across the full scroll area."""
        bbox = self._bg.bbox("all")
        w    = self._bg.winfo_width()
        h    = (bbox[3] if bbox else 0) + 60
        self._bg.delete("nblines")
        for y in range(0, h, 28):
            self._bg.create_line(0, y, max(w, 100), y,
                                  fill=LINE_CLR, width=1, tags="nblines")
        self._bg.tag_lower("nblines")

    def _on_scroll(self, e):
        if e.num == 4:
            self._bg.yview_scroll(-1, "units")
        elif e.num == 5:
            self._bg.yview_scroll(1, "units")
        else:
            self._bg.yview_scroll(int(-1 * (e.delta / 120)), "units")

    # ── Card helper ───────────────────────────────────────────────────────────

    def _card(self, title: str | None = None) -> tk.Frame:
        """White card with thick navy border, matching the Lovable site."""
        outer = tk.Frame(self.main, bg=NAVY, padx=2, pady=2)
        outer.pack(fill="x", pady=(0, 16))
        inner = tk.Frame(outer, bg=WHITE, padx=20, pady=16)
        inner.pack(fill="both", expand=True)
        if title:
            tk.Label(
                inner, text=title,
                font=("Georgia", 12, "italic"),
                fg=PURPLE, bg=WHITE,
            ).pack(anchor="w", pady=(0, 12))
        return inner

    # ── "What to download" pill toggles ───────────────────────────────────────

    def _build_what(self):
        card = self._card("What would you like to download?")
        grid = tk.Frame(card, bg=WHITE)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        for i, (var, label, desc) in enumerate([
            (self.do_canvas,   "Course files",
             "Every PDF, slide deck, video & document"),
            (self.do_external, "External readings",
             "JSTOR, Google Drive, linked content"),
            (self.do_panopto,  "Lecture recordings",
             "Panopto videos, sorted by course"),
            (self.do_reserves, "Library reserves",
             "Articles & book chapters on reserve"),
        ]):
            row, col = divmod(i, 2)
            self._pill_toggle(grid, var, label, desc, row, col)

    def _pill_toggle(self, parent, var: tk.BooleanVar,
                     label: str, desc: str, row: int, col: int):
        """
        Pill-shaped toggle matching the app mockup:
          ON  — light purple fill, filled purple circle
          OFF — white fill, empty grey circle
        Click anywhere on the card to toggle.
        """
        outer = tk.Frame(parent, bg=NAVY, padx=2, pady=2)
        outer.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
        inner = tk.Frame(outer, bg=WHITE, padx=14, pady=12)
        inner.pack(fill="both", expand=True)

        def toggle(*_):
            var.set(not var.get())

        def refresh(*_):
            bg       = PURPLE_L if var.get() else WHITE
            icon     = "●" if var.get() else "○"
            icon_clr = PURPLE   if var.get() else "#cccccc"
            for w in (inner, row_f):
                w.configure(bg=bg)
            icon_lbl.configure(bg=bg, text=icon, fg=icon_clr)
            lbl.configure(bg=bg)
            desc_lbl.configure(bg=bg)

        row_f = tk.Frame(inner, bg=WHITE)
        row_f.pack(fill="x")

        icon_lbl = tk.Label(
            row_f,
            text="●" if var.get() else "○",
            font=("Helvetica", 17),
            fg=PURPLE if var.get() else "#cccccc",
            bg=WHITE, width=2,
        )
        icon_lbl.pack(side="left")

        lbl = tk.Label(
            row_f, text=label,
            font=("Helvetica Neue", 12, "bold"),
            fg=NAVY, bg=WHITE, anchor="w",
        )
        lbl.pack(side="left", padx=(6, 0))

        desc_lbl = tk.Label(
            inner, text=desc,
            font=("Helvetica", 9),
            fg="#666666", bg=WHITE, anchor="w",
        )
        desc_lbl.pack(fill="x", pady=(3, 0))

        for w in (inner, row_f, icon_lbl, lbl, desc_lbl):
            w.bind("<Button-1>", lambda e: toggle())

        var.trace_add("write", refresh)
        refresh()

    # ── Settings ──────────────────────────────────────────────────────────────

    def _build_settings(self):
        card = self._card("Settings")

        # Canvas URL
        r1 = tk.Frame(card, bg=WHITE)
        r1.pack(fill="x", pady=4)
        tk.Label(
            r1, text="Canvas URL", width=11,
            font=("Helvetica", 11, "bold"),
            fg=NAVY, bg=WHITE, anchor="w",
        ).pack(side="left")
        self._url_combo = ctk.CTkComboBox(
            r1,
            variable=self.canvas_url,
            values=COMMON_CANVAS_URLS,
            width=400,
            fg_color=WHITE,
            text_color=NAVY,
            border_color=NAVY,
            border_width=2,
            button_color=NAVY,
            button_hover_color=PURPLE,
            dropdown_fg_color=WHITE,
            dropdown_text_color=NAVY,
            dropdown_hover_color=PURPLE_L,
            font=ctk.CTkFont(family="Helvetica", size=11),
        )
        self._url_combo.pack(side="left", padx=(8, 0))

        # Save-to directory
        r2 = tk.Frame(card, bg=WHITE)
        r2.pack(fill="x", pady=4)
        tk.Label(
            r2, text="Save to", width=11,
            font=("Helvetica", 11, "bold"),
            fg=NAVY, bg=WHITE, anchor="w",
        ).pack(side="left")
        ctk.CTkEntry(
            r2,
            textvariable=self.output_dir,
            width=320,
            fg_color=WHITE,
            text_color=NAVY,
            border_color=NAVY,
            border_width=2,
            font=ctk.CTkFont(family="Helvetica", size=11),
        ).pack(side="left", padx=(8, 8))
        ctk.CTkButton(
            r2, text="Browse…",
            font=ctk.CTkFont(family="Helvetica", size=10),
            fg_color=CREAM,
            text_color=NAVY,
            hover_color="#e8e3dc",
            border_color=NAVY,
            border_width=1,
            corner_radius=6,
            width=80, height=30,
            command=self._browse,
        ).pack(side="left")

    # ── Options ───────────────────────────────────────────────────────────────

    def _build_options(self):
        card = self._card()
        for var, text in [
            (self.skip_ongoing, "Skip administrative / ongoing courses"),
            (self.skip_videos,  "Skip video files  (saves disk space)"),
        ]:
            ctk.CTkCheckBox(
                card, text=text, variable=var,
                font=ctk.CTkFont(family="Helvetica", size=11),
                text_color=NAVY,
                fg_color=PURPLE,
                hover_color=PURPLE_L,
                checkmark_color=WHITE,
                border_color=NAVY,
                border_width=2,
                corner_radius=4,
            ).pack(anchor="w", pady=3)

    # ── Terminal log ──────────────────────────────────────────────────────────

    def _build_log(self):
        """
        Dark terminal log. Uses standard tk.Text (not CTkTextbox) so that
        colour tags (success/error/warn etc.) work correctly.
        """
        outer = tk.Frame(self.main, bg=NAVY, padx=2, pady=2)
        outer.pack(fill="both", expand=True, pady=(0, 8))

        holder = tk.Frame(outer, bg=LOG_BG)
        holder.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            holder,
            height=13,
            font=("Courier", 10),
            bg=LOG_BG, fg=LOG_FG,
            insertbackground=LOG_FG,
            state="disabled",
            relief="flat",
            borderwidth=0,
            padx=12, pady=10,
            wrap="word",
        )
        log_vsb = ctk.CTkScrollbar(
            holder, command=self.log_text.yview,
            button_color="#333355", button_hover_color=PURPLE,
        )
        self.log_text.configure(yscrollcommand=log_vsb.set)
        log_vsb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        for tag, colour in [
            ("success",  "#4ade80"),
            ("error",    "#ff6b6b"),
            ("warn",     "#ffd93d"),
            ("info",     "#74b9ff"),
            ("header",   "#c4a8ff"),
            ("dim",      "#444466"),
            ("login",    "#ffd93d"),
            ("progress", "#74b9ff"),
        ]:
            self.log_text.tag_config(tag, foreground=colour)

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
        pw, ph = 540, 290
        popup.geometry(
            f"{pw}x{ph}+"
            f"{(popup.winfo_screenwidth()-pw)//2}+"
            f"{(popup.winfo_screenheight()-ph)//2}"
        )
        popup.attributes("-topmost", True)
        popup.protocol("WM_DELETE_WINDOW", lambda: None)
        self._login_popup = popup

        border = tk.Frame(popup, bg=NAVY, padx=2, pady=2)
        border.pack(fill="both", expand=True, padx=20, pady=20)
        inner = tk.Frame(border, bg=CREAM, padx=24, pady=20)
        inner.pack(fill="both", expand=True)

        tk.Label(
            inner, text="Log in to Canvas",
            font=("Helvetica Neue", 16, "bold"),
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
            bg=CREAM, fg=NAVY, justify="center",
        ).pack(pady=(0, 16))
        ctk.CTkButton(
            inner,
            text="  ✓  I'm logged in — continue  ",
            font=ctk.CTkFont(family="Helvetica Neue", size=13, weight="bold"),
            fg_color=GREEN,
            hover_color=GREEN_D,
            text_color="white",
            corner_radius=10,
            height=44,
            command=self._confirm_login,
        ).pack()
        popup.focus_force()
        self._set_status("Waiting for login…")

    def _close_login_popup(self):
        if self._login_popup:
            try: self._login_popup.destroy()
            except Exception: pass
            self._login_popup = None

    def _confirm_login(self):
        try:
            SENTINEL_FILE.write_text("ready", encoding="utf-8")
        except Exception as exc:
            self._log(f"  Could not write sentinel: {exc}\n", "warn")
        self._close_login_popup()
        self._log("  Logged in — continuing…\n\n", "success")
        self._set_status("Continuing download…")

    # ── Status + animated dots ────────────────────────────────────────────────

    def _set_status(self, text: str, fg: str = PURPLE):
        self.status_var.set(text)
        self.status_lbl.configure(fg=fg)

    def _start_dots(self, base: str):
        self._dot_base  = base
        self._dot_count = 0
        self._animate_dots()

    def _animate_dots(self):
        if not self.running or self._login_popup:
            return
        self._dot_count = (self._dot_count + 1) % 4
        self._set_status(f"{self._dot_base}{'.' * self._dot_count}")
        self._dot_job = self.root.after(600, self._animate_dots)

    def _stop_dots(self):
        if self._dot_job:
            self.root.after_cancel(self._dot_job)
            self._dot_job = None

    # ── Sleep prevention ──────────────────────────────────────────────────────

    def _start_caffeinate(self):
        import platform
        if platform.system() == "Darwin":
            try:
                self._caffeinate_proc = subprocess.Popen(
                    ["caffeinate", "-dims"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                self._caffeinate_proc = None

    def _stop_caffeinate(self):
        if self._caffeinate_proc:
            try: self._caffeinate_proc.terminate()
            except Exception: pass
            self._caffeinate_proc = None

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

        if not _browser_installed():
            ok = install_browser_dialog(self.root)
            if not ok:
                messagebox.showerror(
                    "Browser required",
                    "Canvas Archive needs a browser to work.\nPlease try again.",
                )
                return

        # Read URL from the combo widget directly (most reliable)
        canvas_url = (
            self._url_combo.get() if self._url_combo
            else self.canvas_url.get()
        ).strip().rstrip("/")

        if not canvas_url.startswith("http"):
            messagebox.showerror(
                "Invalid URL",
                "Please enter a valid Canvas URL starting with https://",
            )
            return

        out = _abs(self.output_dir.get().strip())
        cfg = {
            "canvas_url":   canvas_url,
            "panopto_url":  self.panopto_url.get().strip().rstrip("/"),
            "output_dir":   out,
            "skip_ongoing": self.skip_ongoing.get(),
            "skip_videos":  self.skip_videos.get(),
            "do_canvas":    self.do_canvas.get(),
            "do_external":  self.do_external.get(),
            "do_panopto":   self.do_panopto.get(),
            "do_reserves":  self.do_reserves.get(),
        }
        save_config(cfg)
        write_canvas_config(cfg["canvas_url"], cfg["panopto_url"], out)

        ongoing = ["--skip-ongoing"] if cfg["skip_ongoing"] else []
        novid   = ["--skip-videos"]  if cfg["skip_videos"]  else []

        self.script_queue = []
        if cfg["do_canvas"]:
            self.script_queue.append(
                ("canvas_downloader.py", ["--dir", out] + ongoing + novid))
        if cfg["do_external"]:
            self.script_queue.append(
                ("external_downloader.py", ["--dir", out]))
        if cfg["do_panopto"]:
            self.script_queue.append(
                ("panopto_downloader.py", ["--dir", out] + ongoing))
        if cfg["do_reserves"]:
            self.script_queue.append(
                ("reserves_downloader.py", ["--dir", out] + ongoing))

        if not self.script_queue:
            messagebox.showwarning(
                "Nothing selected",
                "Please select at least one type of content to download.",
            )
            return

        Path(out).mkdir(parents=True, exist_ok=True)

        if SENTINEL_FILE.exists():
            try: SENTINEL_FILE.unlink()
            except Exception: pass

        self.running            = True
        self._last_was_progress = False
        self._close_login_popup()
        self._clear_log()
        self._start_caffeinate()

        self.start_btn.configure(state="disabled", fg_color="#aaaaaa")
        self.stop_btn.configure(state="normal", fg_color=RED, text_color="white")

        self._log("━" * 52 + "\n", "header")
        self._log("  Canvas Archive — Starting\n", "header")
        self._log(f"  Saving to: {out}\n", "info")
        self._log("━" * 52 + "\n\n", "header")

        self._run_next_script()

    def _stop(self):
        self.running = False
        self._stop_dots()
        self._close_login_popup()
        self._stop_caffeinate()
        if self.process:
            try: self.process.terminate()
            except Exception: pass
            self.process = None
        if SENTINEL_FILE.exists():
            try: SENTINEL_FILE.unlink()
            except Exception: pass
        self.start_btn.configure(
            state="normal", fg_color=GREEN, text="Start Download  ▶")
        self.stop_btn.configure(
            state="disabled", fg_color="#e8e3dc", text_color="#888888")
        self._set_status("Stopped — click Start Download to begin again.", RED)
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
        self._last_was_progress = False
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
        self._stop_caffeinate()
        if SENTINEL_FILE.exists():
            try: SENTINEL_FILE.unlink()
            except Exception: pass

        self.start_btn.configure(
            state="normal", fg_color=GREEN, text="Start Download  ▶")
        self.stop_btn.configure(
            state="disabled", fg_color="#e8e3dc", text_color="#888888")

        out = self.output_dir.get()
        self._set_status("All done!  Click Start to run again.", GREEN)
        self._log("\n" + "━" * 52 + "\n", "success")
        self._log("  All downloads complete!\n", "success")
        self._log(f"  Files saved to:\n  {out}\n", "success")
        self._log("━" * 52 + "\n", "success")
        messagebox.showinfo(
            "Download Complete! 🎓",
            f"All done!\n\nYour files have been saved to:\n\n{out}",
            parent=self.root,
        )

    # ── Log helpers ───────────────────────────────────────────────────────────

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._last_was_progress = False

    def _log(self, text: str, tag: str | None = None):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text, (tag,) if tag else ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._last_was_progress = False

    def _log_progress(self, fname: str, pct: str):
        """Update a single in-place progress line instead of spamming new ones."""
        text = f"  ↓ {fname[:56]}… {pct}%\n"
        self.log_text.configure(state="normal")
        if self._last_was_progress:
            ranges = self.log_text.tag_ranges("_progress")
            if ranges:
                self.log_text.delete(str(ranges[0]), str(ranges[-1]))
                self.log_text.insert(
                    str(ranges[0]), text, ("_progress", "progress"))
            else:
                self.log_text.insert("end", text, ("_progress", "progress"))
        else:
            self.log_text.tag_remove("_progress", "1.0", "end")
            self.log_text.insert("end", text, ("_progress", "progress"))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._last_was_progress = True

    def _poll_log(self):
        try:
            while True:
                kind, data = self.log_queue.get_nowait()

                if kind == "line":
                    line = data

                    # Tqdm progress — update in place
                    if "%|" in line:
                        m = re.search(r"↓\s+(.+?):\s*(\d+)%", line)
                        if m:
                            self._log_progress(m.group(1).strip(), m.group(2))
                            self._set_status(
                                f"↓ {m.group(1).strip()[:45]}… {m.group(2)}%")
                        continue

                    if self._last_was_progress and not line.strip():
                        self._last_was_progress = False
                        continue

                    if any(c in line for c in
                           ("✓", "Downloaded", "complete", "exists")):
                        tag = "success"
                    elif any(c in line for c in
                             ("✗", "FAILED", "Error", "Traceback")):
                        tag = "error"
                    elif any(c in line for c in
                             ("WARNING", "timed out", "Waiting")):
                        tag = "warn"
                    elif any(c in line for c in
                             ("━", "Starting", "Saving", "Scanning",
                              "Found", "Total", "Fetching", "Processing")):
                        tag = "header"
                    elif any(p in line for p in _LOGIN_PHRASES):
                        tag = "login"
                    else:
                        tag = None

                    self._log(line, tag)

                    if any(p in line for p in _AUTH_OK_PHRASES):
                        self._close_login_popup()
                    elif any(p in line for p in _LOGIN_PHRASES):
                        if self._login_popup is None:
                            self._stop_dots()
                            self.root.after(300, self._show_login_popup)

                elif kind == "done":
                    rc = data
                    self._stop_dots()
                    self._close_login_popup()
                    self._last_was_progress = False
                    self._log(
                        "  Step complete.\n" if rc == 0
                        else f"  Finished with exit code {rc}.\n",
                        "success" if rc == 0 else "warn",
                    )
                    self.root.after(600, self._run_next_script)

        except queue.Empty:
            pass
        self.root.after(100, self._poll_log)


# ──────────────────────────────────────────────────────────────────────────────
#  Startup checks
# ──────────────────────────────────────────────────────────────────────────────

def check_scripts() -> bool:
    missing = [s for s in REQUIRED_SCRIPTS if not (SCRIPT_DIR / s).exists()]
    if missing:
        root = tk.Tk(); root.withdraw()
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
    for pkg in ["requests", "playwright", "yt_dlp", "tqdm", "customtkinter"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg.replace("_", "-"))
    if missing and not getattr(sys, "frozen", False):
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(
            "Setup incomplete",
            "Some packages are not installed.\n\n"
            "Please run:\n  pip install " + " ".join(missing),
        )
        root.destroy()
        return False
    return True


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────────────

def main():
    if not check_scripts():
        sys.exit(1)
    if not check_packages():
        sys.exit(1)

    if not _acquire_lock():
        root = tk.Tk(); root.withdraw()
        messagebox.showinfo(
            "Already running",
            "Canvas Archive is already open!\n\nCheck your Dock or taskbar.",
        )
        root.destroy()
        sys.exit(0)

    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["defaults", "write", "-g",
                 "NSRequiresAquaSystemAppearance", "-bool", "yes"],
                capture_output=True,
            )
        except Exception:
            pass

    root = ctk.CTk()
    root.update_idletasks()
    CanvasArchiveApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()            data_dir = os.path.join(os.path.expanduser("~"), ".canvas-archive")
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
    spec   = importlib.util.spec_from_file_location("_canvas_script", script_path)
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
    return Path.home() / ".cache" / "ms-playwright"


def _abs(path: str) -> str:
    return str(Path(path).expanduser().resolve())


SCRIPT_DIR    = _get_script_dir()
DATA_DIR      = _get_data_dir()
CONFIG_FILE   = DATA_DIR / "canvas_config.json"
SENTINEL_FILE = DATA_DIR / "gui_login_ready.txt"
LOCK_FILE     = DATA_DIR / ".canvas_archive.lock"


# ─────────────────────────────────────────────────────────────────────────────
#  Design tokens  (matching archive-your-canvas.lovable.app)
# ─────────────────────────────────────────────────────────────────────────────

CREAM    = "#f5f0e8"   # warm notebook paper background
NAVY     = "#1a1a2e"   # text and card borders
PURPLE   = "#4a0e8f"   # header and accents
GREEN    = "#2d8a3e"   # start button
GREEN_D  = "#236b31"   # start button active
RED      = "#c0392b"   # stop button when active
CARD_BG  = "#faf8f3"   # card fill
LOG_BG   = "#0d0d1a"   # progress log background
LOG_FG   = "#4ade80"   # progress log text
GREY_BTN = "#d4cfc4"   # disabled button background
GREY_TXT = "#888888"   # disabled text


# ─────────────────────────────────────────────────────────────────────────────
#  Single-instance lock
# ─────────────────────────────────────────────────────────────────────────────

def _acquire_lock() -> bool:
    try:
        import fcntl
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fd.write(str(os.getpid()))
        lock_fd.flush()
        _acquire_lock._fd = lock_fd
        return True
    except (IOError, OSError):
        return False
    except ImportError:
        try:
            if LOCK_FILE.exists():
                try:
                    pid = int(LOCK_FILE.read_text().strip())
                    import ctypes
                    handle = ctypes.windll.kernel32.OpenProcess(1, False, pid)
                    if handle:
                        ctypes.windll.kernel32.CloseHandle(handle)
                        return False
                except Exception:
                    pass
            LOCK_FILE.write_text(str(os.getpid()))
            return True
        except Exception:
            return True


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

_AUTH_OK_PHRASES = [
    "[CANVAS_AUTH_OK]",
    "Already logged in — saving session",
    "Logged in — session saved",
    "Using saved session cookies",
    "Using saved cookies",
]


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


def write_canvas_config(canvas_url: str, panopto_url: str,
                        output_dir: str = "") -> None:
    (DATA_DIR / "canvas_config.py").write_text(
        f"CANVAS_BASE_URL  = {canvas_url!r}\n"
        f"PANOPTO_BASE_URL = {panopto_url!r}\n",
        encoding="utf-8",
    )
    cfg = {"canvas_url": canvas_url, "panopto_url": panopto_url}
    if output_dir:
        cfg["output_dir"] = _abs(output_dir)
    (DATA_DIR / "canvas_config.json").write_text(
        json.dumps(cfg, indent=2), encoding="utf-8"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Browser management
# ─────────────────────────────────────────────────────────────────────────────

def _chromium_exe() -> Path | None:
    base = _playwright_browsers_dir()
    if not base.exists():
        return None
    for pat in [
        "chromium*/chrome-mac-arm64/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing",
        "chromium*/chrome-mac-x64/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing",
        "chromium*/chrome-win/chrome.exe",
        "chromium*/chrome-linux/chrome",
    ]:
        matches = list(base.glob(pat))
        if matches:
            return matches[0]
    return None


def _browser_installed() -> bool:
    exe = _chromium_exe()
    return exe is not None and exe.exists()


def install_browser_dialog(parent) -> bool:
    win = tk.Toplevel(parent)
    win.title("First-time setup")
    win.configure(bg=CREAM)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    win.protocol("WM_DELETE_WINDOW", lambda: None)

    pw, ph = 520, 270
    x = (win.winfo_screenwidth()  - pw) // 2
    y = (win.winfo_screenheight() - ph) // 2
    win.geometry(f"{pw}x{ph}+{x}+{y}")

    border = tk.Frame(win, bg=NAVY, padx=3, pady=3)
    border.pack(fill="both", expand=True, padx=20, pady=20)
    inner = tk.Frame(border, bg=CREAM, padx=24, pady=20)
    inner.pack(fill="both", expand=True)

    tk.Label(inner, text="One-time setup",
             font=("Georgia", 16, "bold"), bg=CREAM, fg=NAVY).pack(pady=(0, 6))
    tk.Label(
        inner,
        text="Downloading a browser for Canvas Archive to use.\n"
             "About 150 MB — only happens once.\nPlease leave this window open.",
        font=("Helvetica", 11), bg=CREAM, fg=NAVY, justify="center",
    ).pack(pady=(0, 12))

    bar = ttk.Progressbar(inner, mode="indeterminate", length=420)
    bar.pack(pady=(0, 8))
    bar.start(10)

    sv = tk.StringVar(value="Starting download…")
    tk.Label(inner, textvariable=sv,
             font=("Helvetica", 10), fg=PURPLE, bg=CREAM).pack()

    result = {"ok": False}

    def _run():
        try:
            env = os.environ.copy()
            env["PLAYWRIGHT_BROWSERS_PATH"] = str(_playwright_browsers_dir())
            proc = subprocess.Popen(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, env=env,
            )
            for line in proc.stdout:
                line = line.strip()
                if line:
                    sv.set(line[:65])
            proc.wait()
            if proc.returncode == 0 and _browser_installed():
                result["ok"] = True
                sv.set("Browser ready!")
            else:
                sv.set("Download failed — please try again.")
        except Exception as e:
            sv.set(f"Error: {e}")
        finally:
            bar.stop()
            win.after(2000, win.destroy)

    threading.Thread(target=_run, daemon=True).start()
    win.wait_window()
    return result["ok"]


# ─────────────────────────────────────────────────────────────────────────────
#  TTK Style — force consistent light appearance
# ─────────────────────────────────────────────────────────────────────────────

def _configure_style(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        available = style.theme_names()
        if "clam" in available:
            style.theme_use("clam")
        elif "alt" in available:
            style.theme_use("alt")
    except Exception:
        pass

    style.configure(".",
                    background=CREAM, foreground=NAVY,
                    font=("Helvetica", 11))
    style.configure("TFrame",       background=CREAM)
    style.configure("TLabel",       background=CREAM, foreground=NAVY)
    style.configure("TLabelframe",  background=CREAM, foreground=PURPLE,
                    relief="flat", borderwidth=0)
    style.configure("TLabelframe.Label",
                    background=CREAM, foreground=PURPLE,
                    font=("Georgia", 11, "italic"))
    style.configure("TCheckbutton", background=CREAM, foreground=NAVY)
    style.map("TCheckbutton",
              background=[("active", CREAM)],
              foreground=[("disabled", GREY_TXT)])
    style.configure("TScrollbar",
                    background=CREAM, troughcolor="#e0d8cc",
                    arrowcolor=NAVY, borderwidth=0)
    style.configure("Vertical.TScrollbar",
                    background=CREAM, troughcolor="#e0d8cc", arrowcolor=NAVY)
    style.configure("TCombobox",
                    fieldbackground="white", foreground=NAVY,
                    selectbackground=PURPLE, selectforeground="white",
                    bordercolor=NAVY)
    style.map("TCombobox",
              fieldbackground=[("readonly", "white")])
    style.configure("TEntry",
                    fieldbackground="white", foreground=NAVY,
                    selectbackground=PURPLE, selectforeground="white",
                    bordercolor=NAVY)
    style.configure("TProgressbar",
                    troughcolor="#e0d8cc", background=PURPLE)


# ─────────────────────────────────────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────────────────────────────────────

class CanvasArchiveApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Canvas Archive")
        self.root.resizable(True, True)
        self.root.configure(bg=CREAM)

        _configure_style(root)

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
            value=self._cfg.get("panopto_url", "https://harvard.hosted.panopto.com"))
        self.output_dir   = tk.StringVar(value=self._cfg["output_dir"])
        self.skip_ongoing = tk.BooleanVar(value=self._cfg["skip_ongoing"])
        self.skip_videos  = tk.BooleanVar(value=self._cfg["skip_videos"])
        self.do_canvas    = tk.BooleanVar(value=self._cfg["do_canvas"])
        self.do_external  = tk.BooleanVar(value=self._cfg["do_external"])
        self.do_panopto   = tk.BooleanVar(value=self._cfg["do_panopto"])
        self.do_reserves  = tk.BooleanVar(value=self._cfg["do_reserves"])

        self.running             = False
        self.process:            subprocess.Popen | None = None
        self.log_queue:          queue.Queue = queue.Queue()
        self.script_queue:       list[tuple[str, list[str]]] = []
        self._login_popup:       tk.Toplevel | None = None
        self._dot_job:           str | None = None
        # Progress bar collapsing state
        self._last_was_progress: bool = False

        if SENTINEL_FILE.exists():
            try:
                SENTINEL_FILE.unlink()
            except Exception:
                pass

        self._build_ui()
        self._poll_log()
        self.root.after(800, self._check_browser)

    # ── Browser check ─────────────────────────────────────────────────────────

    def _check_browser(self):
        if not _browser_installed():
            ok = install_browser_dialog(self.root)
            if not ok:
                messagebox.showwarning(
                    "Browser not installed",
                    "Canvas Archive needs a browser to log in to Canvas.\n"
                    "Please restart the app to try again.",
                    parent=self.root,
                )

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Purple header ─────────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=PURPLE)
        header.pack(fill="x", side="top")

        h_inner = tk.Frame(header, bg=PURPLE, pady=14)
        h_inner.pack(fill="x", padx=24)

        tk.Label(
            h_inner, text="Canvas Archive",
            font=("Georgia", 22, "bold"),
            fg="white", bg=PURPLE,
        ).pack(side="left")

        tk.Label(
            h_inner, text="★ free for graduating students ★",
            font=("Helvetica", 10),
            fg="#c4a8ff", bg=PURPLE,
        ).pack(side="right", pady=(6, 0))

        # ── Fixed bottom controls ─────────────────────────────────────────────
        bottom = tk.Frame(self.root, bg=CREAM, pady=14, padx=24)
        bottom.pack(fill="x", side="bottom")

        # Start button — big, bold green
        # NOTE: no cursor="hand2" — keeps normal arrow cursor
        self.start_btn = tk.Button(
            bottom,
            text="▶   Start Download",
            font=("Helvetica", 14, "bold"),
            bg=GREEN, fg="white",
            activebackground=GREEN_D,
            activeforeground="white",
            disabledforeground="#cccccc",
            relief="flat", bd=0,
            padx=20, pady=10,
            command=self._start,
        )
        self.start_btn.pack(fill="x", pady=(0, 8))

        # Stop button — clearly styled; prominent red when active,
        # invisible/flat when disabled
        self.stop_btn = tk.Button(
            bottom,
            text="⏹  Stop",
            font=("Helvetica", 11),
            bg=GREY_BTN, fg=GREY_TXT,
            activebackground=RED,
            activeforeground="white",
            relief="flat", bd=0,
            padx=12, pady=6,
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

        self.main = tk.Frame(self._cv, bg=CREAM, padx=20, pady=16)
        self._cw  = self._cv.create_window((0, 0), window=self.main, anchor="nw")
        self._cv.bind("<Configure>", self._on_canvas_resize)
        self.main.bind("<Configure>", lambda e:
            self._cv.configure(scrollregion=self._cv.bbox("all")))
        self._cv.bind_all("<MouseWheel>", self._on_scroll)
        self._cv.bind_all("<Button-4>",   self._on_scroll)
        self._cv.bind_all("<Button-5>",   self._on_scroll)

        self._build_settings()
        self._build_what()
        self._build_options()
        self._build_log()

    def _on_canvas_resize(self, e):
        self._cv.itemconfig(self._cw, width=e.width)
        # Notebook lines behind content
        self._cv.delete("nblines")
        for y in range(0, e.height, 28):
            self._cv.create_line(0, y, e.width, y,
                                  fill="#d4cfc4", width=1, tags="nblines")
        self._cv.tag_lower("nblines")

    def _on_scroll(self, e):
        if e.num == 4:
            self._cv.yview_scroll(-1, "units")
        elif e.num == 5:
            self._cv.yview_scroll(1, "units")
        else:
            self._cv.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def _card(self, parent, title: str | None = None) -> tk.Frame:
        """Card with thick navy border, matching the Lovable site."""
        outer = tk.Frame(parent, bg=NAVY, padx=2, pady=2)
        outer.pack(fill="x", pady=(0, 14))
        inner = tk.Frame(outer, bg=CARD_BG, padx=18, pady=14)
        inner.pack(fill="both", expand=True)
        if title:
            tk.Label(
                inner, text=title,
                font=("Georgia", 11, "italic"),
                fg=PURPLE, bg=CARD_BG,
            ).pack(anchor="w", pady=(0, 10))
        return inner

    def _build_settings(self):
        card = self._card(self.main, "Settings")

        r1 = tk.Frame(card, bg=CARD_BG)
        r1.pack(fill="x", pady=4)
        tk.Label(r1, text="Canvas URL", width=12,
                 font=("Helvetica", 11, "bold"),
                 fg=NAVY, bg=CARD_BG, anchor="w").pack(side="left")
        ttk.Combobox(r1, textvariable=self.canvas_url,
                     values=COMMON_CANVAS_URLS,
                     width=44, font=("Helvetica", 11)).pack(side="left", padx=(8, 0))

        r2 = tk.Frame(card, bg=CARD_BG)
        r2.pack(fill="x", pady=4)
        tk.Label(r2, text="Save to", width=12,
                 font=("Helvetica", 11, "bold"),
                 fg=NAVY, bg=CARD_BG, anchor="w").pack(side="left")
        ttk.Entry(r2, textvariable=self.output_dir,
                  width=38, font=("Helvetica", 11)).pack(side="left", padx=(8, 6))
        # Browse button — no cursor="hand2"
        tk.Button(r2, text="Browse…",
                  font=("Helvetica", 10),
                  bg=CREAM, fg=NAVY,
                  relief="flat", bd=0,
                  command=self._browse).pack(side="left")

    def _build_what(self):
        card = self._card(self.main, "What would you like to download?")

        grid = tk.Frame(card, bg=CARD_BG)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        for i, (var, label, desc) in enumerate([
            (self.do_canvas,   "📄  Course files",
             "PDFs, slides, videos, documents"),
            (self.do_external, "🔗  External readings",
             "JSTOR, Google Drive, linked content"),
            (self.do_panopto,  "🎬  Lecture recordings",
             "Panopto videos, sorted by course"),
            (self.do_reserves, "📚  Library reserves",
             "Articles & book chapters on reserve"),
        ]):
            row, col = divmod(i, 2)
            self._checkbox_card(grid, var, label, desc, row, col)

    def _checkbox_card(self, parent, var, label, desc, row, col):
        """
        Card-style toggle. Clearly shows ON/OFF:
          ON  — light purple background, ☑ in purple
          OFF — white background, ☐ in grey

        Click anywhere on the card to toggle.
        No cursor change (keeps standard arrow).
        """
        bg_on  = "#ede8ff"
        bg_off = "white"

        outer = tk.Frame(parent, bg=NAVY, padx=2, pady=2)
        outer.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")

        inner = tk.Frame(outer, bg=bg_off, padx=12, pady=10)
        inner.pack(fill="both", expand=True)

        def toggle(*_):
            var.set(not var.get())

        def refresh(*_):
            bg = bg_on if var.get() else bg_off
            inner.configure(bg=bg)
            row_f.configure(bg=bg)
            chk_lbl.configure(
                bg=bg,
                text="☑" if var.get() else "☐",
                fg=PURPLE if var.get() else "#bbbbbb",
            )
            title_lbl.configure(bg=bg)
            desc_lbl.configure(bg=bg)

        row_f = tk.Frame(inner, bg=bg_off)
        row_f.pack(fill="x")

        chk_lbl = tk.Label(
            row_f,
            text="☑" if var.get() else "☐",
            font=("Helvetica", 16),
            fg=PURPLE if var.get() else "#bbbbbb",
            bg=bg_off, width=2,
        )
        chk_lbl.pack(side="left")

        title_lbl = tk.Label(
            row_f, text=label,
            font=("Helvetica", 11, "bold"),
            fg=NAVY, bg=bg_off, anchor="w",
        )
        title_lbl.pack(side="left", padx=(4, 0))

        desc_lbl = tk.Label(
            inner, text=desc,
            font=("Helvetica", 9),
            fg="#777777", bg=bg_off, anchor="w",
        )
        desc_lbl.pack(fill="x", pady=(3, 0))

        # Bind click to every widget in the card
        for w in [inner, row_f, chk_lbl, title_lbl, desc_lbl]:
            w.bind("<Button-1>", lambda e: toggle())

        var.trace_add("write", refresh)
        refresh()

    def _build_options(self):
        card = self._card(self.main)
        opts = tk.Frame(card, bg=CARD_BG)
        opts.pack(fill="x")
        for var, label in [
            (self.skip_ongoing, "Skip administrative / ongoing courses"),
            (self.skip_videos,  "Skip video files  (saves disk space)"),
        ]:
            row = tk.Frame(opts, bg=CARD_BG)
            row.pack(fill="x", pady=3)
            ttk.Checkbutton(row, text=label, variable=var).pack(side="left")

    def _build_log(self):
        outer = tk.Frame(self.main, bg=NAVY, padx=2, pady=2)
        outer.pack(fill="both", expand=True, pady=(0, 6))
        self.log_text = scrolledtext.ScrolledText(
            outer,
            height=13,
            font=("Courier", 10),
            bg=LOG_BG, fg=LOG_FG,
            insertbackground=LOG_FG,
            state="disabled",
            relief="flat",
            padx=12, pady=10,
        )
        self.log_text.pack(fill="both", expand=True)

        for tag, colour in [
            ("success",  "#4ade80"),
            ("error",    "#ff6b6b"),
            ("warn",     "#ffd93d"),
            ("info",     "#74b9ff"),
            ("header",   "#c4a8ff"),
            ("dim",      "#444466"),
            ("login",    "#ffd93d"),
            ("progress", "#74b9ff"),   # in-progress download line
        ]:
            self.log_text.tag_config(tag, foreground=colour)

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

        border = tk.Frame(popup, bg=NAVY, padx=3, pady=3)
        border.pack(fill="both", expand=True, padx=20, pady=20)
        inner = tk.Frame(border, bg=CREAM, padx=24, pady=20)
        inner.pack(fill="both", expand=True)

        tk.Label(inner, text="Log in to Canvas",
                 font=("Georgia", 16, "bold"),
                 bg=CREAM, fg=NAVY).pack(pady=(0, 8))
        tk.Label(
            inner,
            text=(
                "A browser window has opened.\n"
                "Log in with your university credentials as normal.\n\n"
                "Once you can see your Canvas dashboard,\n"
                "click the button below."
            ),
            font=("Helvetica", 11),
            bg=CREAM, fg=NAVY, justify="center",
        ).pack(pady=(0, 16))

        # Login confirm button — no cursor="hand2"
        tk.Button(
            inner,
            text="  I'm logged in — continue  ",
            font=("Helvetica", 13, "bold"),
            bg=GREEN, fg="white",
            activebackground=GREEN_D, activeforeground="white",
            relief="flat", bd=0,
            padx=20, pady=10,
            command=self._confirm_login,
        ).pack()

        popup.focus_force()
        self._set_status("Waiting for login — click the button above")

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
        self._set_status("Continuing download…")

    # ── Status / dots ─────────────────────────────────────────────────────────

    def _set_status(self, text: str, fg: str = PURPLE):
        self.status_var.set(text)
        self.status_lbl.configure(fg=fg)

    def _start_dots(self, base: str):
        self._dot_base  = base
        self._dot_count = 0
        self._animate_dots()

    def _animate_dots(self):
        if not self.running or self._login_popup is not None:
            return
        self._dot_count = (self._dot_count + 1) % 4
        self._set_status(f"{self._dot_base}{'.' * self._dot_count}")
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

        if not _browser_installed():
            ok = install_browser_dialog(self.root)
            if not ok:
                messagebox.showerror(
                    "Browser required",
                    "Canvas Archive needs a browser to work.\nPlease try again.",
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

        out = _abs(self.output_dir.get().strip())

        cfg = {
            "canvas_url":   canvas_url,
            "panopto_url":  self.panopto_url.get().strip().rstrip("/"),
            "output_dir":   out,
            "skip_ongoing": self.skip_ongoing.get(),
            "skip_videos":  self.skip_videos.get(),
            "do_canvas":    self.do_canvas.get(),
            "do_external":  self.do_external.get(),
            "do_panopto":   self.do_panopto.get(),
            "do_reserves":  self.do_reserves.get(),
        }
        save_config(cfg)
        write_canvas_config(cfg["canvas_url"], cfg["panopto_url"], out)

        ongoing = ["--skip-ongoing"] if cfg["skip_ongoing"] else []
        novid   = ["--skip-videos"]  if cfg["skip_videos"]  else []

        self.script_queue = []
        if cfg["do_canvas"]:
            self.script_queue.append(("canvas_downloader.py",
                                       ["--dir", out] + ongoing + novid))
        if cfg["do_external"]:
            self.script_queue.append(("external_downloader.py",
                                       ["--dir", out]))
        if cfg["do_panopto"]:
            self.script_queue.append(("panopto_downloader.py",
                                       ["--dir", out] + ongoing))
        if cfg["do_reserves"]:
            self.script_queue.append(("reserves_downloader.py",
                                       ["--dir", out] + ongoing))

        if not self.script_queue:
            messagebox.showwarning("Nothing selected",
                                   "Please select at least one type to download.")
            return

        Path(out).mkdir(parents=True, exist_ok=True)

        if SENTINEL_FILE.exists():
            try:
                SENTINEL_FILE.unlink()
            except Exception:
                pass

        self.running            = True
        self._last_was_progress = False
        self._close_login_popup()
        self._clear_log()

        # Button states
        self.start_btn.configure(state="disabled", bg="#aaaaaa")
        self.stop_btn.configure(state="normal", bg=RED, fg="white")

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
        self.start_btn.configure(state="normal", bg=GREEN, fg="white",
                                  text="▶   Start Download")
        self.stop_btn.configure(state="disabled", bg=GREY_BTN, fg=GREY_TXT)
        self._set_status("Stopped — click Start Download to begin again.", RED)
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
        self._last_was_progress = False

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
        self.start_btn.configure(state="normal", bg=GREEN, fg="white",
                                  text="▶   Start Download")
        self.stop_btn.configure(state="disabled", bg=GREY_BTN, fg=GREY_TXT)

        out = self.output_dir.get()
        self._set_status("All done!  Click Start to run again.", GREEN)

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
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self._last_was_progress = False

    def _log(self, text: str, tag: str | None = None):
        """Append a line to the log. Resets progress-tracking state."""
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text, (tag,) if tag else ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._last_was_progress = False

    def _log_progress(self, fname: str, pct: str):
        """
        KEY FIX: show/update a single progress line instead of
        appending a new line for every tqdm update.

        Uses the '_progress' tag to track the region and replace
        it in-place on subsequent calls for the same file.
        """
        progress_text = f"  ↓ {fname[:56]}… {pct}%\n"
        self.log_text.configure(state="normal")

        if self._last_was_progress:
            # Replace the existing progress line
            ranges = self.log_text.tag_ranges("_progress")
            if ranges:
                start = str(ranges[0])
                end   = str(ranges[-1])
                self.log_text.delete(start, end)
                self.log_text.insert(start, progress_text,
                                      ("_progress", "progress"))
            else:
                self.log_text.insert("end", progress_text,
                                      ("_progress", "progress"))
        else:
            # First progress line in this sequence — clear old tag
            self.log_text.tag_remove("_progress", "1.0", "end")
            self.log_text.insert("end", progress_text,
                                  ("_progress", "progress"))

        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self._last_was_progress = True

    def _poll_log(self):
        try:
            while True:
                kind, data = self.log_queue.get_nowait()

                if kind == "line":
                    line = data

                    # ── Tqdm progress bar filtering ───────────────────────────
                    # Lines containing '%|' are tqdm progress updates.
                    # Instead of appending 40+ lines per file, we keep ONE
                    # line and update it in place.
                    if "%|" in line:
                        m = re.search(r"↓\s+(.+?):\s*(\d+)%", line)
                        if m:
                            fname = m.group(1).strip()
                            pct   = m.group(2)
                            self._log_progress(fname, pct)
                            self._set_status(f"↓ {fname[:45]}… {pct}%")
                        continue   # Don't also append to log

                    # Skip tqdm blank clear lines that follow progress bars
                    if self._last_was_progress and not line.strip():
                        self._last_was_progress = False
                        continue

                    # ── Tag selection ─────────────────────────────────────────
                    if any(c in line for c in
                           ("✓", "Downloaded", "complete", "exists")):
                        tag = "success"
                    elif any(c in line for c in
                             ("✗", "FAILED", "Error", "Traceback")):
                        tag = "error"
                    elif any(c in line for c in
                             ("WARNING", "timed out", "Waiting")):
                        tag = "warn"
                    elif any(c in line for c in
                             ("━", "Starting", "Saving", "Scanning",
                              "Found", "Total", "Fetching", "Processing")):
                        tag = "header"
                    elif any(p in line for p in _LOGIN_PHRASES):
                        tag = "login"
                    else:
                        tag = None

                    self._log(line, tag)

                    # Auth succeeded — dismiss any open login popup
                    if any(p in line for p in _AUTH_OK_PHRASES):
                        self._close_login_popup()
                    # Login needed — show popup
                    elif any(p in line for p in _LOGIN_PHRASES):
                        if self._login_popup is None:
                            self._stop_dots()
                            self.root.after(300, self._show_login_popup)

                elif kind == "done":
                    rc = data
                    self._stop_dots()
                    self._close_login_popup()
                    self._last_was_progress = False
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
    missing = [s for s in REQUIRED_SCRIPTS if not (SCRIPT_DIR / s).exists()]
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

    # Single instance check
    if not _acquire_lock():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(
            "Already running",
            "Canvas Archive is already open!\n\nCheck your Dock or taskbar.",
        )
        root.destroy()
        sys.exit(0)

    # Encourage macOS to use light appearance
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["defaults", "write", "-g",
                 "NSRequiresAquaSystemAppearance", "-bool", "yes"],
                capture_output=True,
            )
        except Exception:
            pass

    root = tk.Tk()
    root.update_idletasks()
    CanvasArchiveApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
