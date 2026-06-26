import os
import sys
import json
import time
import random
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
# login.py se session import kiya gaya hai
from login import login_and_get_context

# =========================
# CONFIG
# =========================
HEADLESS = True
CONNECTIONS_FILE = "scraped_connections.json"

# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=15, max_sec=30):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

# =========================
# JSON DATA HANDLING
# =========================
def load_connections(file_path: Path) -> List[Dict[str, Any]]:
    if not file_path.exists():
        print(f"[ERROR] {file_path.name} not found.", flush=True)
        return []
    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_connections(file_path: Path, data: List[Dict[str, Any]]):
    with file_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    print(f"[INFO] Saved status updates to {file_path.name}", flush=True)

# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    connections_path = Path(CONNECTIONS_FILE)
    connections = load_connections(connections_path)

    if not connections:
        print("[DONE] No connections data to process.", flush=True)
        return

    # SESSION INITIALIZATION VIA login.py
    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        pw, browser, context, page = login_and_get_context(is_headless=HEADLESS)
    except Exception as e:
        print(f"[ERROR] Login session failed: {e}", flush=True)
        sys.exit(1)

    try:
        linkedin_url = "https://www.linkedin.com/feed/"
        print(f"[STEP] Opening LinkedIn Feed: {linkedin_url}", flush=True)
        page.goto(linkedin_url, wait_until="load")
        
        print("[STEP] Verifying login status via 'Me' button...", flush=True)
        me_button = page.get_by_role('button', name='Me', exact=True)
        me_button.wait_for(state="visible", timeout=120000)
        print("[SUCCESS] Login success! 'Me' button detected.\n", flush=True)

        # Loop over profiles in JSON
        for index, item in enumerate(connections):
            # Agar 'sent' key already present hai, toh skip karein
            if "sent" in item:
                continue

            name = item.get("name", "Unknown")
            profile_link = item.get("profile_link")

            if not profile_link:
                print(f"[SKIP] Missing profile link for index {index}", flush=True)
                continue

            print(f"[PROCESSING] Moving to profile: {name} ({profile_link})", flush=True)
            
            try:
                # 1. Target Profile Par Jana
                page.goto(profile_link, wait_until="load")
                custom_random_wait(3, 6) # Page fully stabilize hone ke liye chota wait

                # 2. Lazy Column locator inside checking pattern text match structure
                regex_pattern = re.compile(f"Invite {re.escape(name)}", re.IGNORECASE)
                connect_button = page.get_by_test_id('lazy-column').get_by_role('link', name=regex_pattern)

                if connect_button.is_visible() or connect_button.count() > 0:
                    print(f"[ACTION] 'Invite' button found for {name}. Clicking now...", flush=True)
                    connect_button.click()
                    
                    # Connect click karne ke baad wait: 15, 30 random seconds
                    custom_random_wait(15, 30)

                    # Confirmation button handle karna
                    confirm_button = page.get_by_role('button', name='Send without a note', exact=True)
                    if confirm_button.is_visible() or confirm_button.count() > 0:
                        print("[ACTION] 'Send without a note' clicked.", flush=True)
                        confirm_button.click()
                        
                        # Note submit confirm click ke baad wait: 15, 30 random seconds
                        custom_random_wait(15, 30)
                    else:
                        print("[INFO] 'Send without a note' button popup missing or auto-sent.", flush=True)
                else:
                    print(f"[INFO] Connect / Invite link button not found for {name}.", flush=True)

            except Exception as item_error:
                print(f"[WARNING] Error handling item {name}: {item_error}", flush=True)

            # 3. State JSON append step (Chahe button mile ya na mile)
            item["sent"] = True
            item["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            item["withdrawn"] = False

            # JSON file live save update line
            save_connections(connections_path, connections)
            print(f"[SUCCESS] Logs updated for {name}. Requesting browser environment teardown exit sequence.\n", flush=True)
            
            # Browser exit policy for per execution batch cycle
            break

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Script execution broke down due to trace:", e, flush=True)
        if page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[SCREENSHOT] Failure screenshot saved at: {screenshot_path}", flush=True)
            except Exception as s_e:
                print(f"[ERROR] Could not capture screenshot: {s_e}", flush=True)
        sys.exit(1)

    finally:
        if browser:
            try:
                browser.close()
            except:
                pass

        if pw:
            try:
                pw.stop()
            except:
                pass

        print("[DONE] Script execution environment torn down cleanly.", flush=True)


if __name__ == "__main__":
    run()