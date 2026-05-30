#!/usr/bin/env python3
"""
canvas_auth.py
==============
Handles Canvas login via browser session.
Works in both terminal mode and GUI mode.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

BROWSER_PROFILE   = Path("./browser_profile")
COOKIE_FILE       = Path("./canvas_cookies.json")
GUI_SENTINEL_FILE = Path("./gui_login_ready.txt")
_CONFIG_FILE      = Path("./canvas_config.json")
_CONFIG_PY_FILE   = Path("./canvas_config.py")


def _get_canvas_base_url() -> str:
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
            url  = data.get("canvas_url", "").strip()
            if url.startswith("http"):
                return url
        except Exception:
            pass
    if _CONFIG_PY_FILE.exists():
        try:
            content = _CONFIG_PY_FILE.read_text(encoding="utf-8")
            m = re.search(
                r'CANVAS_BASE_URL\s*=\s*["\']([^"\']+)["\']', content
            )
            if m:
                url = m.group(1).strip()
                if url.startswith("http"):
                    return url
        except Exception:
            pass
    return "https://canvas.harvard.edu"


def _find_chromium() -> str | None:
    """
    Search all the places Chromium might live inside the app bundle
    or on the system. Returns the path to the executable or None.
    """
    candidates: list[Path] = []

    if getattr(sys, "frozen", False):
        # PyInstaller bundle — check every plausible location
        bundle = Path(sys.executable).parent

        # On Mac, sys.executable is inside .app/Contents/MacOS/
        # _MEIPASS is inside .app/Contents/Frameworks/
        meipass = Path(getattr(sys, "_MEIPASS", str(bundle)))

        for base in [bundle, meipass,
                     bundle.parent,           # Contents/
                     bundle.parent / "Resources",
                     bundle.parent / "Frameworks"]:
            for suffix in [
                "playwright/driver/package/.local-browsers",
                "_playwright/driver/package/.local-browsers",
            ]:
                candidates.append(base / suffix)

    # Also check the standard Playwright cache locations
    if sys.platform == "darwin":
        candidates.append(
            Path.home() / "Library" / "Caches" / "ms-playwright"
        )
    elif sys.platform.startswith("win"):
        candidates.append(
            Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
        )
    else:
        candidates.append(Path.home() / ".cache" / "ms-playwright")

    for base in candidates:
        if not base.exists():
            continue
        # Look for any chromium executable under this directory
        for pattern in [
            "chromium*/chrome-mac-arm64/Google Chrome for Testing.app"
            "/Contents/MacOS/Google Chrome for Testing",
            "chromium*/chrome-mac-x64/Google Chrome for Testing.app"
            "/Contents/MacOS/Google Chrome for Testing",
            "chromium*/chrome-win/chrome.exe",
            "chromium*/chrome-linux/chrome",
        ]:
            matches = list(base.glob(pattern))
            if matches:
                log.info(f"  Found Chromium at: {matches[0]}")
                return str(matches[0])

    return None


def _ensure_browser() -> None:
    """
    Make sure Chromium is available. If running as a bundled app,
    set PLAYWRIGHT_BROWSERS_PATH to point at the bundled browser.
    If not found anywhere, download it.
    """
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        bundle  = Path(sys.executable).parent

        # Try all possible bundle locations
        for base in [
            meipass / "playwright" / "driver" / "package" / ".local-browsers",
            bundle.parent / "Resources" / "playwright" / "driver" / "package" / ".local-browsers",
            bundle.parent / "Frameworks" / "playwright" / "driver" / "package" / ".local-browsers",
        ]:
            if base.exists():
                log.info(f"  Setting PLAYWRIGHT_BROWSERS_PATH to: {base}")
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(base)
                return

    # Check if Chromium is findable
    exe = _find_chromium()
    if exe:
        # Point Playwright at the directory containing the chromium-* folder
        browsers_path = str(Path(exe).parents[
            4 if sys.platform == "darwin" else 2
        ])
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
        log.info(f"  Found Chromium — using: {browsers_path}")
        return

    # Nothing found — download it
    log.info("  Chromium not found — downloading (one time only)…")
    print("  Downloading browser — this takes about 2 minutes…")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=False,
    )
    if result.returncode != 0:
        # Try with pip first in case playwright CLI isn't available
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "playwright"],
            capture_output=True,
        )
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
        )


def get_cookies() -> list[dict]:
    canvas_base_url = _get_canvas_base_url()

    # Fast path: saved cookies
    if COOKIE_FILE.exists():
        try:
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            if cookies:
                log.info("  Using saved session cookies.")
                return cookies
        except Exception:
            pass

    # Make sure browser is available before trying to launch it
    _ensure_browser()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed.\n"
            "Run:  pip install playwright && playwright install chromium"
        )

    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

    if GUI_SENTINEL_FILE.exists():
        try:
            GUI_SENTINEL_FILE.unlink()
        except Exception:
            pass

    in_gui = bool(os.environ.get("CANVAS_ARCHIVE_GUI"))

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()

        log.info("  Checking Canvas session …")

        try:
            page.goto(
                f"{canvas_base_url}/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        already_in = (
            "login" not in page.url.lower()
            and "saml"  not in page.url.lower()
            and canvas_base_url.replace("https://", "") in page.url
        )

        if not already_in:
            print()
            print("=" * 62)
            print("  Canvas Login Required")
            print()
            print("  A browser window has just opened.")
            print("  Log in with your university credentials as normal.")
            print("  Once you can see the Canvas dashboard,")
            if in_gui:
                print("  click the green button in the app to continue.")
            else:
                print("  come back here and press ENTER.")
            print("=" * 62)

            if in_gui:
                print("  [Waiting for GUI login confirmation...]")
                for _ in range(1200):
                    if GUI_SENTINEL_FILE.exists():
                        try:
                            GUI_SENTINEL_FILE.unlink()
                        except Exception:
                            pass
                        break
                    time.sleep(0.5)
            else:
                try:
                    input("\n  [Press ENTER after you are logged in] ")
                except EOFError:
                    time.sleep(5)

            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass

            print("\n  Logged in — session saved for future runs.\n")
        else:
            log.info("  Already logged in (using saved session).")

        cookies = ctx.cookies()
        ctx.close()

    COOKIE_FILE.write_text(
        json.dumps(cookies, indent=2), encoding="utf-8"
    )
    return cookies


def cookies_for_domain(cookies: list[dict], base_url: str) -> str:
    domain = (
        base_url.replace("https://", "")
                .replace("http://", "")
                .split("/")[0]
    )
    relevant = [c for c in cookies if domain in c.get("domain", "")]
    return "; ".join(f"{c['name']}={c['value']}" for c in relevant)
    if _CONFIG_PY_FILE.exists():
        try:
            content = _CONFIG_PY_FILE.read_text(encoding="utf-8")
            m = re.search(
                r'CANVAS_BASE_URL\s*=\s*["\']([^"\']+)["\']', content
            )
            if m:
                url = m.group(1).strip()
                if url.startswith("http"):
                    return url
        except Exception:
            pass

    return "https://canvas.harvard.edu"


def _ensure_browser() -> None:
    """
    When running as a frozen PyInstaller app, point Playwright at the
    bundled Chromium. If not bundled, download it on first run.
    """
    if getattr(sys, "frozen", False):
        bundle_dir = Path(sys._MEIPASS)
        playwright_browsers = (
            bundle_dir
            / "playwright"
            / "driver"
            / "package"
            / ".local-browsers"
        )
        if playwright_browsers.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_browsers)
            log.info(f"  Using bundled browser.")
            return

    # Not bundled or browser missing — check if already installed
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            # Try to get browser executable path
            path = pw.chromium.executable_path
            if path and Path(path).exists():
                return   # already installed, nothing to do
    except Exception:
        pass

    # Need to download
    log.info("  Downloading browser (first run — one time only)…")
    print("  Downloading browser (first run — one time only)…")
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False,
    )


def get_cookies() -> list[dict]:
    canvas_base_url = _get_canvas_base_url()

    # Fast path: saved cookies
    if COOKIE_FILE.exists():
        try:
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            if cookies:
                log.info("  Using saved session cookies.")
                return cookies
        except Exception:
            pass

    # Make sure browser is available
    _ensure_browser()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed.\n"
            "Run:  pip install playwright && playwright install chromium"
        )

    BROWSER_PROFILE.mkdir(parents=True, exist_ok=True)

    if GUI_SENTINEL_FILE.exists():
        try:
            GUI_SENTINEL_FILE.unlink()
        except Exception:
            pass

    in_gui = bool(os.environ.get("CANVAS_ARCHIVE_GUI"))

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE),
            headless=False,
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.new_page()

        log.info("  Checking Canvas session …")

        try:
            page.goto(
                f"{canvas_base_url}/",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass

        already_in = (
            "login" not in page.url.lower()
            and "saml"  not in page.url.lower()
            and canvas_base_url.replace("https://", "") in page.url
        )

        if not already_in:
            print()
            print("=" * 62)
            print("  Canvas Login Required")
            print()
            print("  A browser window has just opened.")
            print("  Log in with your university credentials as normal.")
            print("  Once you can see the Canvas dashboard,")
            if in_gui:
                print("  click the green button in the app to continue.")
            else:
                print("  come back here and press ENTER.")
            print("=" * 62)

            if in_gui:
                print("  [Waiting for GUI login confirmation...]")
                for _ in range(1200):
                    if GUI_SENTINEL_FILE.exists():
                        try:
                            GUI_SENTINEL_FILE.unlink()
                        except Exception:
                            pass
                        break
                    time.sleep(0.5)
            else:
                try:
                    input("\n  [Press ENTER after you are logged in] ")
                except EOFError:
                    time.sleep(5)

            try:
                page.wait_for_load_state("networkidle", timeout=20_000)
            except Exception:
                pass

            print("\n  Logged in — session saved for future runs.\n")
        else:
            log.info("  Already logged in (using saved session).")

        cookies = ctx.cookies()
        ctx.close()

    COOKIE_FILE.write_text(
        json.dumps(cookies, indent=2), encoding="utf-8"
    )
    return cookies


def cookies_for_domain(cookies: list[dict], base_url: str) -> str:
    domain = (
        base_url.replace("https://", "")
                .replace("http://", "")
                .split("/")[0]
    )
    relevant = [c for c in cookies if domain in c.get("domain", "")]
    return "; ".join(f"{c['name']}={c['value']}" for c in relevant)
