"""Full-size screenshots of the dashboard tabs (headless Edge/Chrome), for review and the portfolio.

    python run.py screenshots      # writes docs/img/dashboard_<tab>[_dark].png
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from . import config
from .doc_export import BROWSERS

TABS = ["overview", "prices", "invoices", "quality"]
OUT = config.ROOT / "docs" / "img"


def capture(width: int = 1600, height: int = 1200, dark: bool = False, tabs: list[str] | None = None) -> list[Path]:
    browser = next((b for b in BROWSERS if b.exists()), None)
    if not browser:
        raise SystemExit("Edge or Chrome is needed for screenshots.")
    page = config.ROOT / "docs" / "index.html"
    OUT.mkdir(parents=True, exist_ok=True)
    shots = []
    for tab in tabs or TABS:
        path = OUT / f"dashboard_{tab}{'_dark' if dark else ''}.png"
        with tempfile.TemporaryDirectory() as profile:   # fresh profile: no cached state between shots
            # 1.25x device scale: a 1280 px wide layout fills the 1600 px image
            cmd = [str(browser), "--headless", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                   "--force-device-scale-factor=1.25",
                   f"--window-size={int(width / 1.25)},{int(height / 1.25)}", f"--screenshot={path}",
                   page.as_uri() + f"?theme={'dark' if dark else 'light'}#{tab}"]
            for attempt in range(3):                      # headless Edge occasionally hangs: retry
                try:
                    subprocess.run(cmd, check=True, capture_output=True, timeout=45)
                    break
                except subprocess.TimeoutExpired:
                    if attempt == 2:
                        raise
        shots.append(path)
    return shots
