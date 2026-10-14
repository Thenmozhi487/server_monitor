"""Helper script to capture high-resolution dashboard screenshots using headless Chrome or Edge."""

import os
import subprocess
import sys


def capture_dashboard_screenshot(screenshot_name: str, delay_seconds: int = 4) -> str:
    """Capture a screenshot of http://127.0.0.1:5000/ into docs/screenshots/<screenshot_name>.png."""
    out_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "docs", "screenshots")
    )
    os.makedirs(out_dir, exist_ok=True)

    if not screenshot_name.endswith(".png"):
        screenshot_name += ".png"
    target_path = os.path.join(out_dir, screenshot_name)

    browsers = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]

    selected_browser = next((b for b in browsers if os.path.exists(b)), None)
    if not selected_browser:
        raise RuntimeError("No headless browser binary found.")

    virtual_budget_ms = delay_seconds * 1000

    cmd = [
        selected_browser,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--window-size=1440,1100",
        f"--virtual-time-budget={virtual_budget_ms}",
        f"--screenshot={target_path}",
        "http://127.0.0.1:5000/",
    ]

    subprocess.run(cmd, check=True, capture_output=True, timeout=25)
    print(f"Captured screenshot: {target_path} ({os.path.getsize(target_path)} bytes)")
    return target_path


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "dashboard"
    delay = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    capture_dashboard_screenshot(name, delay)
