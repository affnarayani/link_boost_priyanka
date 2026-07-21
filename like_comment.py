import os
import sys
import json
import time
import random
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv

import cv2
import numpy as np
from PIL import Image

from playwright.sync_api import sync_playwright
# login.py se session import kiya gaya hai
from login import login_and_get_context

# =========================
# CONFIG
# =========================
HEADLESS = False  
STATUS_FILE = Path("comment_status.json")
POST_DATA_FILE = Path("post_to_comment.json")
COMMENTED_FILE = Path("commented.json")

# Image Locators Directory
LOCATOR_DIR = Path("locator_images")
IMG_COMMENT_BOX = LOCATOR_DIR / "comment_box.png"
IMG_LIKE_BUTTON = LOCATOR_DIR / "like_button.png"

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
# DPI-AWARE & ROBUST CLICKER
# =========================
def find_and_click_image_viewport_safe(page, template_path: Path, threshold: float = 0.75, max_timeout: int = 15) -> bool:
    """
    DPI devicePixelRatio handle karke correct CSS coordinates par click karta hai
    aur DOM fallback ke sath field focus ensure karta hai.
    """
    if not template_path.exists():
        print(f"[IMAGE ERROR] File not found: {template_path}", flush=True)
        return False

    print(f"[IMAGE SEARCH] Searching locator image '{template_path.name}' with threshold {int(threshold * 100)}%...", flush=True)

    template = cv2.imread(str(template_path), cv2.IMREAD_COLOR)
    if template is None:
        print(f"[IMAGE ERROR] Failed to load locator image: {template_path}", flush=True)
        return False

    t_height, t_width, _ = template.shape
    start_time = time.time()

    while time.time() - start_time < max_timeout:
        # Screenshot in memory
        screenshot_bytes = page.screenshot(full_page=False)
        nparr = np.frombuffer(screenshot_bytes, np.uint8)
        screen_img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        s_height, s_width, _ = screen_img.shape
        
        # Get Browser DPI Ratio & Viewport dimensions
        dpi_scale, v_width, v_height = page.evaluate("""
            () => [window.devicePixelRatio || 1, window.innerWidth, window.innerHeight]
        """)

        # Convert Image pixels to CSS pixels
        css_screen_w = s_width / dpi_scale
        css_screen_h = s_height / dpi_scale

        scale_x = v_width / css_screen_w
        scale_y = v_height / css_screen_h

        result = cv2.matchTemplate(screen_img, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        if max_val >= threshold:
            top_left_x, top_left_y = max_loc

            img_center_x = (top_left_x + (t_width // 2)) / dpi_scale
            img_center_y = (top_left_y + (t_height // 2)) / dpi_scale

            click_x = int(img_center_x * scale_x)
            click_y = int(img_center_y * scale_y)

            print(f"[IMAGE FOUND] Match: {max_val * 100:.2f}% | CSS Point: ({click_x}, {click_y})", flush=True)
            
            # Step 1: Native Playwright Mouse Click
            page.mouse.move(click_x, click_y)
            time.sleep(0.2)
            page.mouse.click(click_x, click_y, click_count=1)
            time.sleep(0.3)

            # Step 2: JS Focus with Guaranteed Exit
            page.evaluate("""
                (coords) => {
                    const x = coords.x;
                    const y = coords.y;
                    const elems = document.elementsFromPoint(x, y);
                    
                    if (elems && elems.length > 0) {
                        let target = elems[0];
                        
                        // Check for editable or interactive node
                        for (let el of elems) {
                            if (el.getAttribute('contenteditable') === 'true' || 
                                el.role === 'textbox' ||
                                el.tagName === 'TEXTAREA' || 
                                el.tagName === 'BUTTON' ||
                                el.tagName === 'INPUT') {
                                target = el;
                                break;
                            }
                        }
                        
                        target.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' });
                        target.focus();
                        target.click();

                        // Selection range set for rich text editors
                        if (target.getAttribute('contenteditable') === 'true' || target.role === 'textbox') {
                            const range = document.createRange();
                            const sel = window.getSelection();
                            range.selectNodeContents(target);
                            range.collapse(false);
                            sel.removeAllRanges();
                            sel.addRange(range);
                        }
                    }
                }
            """, {"x": click_x, "y": click_y})

            print(f"[SUCCESS] Image matched and clicked at CSS ({click_x}, {click_y})", flush=True)
            return True  # Exit loop immediately!

        time.sleep(1)

    print(f"[IMAGE FAILED] Locator image '{template_path.name}' not found. Best match: {max_val * 100:.2f}%", flush=True)
    return False

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

        # 5. SCROLL TO BOTTOM & LOCATE COMMENT BOX IMAGE
        print("[STEP] Locating comment box via viewport-safe image matching...", flush=True)
        slow_scroll_to_bottom(page, step_pixels=250, delay_sec=0.4)
        custom_random_wait(2, 3)

        box_clicked = find_and_click_image_viewport_safe(page, IMG_COMMENT_BOX, threshold=0.75, max_timeout=20)
        
        # DOM Selector Fallback if visual match couldn't focus
        if not box_clicked:
            print("[WARNING] Visual match failed. Using DOM Selector fallback for comment box...", flush=True)
            comment_box = page.locator(".ql-editor, div[contenteditable='true'], div[role='textbox']").first
            if comment_box.is_visible():
                comment_box.click()
                box_clicked = True

        if not box_clicked:
            raise Exception(f"Could not locate or focus '{IMG_COMMENT_BOX.name}' image or DOM element.")

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

        # 8. CLICK LIKE BUTTON
        print("[STEP] Locating 'React Like' button...", flush=True)
        like_clicked = find_and_click_image_viewport_safe(page, IMG_LIKE_BUTTON, threshold=0.75, max_timeout=10)
        
        if like_clicked:
            print("[SUCCESS] Post liked via image match.", flush=True)
        else:
            print("[WARNING] Could not locate Like button.", flush=True)

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
        if 'browser' in locals() and browser: browser.close()
        if 'pw' in locals() and pw: pw.stop()

if __name__ == "__main__":
    run()