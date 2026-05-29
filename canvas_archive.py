#!/usr/bin/env python3
"""
canvas_archive.py
=================
Canvas Archive — Save your course materials before you lose access.

When bundled with PyInstaller this file also acts as the subprocess
runner for the downloader scripts via the --run-script flag.
"""

# ── --run-script dispatch (must be first — before any GUI imports) ─────────────
# When PyInstaller bundles the app, sys.executable points to the bundle itself.
# We use: canvas_archive --run-script canvas_downloader.py [args...]
# to spawn ourselves as a "Python interpreter" for subprocesses.
import sys
import os

if len(sys.argv) >= 3 and sys.argv[1] == "--run-script":
    script_name = sys.argv[2]
    script_args = sys.argv[3:]

    # ── Paths ──────────────────────────────────────────────────────────────────
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
            data_dir = os.path.join(os.path.expanduser("~"), ".canvas-archive")
    else:
        data_dir = script_dir

    os.makedirs(data_dir, exist_ok=True)

    # Add script dir so scripts can import each other
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    # Change to data dir so relative paths (./browser_profile etc.) resolve
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

# ── Normal imports (GUI mode) ──────────────────────────────────────────────────
import json
import logging
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk


# ─────────────────────────────────────────────────────────────────────────────
#  Path helpers
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


SCRIPT_DIR    = _get_script_dir()
DATA_DIR      = _get_data_dir()
HERE          = SCRIPT_DIR          # kept for compatibility
CONFIG_FILE   = DATA_DIR / "canvas_config.json"
SENTINEL_FILE = DATA_DIR / "gui_login_ready.txt"

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


# ─────────────────────────────────────────────────────────────────────────────
#  Config
# ─────────────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
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


def save_config(cfg: dict) -> None:
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def write_canvas_config(canvas_url: str, panopto_url: str) -> None:
    (DATA_DIR / "canvas_config.py").write_text(
        f"CANVAS_BASE_URL  = {canvas_url!r}\n"
        f"PANOPTO_BASE_URL = {panopto_url!r}\n",
        encoding="utf-8",
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Browser setup (first-run)
# ─────────────────────────────────────────────────────────────────────────────

def _browser_installed() -> bool:
    try:
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Caches" / "ms-playwright"
        elif sys.platform.startswith("win"):
            base = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
        else:
            base = Path.home() / ".cache" / "ms-playwright"
        return any(base.glob("chromium*"))
    except Exception:
        return False


def install_browser_dialog(parent) -> bool:
    win = tk.Toplevel(parent)
    win.title("First-time setup")
    win.geometry("480x210")
    win.resizable(False, False)
    win.configure(bg="#f0f0f0")
    win.attributes("-topmost", True)
    win.protocol("WM_DELETE_WINDOW", lambda: None)

    x = (win.winfo_screenwidth()  - 480) // 2
    y = (win.winfo_screenheight() - 210) // 2
    win.geometry(f"480x210+{x}+{y}")

    tk.Label(
        win, text="🌐  First-time setup",
        font=("Helvetica", 14, "bold"), bg="#f0f0f0",
    ).pack(pady=(20, 4))

    tk.Label(
        win,
        text=(
            "Downloading a browser for Canvas Archive to use.\n"
            "This is about 150 MB and only happens once.\n"
            "Please leave this window open."
        ),
        font=("Helvetica", 11), bg="#f0f0f0", justify="center",
    ).pack(pady=(0, 10))

    bar = ttk.Progressbar(win, mode="indeterminate", length=400)
    bar.pack(pady=(0, 8))
    bar.start(10)

    sv = tk.StringVar(value="Downloading Chromium…")
    tk.Label(win, textvariable=sv,
             font=("Helvetica", 10), fg="#555", bg="#f0f0f0").pack()

    result = {"ok": False}

    def _install():
        try:
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                check=True, capture_output=True,
            )
            result["ok"] = True
            sv.set("✅  Browser ready!")
        except Exception as e:
            sv.set(f"✗  {e}")
        finally:
            bar.stop()
            win.after(1800, win.destroy)

    threading.Thread(target=_install, daemon=True).start()
    win.wait_window()
    return result["ok"]


# ─────────────────────────────────────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────────────────────────────────────

