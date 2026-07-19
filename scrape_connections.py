import os
import sys
import json
import time
import random
import re  # Hidden characters aur special verified string patterns clean karne ke liye
import requests
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
TARGET_URL = "https://www.linkedin.com/search/results/people/?keywords=advocate&origin=FACETED_SEARCH&geoUrn=%5B%22102913253%22%5D"
OUTPUT_FILE = "scraped_connections.json"

# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=15, max_sec=30):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

# =========================
# FILE HELPERS
# =========================
def clear_json_file(file_path: str):
    print(f"[INIT] Clearing contents of {file_path}...", flush=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump([], f)


def append_to_json(file_path: str, data: Dict[str, str]):
    existing_data = []
    path = Path(file_path)
    if path.exists() and path.stat().st_size > 0:
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
        except Exception:
            existing_data = []
    
    existing_data.append(data)
    
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=4)


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    clear_json_file(OUTPUT_FILE)

    # SESSION INITIALIZATION VIA login.py
    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        pw, browser, context, page = login_and_get_context(is_headless=HEADLESS)
    except Exception as e:
        print(f"[ERROR] Login session failed: {e}", flush=True)
        sys.exit(1)

    try:
        linkedin_feed = "https://www.linkedin.com/feed/"
        print(f"[STEP] Opening LinkedIn Feed: {linkedin_feed}", flush=True)
        page.goto(linkedin_feed, wait_until="load")
        
        print("[VALIDATE] Searching for login verification locator: 'Me' button (Timeout: 120s)...", flush=True)
        login_indicator = page.get_by_role('button', name='Me', exact=True)
        
        login_indicator.wait_for(state="visible", timeout=120000)
        print("[SUCCESS] Login verified via 'Me' button. Proceeding to target URL page...", flush=True)

        current_page = 1
        empty_pages_count = 0

        while True:
            url_to_navigate = TARGET_URL if current_page == 1 else f"{TARGET_URL}&page={current_page}"
            print(f"[STEP] Navigating to target page {current_page}: {url_to_navigate}", flush=True)
            page.goto(url_to_navigate, wait_until="load")
            page.wait_for_timeout(5000)

            all_links = page.get_by_role('link').all()
            
            if not all_links:
                print(f"[INFO] No role links found on page {current_page}.", flush=True)
                if 'page' in locals() and page:
                    try:
                        screenshot_path = "error_screenshot.png"
                        page.screenshot(path=screenshot_path, full_page=True)
                        print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                        
                        imgbb_key = os.getenv("IMGBBB_API_KEY")
                        if imgbb_key:
                            print("[OK] Uploading screenshot to ImgBB...", flush=True)
                            url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
                            
                            with open(screenshot_path, "rb") as file:
                                response = requests.post(url, files={"image": file})
                            
                            if response.status_code == 200:
                                res_data = response.json()
                                direct_url = res_data["data"]["display_url"]
                                print("\n" + "="*50, flush=True)
                                print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                                print("="*50 + "\n", flush=True)
                            else:
                                print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
                        else:
                            print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
                    except Exception as screenshot_err:
                        print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
                sys.exit(1)

            profiles_scraped_on_this_page = 0
            processed_names = set()

            for link in all_links:
                try:
                    raw_text = link.inner_text()
                    if not raw_text:
                        continue
                    
                    # Newline aur extra/hidden spaces ko clean karke single space se normalize karein
                    normalized_text = re.sub(r'\s+', ' ', raw_text).strip()
                    
                    if not normalized_text or len(normalized_text) > 80:
                        continue
                    
                    # Cleaned name nikalne ke liye 'Verified' check lagayein
                    if "Verified" in normalized_text:
                        clean_name = re.sub(r'\s+Verified$', '', normalized_text).strip()
                    else:
                        clean_name = normalized_text

                    if not clean_name or clean_name in processed_names:
                        continue

                    # Name match karne ke liye flexible regex patterns jo normal aur verified dono ko handle karein
                    name_regex = re.compile(rf"^{re.escape(clean_name)}(\s+Verified)?$")
                    name_locator = page.get_by_role('link', name=name_regex, exact=True)
                    
                    # Connect text button ke liye cleaned structural identity pass karein
                    connect_locator = page.get_by_role('link', name=f'Invite {clean_name} to connect', exact=True)

                    if name_locator.count() > 0 and connect_locator.count() > 0:
                        processed_names.add(clean_name)
                        profile_url = name_locator.first.get_attribute("href")
                        if profile_url and profile_url.startswith("/"):
                            profile_url = f"https://www.linkedin.com{profile_url}"

                        print(f"[SCRAPE] Match found strictly via specified locators: {clean_name}", flush=True)
                        profile_data = {
                            "name": clean_name,
                            "profile_link": profile_url
                        }
                        append_to_json(OUTPUT_FILE, profile_data)
                        profiles_scraped_on_this_page += 1
                        
                except Exception:
                    continue

            print(f"[PAGE SUMMARY] Page {current_page} execution done. Appended: {profiles_scraped_on_this_page}", flush=True)

            if profiles_scraped_on_this_page == 0:
                empty_pages_count += 1
            else:
                empty_pages_count = 0

            if empty_pages_count >= 3:
                print("[TERMINATE] Continuous 3 pages with 0 results recorded. Stopping workflow.", flush=True)
                break

            current_page += 1
            time.sleep(random.uniform(2, 5))

        print("[SUCCESS] All rules executed. Preparing final window teardown.", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Script execution broke down due to trace:", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                imgbb_key = os.getenv("IMGBBB_API_KEY")
                if imgbb_key:
                    print("[OK] Uploading screenshot to ImgBB...", flush=True)
                    url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
                    
                    with open(screenshot_path, "rb") as file:
                        response = requests.post(url, files={"image": file})
                    
                    if response.status_code == 200:
                        res_data = response.json()
                        direct_url = res_data["data"]["display_url"]
                        print("\n" + "="*50, flush=True)
                        print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                        print("="*50 + "\n", flush=True)
                    else:
                        print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
                else:
                    print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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