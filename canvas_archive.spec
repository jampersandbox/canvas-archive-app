import sys
import os
from pathlib import Path
import playwright

block_cipher = None

# Find where Playwright stores Chromium on the build machine
playwright_pkg = Path(playwright.__file__).parent
driver_dir     = playwright_pkg / "driver"

# Find the local-browsers directory
local_browsers = driver_dir / "package" / ".local-browsers"

# Build datas list
datas = [
    ("canvas_auth.py",           "."),
    ("canvas_downloader.py",     "."),
    ("external_downloader.py",   "."),
    ("panopto_downloader.py",    "."),
    ("reserves_downloader.py",   "."),
    ("patch_scripts.py",         "."),
    # Bundle the entire Playwright driver (includes Chromium)
    (str(driver_dir), "playwright/driver"),
]

# Also bundle local browsers if they exist
if local_browsers.exists():
    datas.append((str(local_browsers),
                  "playwright/driver/package/.local-browsers"))

a = Analysis(
    ["canvas_archive.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "tkinter",
        "tkinter.ttk",
        "tkinter.scrolledtext",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "requests",
        "requests.adapters",
        "urllib3",
        "certifi",
        "tqdm",
        "yt_dlp",
        "playwright",
        "playwright.sync_api",
        "playwright._impl._driver",
        "playwright._impl._connection",
        "greenlet",
        "importlib.util",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "numpy", "pandas", "scipy"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Canvas Archive",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.icns" if sys.platform == "darwin" else "icon.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Canvas Archive",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Canvas Archive.app",
        icon="icon.icns",
        bundle_identifier="com.canvasarchive.app",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleVersion":            "1.0.0",
            "CFBundleShortVersionString": "1.0.0",
            "NSHumanReadableCopyright":   "Free to use",
            "LSApplicationCategoryType":  "public.app-category.education",
        },
    )