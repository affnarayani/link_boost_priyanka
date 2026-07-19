import os
import sys
import json
import time
import random
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
STATUS_FILE = Path("comment_status.json")
POST_DATA_FILE = Path("post_to_comment.json")
COMMENTED_FILE = Path("commented.json")

# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # 1. CONDITION CHECK: comment_status.json
    if not STATUS_FILE.exists():
        print(f"[ERROR] {STATUS_FILE.name} nahi mili! Execution stopped.", flush=True)
        sys.exit(0)

    try:
        with STATUS_FILE.open("r", encoding="utf-8") as f:
            status_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] {STATUS_FILE.name} parse karne me issue: {e}", flush=True)
        sys.exit(0)

    if (status_data.get("post_to_comment_found") is True and 
        status_data.get("comment_generated") is True and 
        status_data.get("comment_posted") is False):
        print("[OK] Target status matched. Proceeding with browser setup...", flush=True)
    else:
        print(f"[INFO] Status requirements match nahi hui. Exiting...", flush=True)
        sys.exit(0)

    # 2. READ DATA
    if not POST_DATA_FILE.exists():
        print(f"[ERROR] {POST_DATA_FILE.name} nahi mili!", flush=True)
        sys.exit(0)

    try:
        with POST_DATA_FILE.open("r", encoding="utf-8") as f:
            post_data = json.load(f)
        target_url = post_data.get("url", "").strip()
        comment_text = post_data.get("comment", "").strip()
    except Exception as e:
        print(f"[ERROR] {POST_DATA_FILE.name} read error: {e}", flush=True)
        sys.exit(0)

    # 3. SESSION INITIALIZATION VIA login.py
    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        pw, browser, context, page = login_and_get_context(is_headless=HEADLESS)
    except Exception as e:
        print(f"[ERROR] Login session failed: {e}", flush=True)
        sys.exit(1)

    try:
        # Navigate to target
        print(f"[STEP] Navigating to target post URL: {target_url}", flush=True)
        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        custom_random_wait(6, 12)

        # 4. LOCATE TEXTBOX OR RESTRICTION
        print("[STEP] Checking for comment box or restriction...", flush=True)
        
        comment_box = page.locator('.comments-comment-box-comment__text-editor')
        restricted_text = page.get_by_text('Only group members can')

        # Wait until either the comment box OR the restricted text becomes visible
        try:
            page.wait_for_function(
                "() => document.querySelector('.comments-comment-box-comment__text-editor') || document.body.innerText.includes('Only group members can')",
                timeout=30000
            )
        except Exception as e:
            print("[ERROR] Neither comment box nor restriction text found within timeout.", flush=True)
            raise e

        # CONDITION CHECK: Agar group restriction text dikhta hai
        if restricted_text.count() > 0 and restricted_text.is_visible():
            print("[INFO] 'Only group members can...' restriction text found. Treating as SUCCESS.", flush=True)
            
            # History me URL add karke success mark karein
            commented_urls = []
            if COMMENTED_FILE.exists():
                with COMMENTED_FILE.open("r", encoding="utf-8") as f:
                    try: commented_urls = json.load(f)
                    except: commented_urls = []
            
            if target_url not in commented_urls:
                commented_urls.append(target_url)
                with COMMENTED_FILE.open("w", encoding="utf-8") as f:
                    json.dump(commented_urls, f, indent=4, ensure_ascii=False)

            # Status true mark karein
            status_data["comment_posted"] = True
            with STATUS_FILE.open("w", encoding="utf-8") as f:
                json.dump(status_data, f, indent=4, ensure_ascii=False)

            print("[STEP] Finalizing restricted post flow...", flush=True)
            custom_random_wait(5, 10)
            
            # Status file ko reset karein agli run ke liye
            reset_status = {"post_to_comment_found": False, "comment_generated": False, "comment_posted": False}
            with STATUS_FILE.open("w", encoding="utf-8") as f:
                json.dump(reset_status, f, indent=4, ensure_ascii=False)
                
            print("[SUCCESS] Exiting safely with code 0.", flush=True)
            return
        
        # Agar restriction nahi mili, toh normal text box flow chalega
        print("[STEP] Comment box found. Proceeding to type...", flush=True)
        comment_box.click()
        custom_random_wait(2, 4)
        
        print("[STEP] Typing comment...", flush=True)
        comment_box.press_sequentially(comment_text, delay=random.uniform(60, 140), timeout=0)
        custom_random_wait(3, 6)

        # 5. KEYBOARD NAVIGATION
        print("[STEP] Executing Keyboard Flow...", flush=True)
        for i in range(1, 4):
            page.keyboard.press("Tab")
            custom_random_wait(3, 6)
            
        page.keyboard.press("Enter")
        custom_random_wait(6, 12)

        # 6. REACT LIKE
        print("[STEP] Locating 'React Like' button...", flush=True)
        like_btn = page.get_by_role('button', name='React Like', exact=True)
        if like_btn.count() > 0:
            like_btn.first.click()
            print("[SUCCESS] Post liked.", flush=True)

        # 7. APPEND TO HISTORY
        commented_urls = []
        if COMMENTED_FILE.exists():
            with COMMENTED_FILE.open("r", encoding="utf-8") as f:
                try: commented_urls = json.load(f)
                except: commented_urls = []
        
        if target_url not in commented_urls:
            commented_urls.append(target_url)
            with COMMENTED_FILE.open("w", encoding="utf-8") as f:
                json.dump(commented_urls, f, indent=4, ensure_ascii=False)

        # 8. UPDATE STATUS
        status_data["comment_posted"] = True
        with STATUS_FILE.open("w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=4, ensure_ascii=False)

        print("[STEP] Finalizing...", flush=True)
        custom_random_wait(15, 30)
        
        reset_status = {"post_to_comment_found": False, "comment_generated": False, "comment_posted": False}
        with STATUS_FILE.open("w", encoding="utf-8") as f:
            json.dump(reset_status, f, indent=4, ensure_ascii=False)

    except Exception as e:
        print("[ERROR] Script crashed:", e, flush=True)
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
        if browser: browser.close()
        if pw: pw.stop()

if __name__ == "__main__":
    run()