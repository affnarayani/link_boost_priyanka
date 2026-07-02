import os
import sys
import json
import time
import base64
import random
import shutil
import requests
import re
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


# =========================
# CONFIG
# =========================
HEADLESS = True

# Cookie folder changed to chatgpt_cookies
COOKIES_DIR = Path("chatgpt_cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'chatgpt_cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

IMAGE_DIR = Path("image")
IMAGE_DIR.mkdir(exist_ok=True)

PBKDF2_ITERATIONS = 200_000
MAX_RETRIES = 5  

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


# =========================
# RANDOM WAIT
# =========================
def random_wait():
    seconds = random.uniform(6, 12)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def custom_random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


# =========================
# CRYPTO
# =========================
def _derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def _decrypt_payload(payload: Dict[str, Any], password: str) -> bytes:
    salt = base64.b64decode(payload["s"])
    nonce = base64.b64decode(payload["n"])
    ciphertext = base64.b64decode(payload["ct"])

    key = _derive_key(password.encode("utf-8"), salt)
    aesgcm = AESGCM(key)

    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise RuntimeError("❌ Decryption failed (InvalidTag)")


def load_cookies(file_path: Path) -> List[Dict[str, Any]]:
    print("[STEP] Loading cookies...", flush=True)

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, DECRYPT_KEY)
    cookies = json.loads(plaintext.decode("utf-8"))

    # normalize SameSite and PartitionKey
    for c in cookies:
        if "partitionKey" in c and isinstance(c["partitionKey"], dict):
            if "topLevelSite" in c["partitionKey"]:
                c["partitionKey"] = str(c["partitionKey"]["topLevelSite"])
            else:
                del c["partitionKey"]

        if "sameSite" in c:
            val = str(c["sameSite"]).lower()

            if val in ["no_restriction", "none", "unspecified", "null"]:
                c["sameSite"] = "None"
            elif val == "lax":
                c["sameSite"] = "Lax"
            elif val == "strict":
                c["sameSite"] = "Strict"
            else:
                c["sameSite"] = "Lax"

    print("[OK] Cookies loaded", flush=True)
    return cookies


# ==================================
# NEW: VALIDATION & STATUS UPDATERS
# ==================================
def can_run_image_script() -> tuple:
    print("[STEP] Checking status in priyanka_linkedin_topics.json...", flush=True)
    topics_file = Path("priyanka_linkedin_topics.json")
    if not topics_file.exists():
        print("[INFO] 'priyanka_linkedin_topics.json' nahi mili. Execution stopped.", flush=True)
        return False, None
    
    try:
        with topics_file.open("r", encoding="utf-8") as f:
            topics = json.load(f)
    except Exception as e:
        print(f"[ERROR] Topics file read karne me dikkat: {e}.", flush=True)
        return False, None

    if not topics or not isinstance(topics, list):
        print("[INFO] Topics list khali hai.", flush=True)
        return False, None

    # Sabse aakhri item nikalna jo process ho raha tha
    last_processed_item = None
    for item in topics:
        if isinstance(item, dict) and "content_generated" in item:
            last_processed_item = item

    if last_processed_item is None:
        print("[INFO] Koi processed entry nahi mili.", flush=True)
        return False, None

    cg = last_processed_item.get("content_generated") is True
    ig = last_processed_item.get("image_generated") is True

    # Run strictly when content_generated=True and image_generated=False
    if cg and not ig:
        print(f"[OK] Last post '{last_processed_item.get('topic')}' validation clear! Running Image Generation.", flush=True)
        return True, last_processed_item.get("topic")
    else:
        print(f"[INFO] Script halted! Condition match nahi hui: content_generated={cg}, image_generated={ig}.", flush=True)
        return False, None


