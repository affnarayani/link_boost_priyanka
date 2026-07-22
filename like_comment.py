import os
import sys
import json
import time
import random
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
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
# DYNAMIC WAITS & SCROLL
# =========================
def custom_random_wait(min_sec: float, max_sec: float):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)

def slow_scroll_to_bottom(page, step_pixels: int = 250, delay_sec: float = 0.4):
    """
    Dheere-dheere page ke bottom tak scroll karta hai taaki post elements load ho sakein.
    """
    print("[STEP] Dheere-dheere page scroll down kar rahe hain...", flush=True)
    
    while True:
        current_scroll = page.evaluate("window.scrollY")
        total_height = page.evaluate("document.body.scrollHeight - window.innerHeight")
        
        page.mouse.wheel(0, step_pixels)
        time.sleep(delay_sec)
        
        new_scroll = page.evaluate("window.scrollY")
        if new_scroll == current_scroll or new_scroll >= total_height:
            print("[OK] Page completely scroll ho gaya.", flush=True)
            break

# =========================
# DOM DOMINANT INTERACTION HELPERS
# =========================
def focus_and_click_comment_box(page, max_timeout: int = 15) -> bool:
    """
    DOM Selectors use karke Comment Box ko locate, scroll, aur focus karta hai.
    """
    print("[DOM SEARCH] Locating comment box via DOM selectors...", flush=True)
    
    selectors = [
        "div.ql-editor[contenteditable='true']",
        "div[contenteditable='true'][role='textbox']",
        "div[role='textbox']",
        "textarea.comments-comment-box__textarea",
        ".ql-editor"
    ]
    
    start_time = time.time()
    while time.time() - start_time < max_timeout:
        for selector in selectors:
            try:
                elem = page.locator(selector).first
                if elem.is_visible():
                    elem.scroll_into_view_if_needed()
                    custom_random_wait(0.5, 1.0)
                    elem.click()
                    
                    # Ensure focus via JS for rich text editor
                    page.evaluate("""
                        (sel) => {
                            const el = document.querySelector(sel);
                            if (el) {
                                el.focus();
                                if (el.getAttribute('contenteditable') === 'true') {
                                    const range = document.createRange();
                                    const sel = window.getSelection();
                                    range.selectNodeContents(el);
                                    range.collapse(false);
                                    sel.removeAllRanges();
                                    sel.addRange(range);
                                }
                            }
                        }
                    """, selector)
                    
                    print(f"[SUCCESS] Comment box located and focused via selector: '{selector}'", flush=True)
                    return True
            except Exception:
                continue
        time.sleep(1)
        
    print("[ERROR] Comment box not found using DOM selectors.", flush=True)
    return False

def click_like_button(page, max_timeout: int = 10) -> bool:
    """
    DOM Selectors use karke 'React Like' button ko locate aur click karta hai.
    """
    print("[DOM SEARCH] Locating 'React Like' button via DOM selectors...", flush=True)
    
    selectors = [
        "button[aria-label*='React Like']",
        "button[aria-label*='Like']",
        "button.react-button__trigger",
        "button.artdeco-button:has-text('Like')",
        "button:has-text('Like')"
    ]
    
    start_time = time.time()
    while time.time() - start_time < max_timeout:
        for selector in selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible():
                    btn.scroll_into_view_if_needed()
                    custom_random_wait(0.5, 1.0)
                    btn.click()
                    print(f"[SUCCESS] Post liked via selector: '{selector}'", flush=True)
                    return True
            except Exception:
                continue
        time.sleep(1)
        
    print("[WARNING] Could not locate or click Like button via DOM selectors.", flush=True)
    return False

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
        custom_random_wait(4, 8)

        # 4. CHECK GROUP RESTRICTION
        print("[STEP] Checking for restriction text...", flush=True)
        restricted_text = page.get_by_text("Only group members can")

        if restricted_text.count() > 0 and restricted_text.first.is_visible():
            print("[INFO] 'Only group members can...' restriction text found. Treating as SUCCESS.", flush=True)
            
            commented_urls = []
            if COMMENTED_FILE.exists():
                with COMMENTED_FILE.open("r", encoding="utf-8") as f:
                    try: commented_urls = json.load(f)
                    except: commented_urls = []
            
            if target_url not in commented_urls:
                commented_urls.append(target_url)
                with COMMENTED_FILE.open("w", encoding="utf-8") as f:
                    json.dump(commented_urls, f, indent=4, ensure_ascii=False)

            status_data["comment_posted"] = True
            with STATUS_FILE.open("w", encoding="utf-8") as f:
                json.dump(status_data, f, indent=4, ensure_ascii=False)

            print("[STEP] Finalizing restricted post flow...", flush=True)
            custom_random_wait(5, 10)
            
            reset_status = {"post_to_comment_found": False, "comment_generated": False, "comment_posted": False}
            with STATUS_FILE.open("w", encoding="utf-8") as f:
                json.dump(reset_status, f, indent=4, ensure_ascii=False)
                
            print("[SUCCESS] Exiting safely with code 0.", flush=True)
            return

        # 5. SCROLL TO BOTTOM & LOCATE COMMENT BOX VIA DOM
        print("[STEP] Locating comment box via DOM selectors...", flush=True)
        slow_scroll_to_bottom(page, step_pixels=250, delay_sec=0.4)
        custom_random_wait(2, 3)

        box_clicked = focus_and_click_comment_box(page, max_timeout=15)

        if not box_clicked:
            raise Exception("Could not locate or focus comment box via DOM selectors.")

        custom_random_wait(1, 2)
        
        # 6. TYPE COMMENT
        print("[STEP] Typing comment...", flush=True)
        page.keyboard.type(comment_text, delay=70)
        custom_random_wait(1, 2)

        # Executive Input Fallback
        page.evaluate("""
            (text) => {
                let active = document.activeElement;
                if (active && (active.isContentEditable || active.getAttribute('contenteditable') === 'true')) {
                    if (active.innerText.trim() === '') {
                        document.execCommand('insertText', false, text);
                        active.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                }
            }
        """, comment_text)

        custom_random_wait(1, 2)

        # 7. SUBMIT COMMENT (3x TAB + 1x ENTER WITH RANDOM DELAYS)
        print("[STEP] Submitting comment using Keyboard sequence (3 TABs + ENTER)...", flush=True)
        for i in range(1, 4):
            page.keyboard.press("Tab")
            print(f"[KEYBOARD] Pressed TAB ({i}/3)", flush=True)
            custom_random_wait(1, 2)

        page.keyboard.press("Enter")
        print("[KEYBOARD] Pressed ENTER to post comment.", flush=True)
        custom_random_wait(6, 12)

        # 8. CLICK LIKE BUTTON VIA DOM
        print("[STEP] Locating 'React Like' button via DOM...", flush=True)
        like_clicked = click_like_button(page, max_timeout=10)

        # 9. APPEND TO HISTORY
        commented_urls = []
        if COMMENTED_FILE.exists():
            with COMMENTED_FILE.open("r", encoding="utf-8") as f:
                try: commented_urls = json.load(f)
                except: commented_urls = []
        
        if target_url not in commented_urls:
            commented_urls.append(target_url)
            with COMMENTED_FILE.open("w", encoding="utf-8") as f:
                json.dump(commented_urls, f, indent=4, ensure_ascii=False)

        # 10. UPDATE STATUS
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
                
                upload_to_tmpfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
        sys.exit(1)
    finally:
        if 'browser' in locals() and browser: browser.close()
        if 'pw' in locals() and pw: pw.stop()

if __name__ == "__main__":
    run()