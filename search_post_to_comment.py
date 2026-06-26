import os
import sys
import json
import time
import random
import re
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
# login.py se function import karna hoga
from login import login_and_get_context 

# =========================
# CONFIG
# =========================
HEADLESS = True
JSON_OUTPUT_FILE = "post_to_comment.json"
STATUS_FILE = "comment_status.json"

# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=15, max_sec=30):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # 1. Status Check
    status_path = Path(STATUS_FILE)
    if not status_path.exists():
        print(f"[ERROR] {STATUS_FILE} nahi mili!", flush=True)
        sys.exit(1)

    with open(status_path, "r", encoding="utf-8") as f:
        status_data = json.load(f)

    if (status_data.get("post_to_comment_found") is not False or 
        status_data.get("comment_generated") is not False or 
        status_data.get("comment_posted") is not False):
        print("[INFO] Status check failed: Flags are not all 'false'.", flush=True)
        sys.exit(0)

    # 2. Use login.py for session
    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        pw, browser, context, page = login_and_get_context(is_headless=HEADLESS)
        context.grant_permissions(["clipboard-read", "clipboard-write"])
    except Exception as e:
        print(f"[ERROR] Login failed: {e}", flush=True)
        sys.exit(1)

    try:
        linkedin_url = "https://www.linkedin.com/feed/"
        page.goto(linkedin_url, wait_until="load")
        
        custom_random_wait(6, 12)

        # 3. Locate and click control menu
        print("[STEP] Locating control menu for the first post...", flush=True)
        control_menu_btn = page.get_by_role("button", name=re.compile(r"Open control menu for post by.*", re.IGNORECASE)).first
        control_menu_btn.click()
        custom_random_wait(6, 12)

        # 4. Click 'Copy link to post'
        print("[STEP] Clicking 'Copy link to post'...", flush=True)
        page.get_by_text("Copy link to post").click()
        custom_random_wait(6, 12)

        # 5. Read clipboard
        raw_url = page.evaluate("navigator.clipboard.readText()")
        trimmed_url = raw_url.split("?")[0]
        print(f"[INFO] Trimmed URL: {trimmed_url}", flush=True)

        # 6. Check commented.json
        commented_file = Path("commented.json")
        if commented_file.exists():
            with open(commented_file, "r", encoding="utf-8") as f:
                commented_data = json.load(f)
                if trimmed_url in commented_data:
                    print(f"[INFO] URL already commented. Exiting.", flush=True)
                    sys.exit(1)

        # 7. Extract content
        page.goto(trimmed_url, wait_until="load")
        custom_random_wait(6, 12)
        post_content = page.locator(".update-components-text").first.inner_text().strip()
        
        if len(post_content) < 150:
            print("[FAIL] Content too short.", flush=True)
            sys.exit(1)

        # 8. Save Data
        with open(JSON_OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump({"url": trimmed_url, "content": post_content}, f, indent=4, ensure_ascii=False)
        
        # 9. Update Status
        status_data["post_to_comment_found"] = True
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=4, ensure_ascii=False)

        print("[SUCCESS] Process completed.", flush=True)
        custom_random_wait(15, 30)

    except Exception as e:
        print(f"[ERROR] {e}", flush=True)
        if page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[SCREENSHOT] Failure screenshot saved at: {screenshot_path}", flush=True)
            except Exception as s_e:
                print(f"[ERROR] Could not capture screenshot: {s_e}", flush=True)
        sys.exit(1)
    finally:
        if browser: browser.close()
        if pw: pw.stop()

if __name__ == "__main__":
    run()