def update_image_status_in_json(topic_text: str):
    print("[STEP] Updating image status in priyanka_linkedin_topics.json...", flush=True)
    topics_file = Path("priyanka_linkedin_topics.json")
    if not topics_file.exists():
        return
        
    with topics_file.open("r", encoding="utf-8") as f:
        topics = json.load(f)
        
    for item in topics:
        if item.get("topic") == topic_text:
            item["image_generated"] = True
            break
            
    with topics_file.open("w", encoding="utf-8") as f:
        json.dump(topics, f, indent=4, ensure_ascii=False)
    print("[OK] Image status successfully updated (image_generated=True) in priyanka_linkedin_topics.json", flush=True)


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # Condition validator trigger
    can_run, topic = can_run_image_script()
    if not can_run:
        print("[INFO] Pre-conditions meet nahi hui. Exiting script gracefully...", flush=True)
        sys.exit(0)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))

    # ==================================
    # LOAD GENERATED POST DATA (post.json)
    # ==================================
    print("[STEP] Loading post JSON...", flush=True)
    post_file = Path("post.json")
    if not post_file.exists():
        print("[ERROR] 'post.json' file nahi mila.", flush=True)
        sys.exit(1)
        
    with post_file.open("r", encoding="utf-8") as json_file:
        post_data = json.load(json_file)
        
    post_title = post_data.get("title", topic)
    print(f"[OK] Post Title extracted: {post_title}", flush=True)

    p1 = post_data.get("p1", [])
    print(f"[OK] Post P1 extracted: {p1}", flush=True)

    p2 = post_data.get("p2", [])
    print(f"[OK] Post P2 extracted: {p2}", flush=True)

    p3 = post_data.get("p3", [])
    print(f"[OK] Post P3 extracted: {p3}", flush=True)

    conclusion = post_data.get("conclusion", [])
    print(f"[OK] Post conclusion extracted: {conclusion}", flush=True)

    post_keywords = post_data.get("keywords", [])
    print(f"[OK] Post Keywords extracted: {post_keywords}", flush=True)

    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent=USER_AGENT
        )

        context.grant_permissions(["clipboard-read", "clipboard-write"])
        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        # Optimized Image Prompt tailored for high-conversion LinkedIn feed visuals
        prompt = ("""
        Create image for a LinkedIn post with title: "{post_title}". Image must in the ratio 1:1. This will be a hero image to this LinkedIn post. Image must be engaging. The core idea of the image must revolve around these keywords: {post_keywords}
        """)

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto(
            "https://chatgpt.com/",
            wait_until="load"
        )
        print("[OK] URL opened", flush=True)

        print("[STEP] Performing initial random wait (30-60 seconds)...", flush=True)
        custom_random_wait(30, 60)

        open_button = page.get_by_test_id("existing-workspace-row").get_by_role("button", name="Open")
        if open_button.is_visible():
            open_button.click()
            print("[STEP] Open button par click kar diya gaya.", flush=True)
            custom_random_wait(30, 60)

        # CHECK LOGIN SUCCESS VIA USER PROFILE BUTTON
        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = page.get_by_role('button', name=list(map(lambda x: x.compile(r'.*Free, open'), [__import__('re')]))[0])
        
        if profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{profile_button.first.get_attribute('aria-label') or 'User Account'}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        if page.get_by_role('button', name='Create an image').is_visible():
            page.get_by_role('button', name='Create an image').click()
            print("[STEP] Create an image button clicked!...", flush=True)
            custom_random_wait(6, 12)

        # Locate chat box and type prompt
        print("[STEP] Locating chat textbox...", flush=True)
        chat_box = page.get_by_role('textbox', name='Chat with ChatGPT')

        if chat_box.count() == 0:
            print("[INFO] Fallback 1: Searching for 'Describe or edit an image' paragraph inside textbox context...", flush=True)
            chat_box = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Describe or edit an image')).first
            
        if chat_box.count() == 0:
            print("[INFO] Fallback 2: Searching via CSS Selector '#prompt-textarea'...", flush=True)
            chat_box = page.locator('#prompt-textarea')

        if chat_box.count() > 0:
            chat_box.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya (All strategies failed).")
        
        formatted_base = prompt.format(post_title=post_title, post_keywords=post_keywords)
        clean_base_prompt = " ".join(formatted_base.split())
        prompt_text = clean_base_prompt
        print(f"[STEP] Filling prompt: '{prompt_text}'", flush=True)
        chat_box.first.type(prompt_text)
        custom_random_wait(6, 12)
        
        page.keyboard.press("Enter")
        print("[OK] Prompt sent successfully", flush=True)

        # 'Share this image' retry loop
        share_button = None
        found_share = False

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for image generation... Attempt {attempt}/{MAX_RETRIES}", flush=True)
            custom_random_wait(30, 60)
            
            # --- START: MODIFIED LOGIC FOR PREFERENCE HANDLING ---
            img1_better = page.get_by_role('button', name='Image 1 is better')
            img2_better = page.get_by_role('button', name='Image 2 is better')
            skip_btn = page.get_by_role('button', name='Skip')
            
            p_img1 = img1_better.count() > 0
            p_img2 = img2_better.count() > 0
            
            # Agar dono buttons dikh rahe hai
            if p_img1 and p_img2:
                print(f"[INFO] Feedback required! 'Image 1 is better' and 'Image 2 is better' both found.", flush=True)
                choice = random.choice([img1_better, img2_better])
                print(f"[STEP] Selecting preference button randomly...", flush=True)
                choice.first.click()
                print(f"[OK] Clicked preference button. Waiting 30-60 seconds...", flush=True)
                custom_random_wait(30, 60)
                # Ab loop ko age badhne do agle logic check ke liye
            
            # Agar sirf Image 1 button dikh raha hai
            elif p_img1:
                print(f"[INFO] Feedback required! 'Image 1 is better' found. Clicking it explicitly...", flush=True)
                img1_better.first.click()
                print(f"[OK] Clicked 'Image 1 is better'. Waiting 30-60 seconds...", flush=True)
                custom_random_wait(30, 60)
                # Ab loop ko age badhne do agle logic check ke liye
                
            # Agar sirf Image 2 button dikh raha hai
            elif p_img2:
                print(f"[INFO] Feedback required! 'Image 2 is better' found. Clicking it explicitly...", flush=True)
                img2_better.first.click()
                print(f"[OK] Clicked 'Image 2 is better'. Waiting 30-60 seconds...", flush=True)
                custom_random_wait(30, 60)
                # Ab loop ko age badhne do agle logic check ke liye
                
            # Agar teeno me se upar wale dono nahi mile toh 'Skip' check karo
            elif skip_btn.count() > 0:
                print(f"[INFO] Feedback required! 'Skip' button found. Clicking it explicitly...", flush=True)
                skip_btn.first.click()
                print(f"[OK] Clicked 'Skip'. Waiting 30-60 seconds...", flush=True)
                custom_random_wait(30, 60)
                # Ab loop ko age badhne do agle logic check ke liye
                
            # Agar teeno button nahi mile (Original logic for feedback which may be single choice sometimes)
            else:
                print(f"[INFO] No preference buttons found yet.", flush=True)
                # Agar share button dikhta hai toh check karo as per original logic fallback
                try:
                    locator = page.get_by_role('button', name='Share this image').first
                    if locator.is_visible():
                        share_button = locator
                        found_share = True
                        print("✅ 'Share this image' button located successfully!", flush=True)
                        break
                except Exception as loc_err:
                    print(f"[INFO] Share locator exception: {loc_err}", flush=True)
            
            # print(f"[WARNING] Share button not visible on attempt {attempt}. Retrying...", flush=True)
            # Retrying comment removed as per logic change...

        # --- END: MODIFIED LOGIC FOR PREFERENCE HANDLING ---

        if not found_share or not share_button:
            print("❌ Error: 'Share this image' button not found after 5 retries. Exiting program.", flush=True)
            if 'page' in locals() and page:
                try:
                    screenshot_path = "error_screenshot.png"
                    # Playwright full page screenshot
                    page.screenshot(path=screenshot_path, full_page=True)
                    print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                    
                    # --- ImgBB Upload Logic Starts Here ---
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

        # ========================================================
        # STRATEGY 1: DIRECT DOWNLOAD CHECK LOGIC (image.png)
        # ========================================================
        print("[STEP] Checking if direct 'Download' button is available on main page...", flush=True)
        direct_download_btn = page.get_by_role('button', name='Download').first
        
        if direct_download_btn.is_visible():
            print("✅ Direct 'Download' button found! Initiating direct download...", flush=True)
            try:
                with page.expect_download(timeout=60000) as download_info:
                    direct_download_btn.click()
                
                download = download_info.value
                local_filename = IMAGE_DIR / "image.png"
                download.save_as(local_filename)
                print(f"✅ Original resolution image downloaded directly (Saved to image directory): {local_filename}", flush=True)
                
                # Update status on success
                update_image_status_in_json(topic)
                
                print("[STEP] Performing final random wait before exit (30-60 seconds)...", flush=True)
                custom_random_wait(30, 60)
                print("[DONE] Kahaani Khatam! Direct download successfully processed.", flush=True)
                return  
                
            except Exception as direct_dl_err:
                print(f"[WARNING] Direct download triggered error, falling back to next strategies: {direct_dl_err}", flush=True)

        # ========================================================
        # STRATEGY 2: FALLBACK 1 - SAVE IMAGE FROM ROLE (image.png)
        # ========================================================
        print("[STEP] Executing Fallback 1: Searching for Generated image container...", flush=True)
        try:
            generated_image_btn = page.get_by_role('button', name=re.compile(r'Generated image:.*', re.IGNORECASE)).first
            if generated_image_btn.is_visible():
                print("✅ Generated image area located via regex. Extracting inner image element...", flush=True)
                
                img_element = generated_image_btn.locator('img').first
                img_src = img_element.get_attribute('src')
                
                if img_src:
                    local_filename = IMAGE_DIR / "image.png"
                    
                    if img_src.startswith('blob:'):
                        print("[INFO] Blob URL detected. Extracting image data directly from browser context...", flush=True)
                        base64_data = page.evaluate("""async (url) => {
                            const response = await fetch(url);
                            const blob = await response.blob();
                            return new Promise((resolve) => {
                                const reader = new FileReader();
                                reader.onloadend = () => resolve(reader.result.split(',')[1]);
                                reader.readAsDataURL(blob);
                            });
                        }""", img_src)
                        
                        with open(local_filename, "wb") as fh:
                            fh.write(base64.b64decode(base64_data))
                    else:
                        print(f"[INFO] Standard image URL detected. Downloading element source...", flush=True)
                        img_response = page.request.get(img_src)
                        with open(local_filename, "wb") as fh:
                            fh.write(img_response.body())
                            
                    print(f"✅ Original dimensions image successfully saved via Fallback 1: {local_filename}", flush=True)
                    
                    # Update status on success
                    update_image_status_in_json(topic)
                    
                    print("[STEP] Performing final random wait before exit (30-60 seconds)...", flush=True)
                    custom_random_wait(30, 60)
                    print("[DONE] Kahaani Khatam! Saved via Fallback 1 image extraction.", flush=True)
                    return
                else:
                    print("[WARNING] Image element found but 'src' attribute was empty.", flush=True)
        except Exception as fallback_one_err:
            print(f"[WARNING] Fallback 1 extraction method failed: {fallback_one_err}, falling back to share link flow.", flush=True)

        # ========================================================
        # STRATEGY 3: FALLBACK 2 - ORIGINAL POPUP & NEW TAB WORKFLOW
        # ========================================================
        print("[INFO] Moving forward with Fallback 2 workflow (New Tab / Share Link Method)...", flush=True)

        page.evaluate("() => navigator.clipboard.writeText('')")

        print("[STEP] Clicking 'Share this image' button...", flush=True)
        share_button.click()
        custom_random_wait(15, 30)

        # Pop-up Download Check
        try:
            popup_download_btn = page.get_by_role('button', name='Download').first
            if popup_download_btn.is_visible():
                print("✅ 'Download' button found inside the Copy Link pop-up! Initiating download...", flush=True)
                with page.expect_download(timeout=60000) as download_info:
                    popup_download_btn.click()
                
                download = download_info.value
                local_filename = IMAGE_DIR / "image.png"
                download.save_as(local_filename)
                print(f"✅ Original resolution image downloaded from pop-up successfully: {local_filename}", flush=True)
                
                # Update status on success
                update_image_status_in_json(topic)
                
                print("[STEP] Performing final random wait before exit (30-60 seconds)...", flush=True)
                custom_random_wait(30, 60)
                print("[DONE] Kahaani Khatam! Downloaded directly from pop-up.", flush=True)
                return
        except Exception as popup_dl_err:
            print(f"[INFO] Pop-up direct download failed or not found, continuing with link copy: {popup_dl_err}", flush=True)

        try:
            copy_link_btn = page.get_by_role('button', name='Copy link').first
            if copy_link_btn.is_visible():
                print("[INFO] 'Copy link' pop-up detected. Clicking it explicitly...", flush=True)
                copy_link_btn.click()
                time.sleep(2)
        except Exception as pop_err:
            print("[INFO] No pop-up button found, continuing with direct copy...", flush=True)

        public_shared_url = page.evaluate("() => navigator.clipboard.readText()")
        print(f"\n[COPIED URL] Shared Link Extracted: {public_shared_url}\n", flush=True)

        if public_shared_url and "chatgpt.com/s/" in public_shared_url:
            print("[STEP] Opening new tab for public shared link...", flush=True)
            shared_page = context.new_page()
            shared_page.goto(public_shared_url, wait_until="domcontentloaded")
            
            print("[STEP] Performing mandatory random wait on new tab (30-60 seconds)...", flush=True)
            custom_random_wait(30, 60)
            
            print("[STEP] Locating 'Save' button to trigger high-res download...", flush=True)
            
            try:
                save_btn = shared_page.get_by_role('button', name='Save').first.or_(shared_page.get_by_role('button', name='Save'))
                
                with shared_page.expect_download(timeout=60000) as download_info:
                    print("[STEP] Clicking 'Save' button...", flush=True)
                    save_btn.click()
                
                download = download_info.value
                local_filename = IMAGE_DIR / "image.png"
                download.save_as(local_filename)
                print(f"✅ Original resolution high quality image downloaded successfully: {local_filename}", flush=True)
                
                # Update status on success
                update_image_status_in_json(topic)
                
            except Exception as download_err:
                print(f"❌ Error during 'Save' button download processing: {download_err}", flush=True)
                if 'page' in locals() and page:
                    try:
                        screenshot_path = "error_screenshot.png"
                        # Playwright full page screenshot
                        page.screenshot(path=screenshot_path, full_page=True)
                        print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                        
                        # --- ImgBB Upload Logic Starts Here ---
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
            shared_page.close()
        else:
            print("[ERROR] Extracted clipboard content is not a valid ChatGPT shared page link URL.", flush=True)

        print("[STEP] Performing final random wait (30-60 seconds)...", flush=True)
        custom_random_wait(30, 60)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                # Playwright full page screenshot
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                # --- ImgBB Upload Logic Starts Here ---
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

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()