class CanvasArchiveApp:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Canvas Archive  🎓")
        self.root.resizable(True, True)
        self.root.configure(bg="#f0f0f0")

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        w  = min(920, int(sw * 0.92))
        h  = min(800, int(sh * 0.88))
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

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#4a148c", pady=12)
        header.pack(fill="x", side="top")
        tk.Label(
            header, text="🎓  Canvas Archive",
            font=("Helvetica", 20, "bold"), fg="white", bg="#4a148c",
        ).pack()
        tk.Label(
            header,
            text="Save all your course materials before you lose access",
            font=("Helvetica", 10), fg="#e1bee7", bg="#4a148c",
        ).pack(pady=(1, 0))

        # Bottom controls (fixed)
        ctrl = tk.Frame(self.root, bg="#e8e8e8", pady=10, padx=20)
        ctrl.pack(fill="x", side="bottom")
        self.start_btn = tk.Button(
            ctrl, text="▶   Start Download",
            font=("Helvetica", 13, "bold"),
            bg="#4a148c", fg="white",
            activebackground="#6a1fbc", activeforeground="white",
            relief="raised", bd=3, cursor="hand2", padx=14, pady=6,
            command=self._start,
        )
        self.start_btn.pack(side="left", padx=(0, 10))
        self.stop_btn = tk.Button(
            ctrl, text="⏹  Stop",
            font=("Helvetica", 12), bg="#cccccc", fg="#444444",
            relief="raised", bd=2, cursor="hand2", padx=10, pady=6,
            state="disabled", command=self._stop,
        )
        self.stop_btn.pack(side="left")

        # Status bar (fixed)
        self.status_frame = tk.Frame(self.root, bg="#d0d0d0", pady=5, padx=20)
        self.status_frame.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(
            value="Ready — click 'Start Download' to begin."
        )
        self.status_label = tk.Label(
            self.status_frame, textvariable=self.status_var,
            fg="#222", bg="#d0d0d0",
            font=("Helvetica", 10, "bold"), anchor="w",
        )
        self.status_label.pack(fill="x")

        # Scrollable middle
        sc = tk.Frame(self.root, bg="#f0f0f0")
        sc.pack(fill="both", expand=True, side="top")

        self._canvas_w = tk.Canvas(sc, bg="#f0f0f0", highlightthickness=0)
        sb = ttk.Scrollbar(sc, orient="vertical",
                           command=self._canvas_w.yview)
        self._canvas_w.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._canvas_w.pack(side="left", fill="both", expand=True)

        self.main = tk.Frame(self._canvas_w, bg="#f0f0f0", padx=20, pady=10)
        self._cw = self._canvas_w.create_window(
            (0, 0), window=self.main, anchor="nw"
        )
        self._canvas_w.bind("<Configure>", self._on_resize)
        self.main.bind("<Configure>", self._on_frame_conf)
        self._canvas_w.bind_all("<MouseWheel>", self._on_scroll)
        self._canvas_w.bind_all("<Button-4>",   self._on_scroll)
        self._canvas_w.bind_all("<Button-5>",   self._on_scroll)

        self._build_settings()
        self._build_what()
        self._build_options()
        self._build_log()

    def _on_resize(self, e):
        self._canvas_w.itemconfig(self._cw, width=e.width)

    def _on_frame_conf(self, e):
        self._canvas_w.configure(scrollregion=self._canvas_w.bbox("all"))

    def _on_scroll(self, e):
        if e.num == 4:
            self._canvas_w.yview_scroll(-1, "units")
        elif e.num == 5:
            self._canvas_w.yview_scroll(1, "units")
        else:
            self._canvas_w.yview_scroll(int(-1*(e.delta/120)), "units")

    def _build_settings(self):
        sf = ttk.LabelFrame(self.main, text=" ⚙️  Settings ", padding=10)
        sf.pack(fill="x", pady=(0, 8))

        r1 = tk.Frame(sf, bg="white")
        r1.pack(fill="x", pady=4)
        tk.Label(r1, text="Canvas URL:", width=13,
                 anchor="w", bg="white", font=("Helvetica", 11)).pack(side="left")
        ttk.Combobox(r1, textvariable=self.canvas_url,
                     values=COMMON_CANVAS_URLS,
                     width=46, font=("Helvetica", 11)).pack(side="left", padx=4)

        r2 = tk.Frame(sf, bg="white")
        r2.pack(fill="x", pady=4)
        tk.Label(r2, text="Save files to:", width=13,
                 anchor="w", bg="white", font=("Helvetica", 11)).pack(side="left")
        ttk.Entry(r2, textvariable=self.output_dir,
                  width=40, font=("Helvetica", 11)).pack(side="left", padx=4)
        ttk.Button(r2, text="Browse…",
                   command=self._browse).pack(side="left", padx=4)

    def _build_what(self):
        wf = ttk.LabelFrame(self.main, text=" 📥  What to download ", padding=10)
        wf.pack(fill="x", pady=(0, 8))
        for var, label, desc in [
            (self.do_canvas,   "📄  Course files",
             "All PDFs, slides, videos, and documents uploaded to Canvas"),
            (self.do_external, "🔗  External readings",
             "JSTOR articles, Google Drive files, and other linked content"),
            (self.do_panopto,  "🎬  Lecture recordings",
             "Panopto videos recorded by your professors"),
            (self.do_reserves, "📚  Library reserve readings",
             "Articles and book chapters on course reserve"),
        ]:
            row = tk.Frame(wf, bg="white")
            row.pack(fill="x", pady=3)
            ttk.Checkbutton(row, text=label, variable=var).pack(side="left")
            tk.Label(row, text=f"  —  {desc}",
                     fg="#666", bg="white",
                     font=("Helvetica", 9)).pack(side="left")

    def _build_options(self):
        of = ttk.LabelFrame(self.main, text=" 🔧  Options ", padding=10)
        of.pack(fill="x", pady=(0, 8))
        opts = tk.Frame(of, bg="white")
        opts.pack(fill="x")
        ttk.Checkbutton(opts,
                        text="Skip administrative / ongoing courses  ",
                        variable=self.skip_ongoing).pack(side="left")
        ttk.Checkbutton(opts,
                        text="Skip video files  (saves disk space)",
                        variable=self.skip_videos).pack(side="left")

    def _build_log(self):
        lf = ttk.LabelFrame(self.main, text=" 📋  Progress log ", padding=6)
        lf.pack(fill="both", expand=True, pady=(0, 4))
        self.log_text = scrolledtext.ScrolledText(
            lf, height=14, font=("Courier", 10),
            bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", state="disabled",
        )
        self.log_text.pack(fill="both", expand=True)
        for tag, colour in [
            ("success", "#4ec9b0"), ("error", "#f44747"),
            ("warn", "#dcdcaa"), ("info", "#9cdcfe"),
            ("header", "#c586c0"), ("dim", "#888888"),
            ("login", "#ffcc02"),
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
        popup.title("🔐  Login Required")
        popup.configure(bg="#fff3cd")
        popup.resizable(False, False)
        pw, ph = 520, 270
        x = (popup.winfo_screenwidth()  - pw) // 2
        y = (popup.winfo_screenheight() - ph) // 2
        popup.geometry(f"{pw}x{ph}+{x}+{y}")
        popup.attributes("-topmost", True)
        popup.lift()
        popup.protocol("WM_DELETE_WINDOW", lambda: None)
        self._login_popup = popup

        tk.Label(popup, text="🔐  Login Required",
                 font=("Helvetica", 16, "bold"),
                 bg="#fff3cd", fg="#856404").pack(pady=(24, 8))
        tk.Label(
            popup,
            text=(
                "A browser window has opened.\n"
                "Please log in with your university credentials.\n\n"
                "Once you can see your Canvas dashboard,\n"
                "click the button below."
            ),
            font=("Helvetica", 12), bg="#fff3cd", fg="#533f03",
            justify="center",
        ).pack(pady=(0, 16))
        tk.Button(
            popup,
            text="  ✅  I'm logged in — continue downloading  ",
            font=("Helvetica", 14, "bold"),
            bg="#28a745", fg="white",
            activebackground="#218838", activeforeground="white",
            relief="raised", bd=3, cursor="hand2", padx=10, pady=8,
            command=self._confirm_login,
        ).pack()
        popup.focus_force()
        self._set_status(
            "⏸  Waiting for login — click the green button in the popup",
            "#856404", "#fff3cd",
        )

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
            self._log(f"  ⚠  Could not write sentinel: {exc}\n", "warn")
        self._close_login_popup()
        self._log("  ✅  Login confirmed — continuing…\n\n", "success")
        self._set_status("Continuing download…", "#155724", "#d4edda")

    # ── Status / dots ─────────────────────────────────────────────────────────

    def _set_status(self, text, fg="#222", bg="#d0d0d0"):
        self.status_var.set(text)
        self.status_label.config(fg=fg)
        self.status_frame.config(bg=bg)
        self.status_label.config(bg=bg)

    def _start_dots(self, base):
        self._dot_base  = base
        self._dot_count = 0
        self._animate_dots()

    def _animate_dots(self):
        if not self.running or self._login_popup is not None:
            return
        self._dot_count = (self._dot_count + 1) % 4
        self._set_status(
            f"⏳  {self._dot_base}{'.' * self._dot_count}",
            "#0c5460", "#d1ecf1",
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

        canvas_url = self.canvas_url.get().strip().rstrip("/")
        if not canvas_url.startswith("http"):
            messagebox.showerror("Invalid URL",
                                 "Please enter a valid Canvas URL.")
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
                "external_downloader.py",
                ["--dir", out],
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
            messagebox.showwarning("Nothing selected",
                                   "Please select at least one type to download.")
            return

        Path(out).mkdir(parents=True, exist_ok=True)

        if SENTINEL_FILE.exists():
            SENTINEL_FILE.unlink()

        self.running = True
        self._close_login_popup()
        self._clear_log()
        self.start_btn.config(state="disabled", bg="#888888")
        self.stop_btn.config(state="normal", bg="#c0392b", fg="white")

        self._log("═" * 56 + "\n", "header")
        self._log("  🎓  Canvas Archive — Starting\n", "header")
        self._log(f"  📁  Saving to: {out}\n", "info")
        self._log("═" * 56 + "\n\n", "header")

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
        self.start_btn.config(state="normal", bg="#4a148c", fg="white",
                              text="▶   Start Download")
        self.stop_btn.config(state="disabled", bg="#cccccc", fg="#444444")
        self._set_status("⏹  Stopped — click Start Download to begin again.",
                         "#721c24", "#f8d7da")
        self._log("\n  ⏹  Download stopped.\n", "warn")

    def _run_next_script(self):
        if not self.running:
            return
        if not self.script_queue:
            self._all_done()
            return

        script_name, args = self.script_queue.pop(0)

        # Check the script exists in the bundle
        if not (SCRIPT_DIR / script_name).exists():
            self._log(f"  ⚠  {script_name} not found — skipping.\n", "warn")
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
        self._log(f"\n{'─' * 56}\n", "dim")
        self._log(f"  ▶  {friendly}…\n", "info")
        self._log(f"{'─' * 56}\n", "dim")

        # KEY: use sys.executable --run-script so PyInstaller bundles work
        env = os.environ.copy()
        env["CANVAS_ARCHIVE_GUI"]          = "1"
        env["PYTHONUNBUFFERED"]            = "1"
        env["PYTHONDONTWRITEBYTECODE"]     = "1"
        env["CANVAS_ARCHIVE_DATA_DIR"]     = str(DATA_DIR)

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
            self._log(f"  ✗  Could not start {script_name}: {exc}\n", "error")
            self.root.after(500, self._run_next_script)
            return

        self._log(f"  ⚙  Started (PID {self.process.pid})\n", "dim")

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
        self.start_btn.config(state="normal", bg="#4a148c", fg="white",
                              text="▶   Start Download")
        self.stop_btn.config(state="disabled", bg="#cccccc", fg="#444444")
        out = self.output_dir.get()
        self._set_status("✅  All done!  Click Start to run again.",
                         "#155724", "#d4edda")
        self._log("\n" + "═" * 56 + "\n", "success")
        self._log("  ✅  All downloads complete!\n", "success")
        self._log(f"  📁  Files saved to:\n      {out}\n", "success")
        self._log("═" * 56 + "\n", "success")
        messagebox.showinfo(
            "Download Complete! 🎓",
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
                    if any(c in line for c in
                           ("✓", "FINISHED", "Downloaded", "✅")):
                        tag = "success"
                    elif any(c in line for c in
                             ("✗", "FAILED", "Error", "Traceback")):
                        tag = "error"
                    elif any(c in line for c in
                             ("⚠", "WARNING", "timed out", "Waiting")):
                        tag = "warn"
                    elif any(c in line for c in
                             ("═", "─", "📚", "📹", "📖", "🎓", "🌐")):
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
                        "  ✓  Step complete.\n" if rc == 0
                        else f"  ⚠  Finished with exit code {rc}.\n",
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
            "Please run:\n  pip install "
            + " ".join(missing),
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

    root = tk.Tk()
    root.update_idletasks()
    app = CanvasArchiveApp(root)

    # First-run browser check
    if not _browser_installed():
        root.after(500, lambda: install_browser_dialog(root))

    root.mainloop()


if __name__ == "__main__":
    main()