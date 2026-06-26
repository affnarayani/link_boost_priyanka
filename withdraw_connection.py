import os
import sys
import json
import time
import random
import re
from datetime import datetime, timedelta
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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

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
        print("[DONE] No data found in connections JSON file.", flush=True)
        sys.exit(0)

    target_item = None
    target_index = -1
    seven_days_ago = datetime.now() - timedelta(days=7)

    # 1. JSON filter validation conditions check karna
    for index, item in enumerate(connections):
        # Condition A: withdrawn false hona chahiye (ya key missing ho)
        if item.get("withdrawn") is False:
            timestamp_str = item.get("timestamp")
            if timestamp_str:
                try:
                    # Expected format matching your logs: "YYYY-MM-DD HH:MM:SS"
                    item_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    
                    # Condition B: Timestamp 7 din se pehle (old) ka hona chahiye
                    if item_time < seven_days_ago:
                        target_item = item
                        target_index = index
                        break
                except ValueError:
                    print(f"[WARNING] Invalid date format for index {index}, skipping.", flush=True)

    # Agar koi profile conditions meet nahi karti, toh sysexit0
    if target_item is None:
        print("[INFO] No profiles found with withdrawn=False and timestamp older than 7 days. Exiting.", flush=True)
        sys.exit(0)

    name = target_item.get("name", "Unknown")
    profile_link = target_item.get("profile_link")

    if not profile_link:
        print(f"[ERROR] Target profile '{name}' doesn't have a valid profile_link. Exiting.", flush=True)
        sys.exit(0)

    print(f"[ELIGIBLE] Found Profile: {name} | Timestamp: {target_item['timestamp']}", flush=True)

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

        print(f"[NAVIGATION] Navigating to target profile: {profile_link}", flush=True)
        page.goto(profile_link, wait_until="load")
        custom_random_wait(3, 6) # Page fully load hone ka short wait

        # Locators setup
        pending_button = page.get_by_test_id('lazy-column').get_by_role('link', name='Pending, click to withdraw')

        # Check if Pending button is visible
        if pending_button.is_visible() or pending_button.count() > 0:
            print("[ACTION] Pending button found. Clicking to open withdraw modal...", flush=True)
            pending_button.click()
            
            # Wait 15 to 30 random seconds after click
            custom_random_wait(15, 30)

            # Locate and click: Withdraw confirmation button (Regex pattern safe text matching)
            confirm_regex = re.compile(r"Withdraw invitation sent to", re.IGNORECASE)
            withdraw_confirm_btn = page.get_by_role('button', name=confirm_regex)

            if withdraw_confirm_btn.is_visible() or withdraw_confirm_btn.count() > 0:
                print("[ACTION] Clicking confirmation 'Withdraw invitation sent to' button.", flush=True)
                withdraw_confirm_btn.click()
                
                # Wait 15 to 30 seconds after final interaction
                custom_random_wait(15, 30)
            else:
                print("[INFO] Confirmation withdraw popup button not found/visible.", flush=True)
        else:
            print("[INFO] 'Pending, click to withdraw' button not found on this profile.", flush=True)

        # Update json array structure state (Chahe pending button mile ya na mile)
        connections[target_index]["withdrawn"] = True
        save_connections(connections_path, connections)
        print(f"[SUCCESS] JSON state updated: withdrawn=True for {name}.", flush=True)

        # Browser close karne se pehle requirement delay wait
        print("[SHUTDOWN] Executing pre-close session buffer wait...", flush=True)
        custom_random_wait(15, 30)

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
    load_dotenv()
    run()