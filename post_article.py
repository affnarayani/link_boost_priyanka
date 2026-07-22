import os
import sys
import json
import time
import random
import re
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
TOPICS_FILE = "priyanka_linkedin_topics.json"
POST_FILE = "post.json"
IMAGE_PATH = "image/image.png"

# =========================
# DYNAMIC WAITS
# =========================
def custom_random_wait(min_sec=15, max_sec=30):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

def step_wait():
    seconds = random.uniform(6, 12)
    print(f"[WAIT] Dynamic step delay: sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

# =========================
# TEXT PARSING HELPER
# =========================
def clean_and_format_post(post_data: Dict[str, Any]) -> str:
    p1 = re.sub(r'\n+', '\n', post_data.get("p1", ""))
    p2 = re.sub(r'\n+', '\n', post_data.get("p2", ""))
    p3 = re.sub(r'\n+', '\n', post_data.get("p3", ""))
    conclusion = re.sub(r'\n+', '\n', post_data.get("conclusion", ""))
    
    combined_body = f"{p1}\n{p2}\n{p3}\n{conclusion}"
    combined_body = re.sub(r'\n+', '\n', combined_body)
    
    keywords = post_data.get("keywords", [])
    hashtags = " ".join([f"#{kw.strip()}" for kw in keywords])
    
    full_text = f"{combined_body}\n{hashtags}"
    return full_text

def upload_to_tmpfiles(screenshot_path):
    url = "https://tmpfiles.org/api/v1/upload"
    
    with open(screenshot_path, "rb") as file:
        response = requests.post(url, files={"file": file})
        
    if response.status_code == 200:
        res_data = response.json()
        # Direct view URL banane ke liye '/dl/' replace karte hain
        page_url = res_data["data"]["url"]
        direct_url = page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
        print(f"👉 DIRECT LINK (Expires in 2 Hours): {direct_url}")
        return direct_url
    else:
        print(f"[WARNING] Upload Failed: {response.status_code}")
        return None
    
# =========================
# MAIN RUNNER
# =========================
def run():
    print("[START] Script started", flush=True)

    p_file = Path(POST_FILE)
    t_file = Path(TOPICS_FILE)

    if not p_file.exists() or not t_file.exists():
        print(f"[ERROR] Required files missing ({POST_FILE} or {TOPICS_FILE})", flush=True)
        return

    with p_file.open("r", encoding="utf-8") as f:
        post_data = json.load(f)
    current_title = post_data.get("title")

    with t_file.open("r", encoding="utf-8") as f:
        topics_list = json.load(f)

    target_topic = None
    target_index = -1
    for idx, item in enumerate(topics_list):
        if item.get("topic") == current_title:
            target_topic = item
            target_index = idx
            break

    if not target_topic:
        print(f"[SKIP] Topic '{current_title}' not found.", flush=True)
        return

    if not (target_topic.get("content_generated") is True and 
            target_topic.get("image_generated") is True and 
            target_topic.get("posted") is False):
        print(f"[SKIP] Conditions not met.", flush=True)
        return

    # SESSION INITIALIZATION
    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        pw, browser, context, page = login_and_get_context(is_headless=HEADLESS)
    except Exception as e:
        print(f"[ERROR] Login session failed: {e}", flush=True)
        sys.exit(1)

    try:
        linkedin_url = "https://www.linkedin.com/feed/"
        page.goto(linkedin_url, wait_until="load")
        
        print("[STEP] Verifying login status...", flush=True)
        me_button = page.get_by_role('button', name='Me', exact=True)
        me_button.wait_for(state="visible", timeout=120000)
        step_wait()

        print("[STEP] Clicking 'Start a post'...", flush=True)
        page.get_by_role('link', name='Start a post').click()
        step_wait()

        full_post_text = clean_and_format_post(post_data)
        editor = page.get_by_role('textbox', name='Text editor for creating')
        editor.focus()
        editor.press_sequentially(full_post_text, delay=40, timeout=0)
        step_wait()

        print("[STEP] Uploading media...", flush=True)
        img_file = Path(IMAGE_PATH)
        with page.expect_file_chooser() as fc_info:
            page.get_by_role('button', name='Add media').click()
        fc_info.value.set_files(str(img_file))
        step_wait()

        page.get_by_test_id('interop-shadowdom').get_by_role('button', name='Next').click()
        step_wait()

        print("[STEP] Posting...", flush=True)
        page.get_by_role('button', name='Post', exact=True).click()
        step_wait()

        topics_list[target_index]["posted"] = True
        with t_file.open("w", encoding="utf-8") as f:
            json.dump(topics_list, f, indent=4, ensure_ascii=False)

        custom_random_wait(15, 30)

    except Exception as e:
        print("[ERROR] Script crashed:", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                upload_to_tmpfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
        sys.exit(1)
    finally:
        if browser: browser.close()
        if pw: pw.stop()

if __name__ == "__main__":
    run()