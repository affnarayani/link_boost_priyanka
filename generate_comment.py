import os
import sys
import json
import time
import base64
import random
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

COOKIES_DIR = Path("chatgpt_cookies")  # Path chatgpt_cookies set kiya gaya hai
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'chatgpt_cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

STATUS_FILE = Path("comment_status.json")
POST_DATA_FILE = Path("post_to_comment.json")


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


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # ============================================
    # STATUS CHECK: comment_status.json
    # ============================================
    if not STATUS_FILE.exists():
        print(f"[ERROR] {STATUS_FILE.name} file nahi mila. Exiting...", flush=True)
        sys.exit(0)
        
    try:
        with STATUS_FILE.open("r", encoding="utf-8") as f:
            status_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] {STATUS_FILE.name} parse nahi ho paya: {e}. Exiting...", flush=True)
        sys.exit(0)

    # Condition Check matching your exact json structure requirement
    if (status_data.get("post_to_comment_found") is True and 
        status_data.get("comment_generated") is False and 
        status_data.get("comment_posted") is False):
        print("[OK] Status check passed. Proceeding with comment generation...", flush=True)
    else:
        print(f"[INFO] Status match nahi hua {status_data}. Conditions failed, exiting...", flush=True)
        sys.exit(0)

    # ============================================
    # READ CONTENT FROM post_to_comment.json
    # ============================================
    if not POST_DATA_FILE.exists():
        print(f"[ERROR] {POST_DATA_FILE.name} nahi mili jahan se content read karna tha. Exiting...", flush=True)
        sys.exit(0)

    try:
        with POST_DATA_FILE.open("r", encoding="utf-8") as f:
            post_data = json.load(f)
        post_content = post_data.get("content", "").strip()
    except Exception as e:
        print(f"[ERROR] {POST_DATA_FILE.name} read/parse karne me dikkat aayi: {e}", flush=True)
        sys.exit(0)

    if not post_content:
        print(f"[ERROR] {POST_DATA_FILE.name} ke andar 'content' khali mila. Exiting...", flush=True)
        sys.exit(0)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP & LOGIN
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

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto("https://chatgpt.com/", wait_until="load")
        print("[OK] URL opened successfully (Logged In)", flush=True)

        custom_random_wait(30, 60)

        # CHECK LOGIN SUCCESS VIA USER PROFILE BUTTON
        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = page.get_by_role('button', name=list(map(lambda x: x.compile(r'.*Free, open'), [__import__('re')]))[0])
        
        if profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{profile_button.first.get_attribute('aria-label') or 'User Account'}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        # AUTOMATION FLOW
        print("[STEP] Locating chat textbox...", flush=True)
        textbox = page.get_by_role('textbox', name='Chat with ChatGPT')
        
        if textbox.count() == 0:
            textbox = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Ask anything')).first
            
        if textbox.count() == 0:
            textbox = page.locator('#prompt-textarea')

        if textbox.count() > 0:
            textbox.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya (All strategies failed).")
            
        custom_random_wait(15, 30)

        # =========================================================
        # LINKEDIN OPTIMIZED PROMPT 
        # =========================================================
        prompt = (
            f"IMPORTANT: Your entire response must be wrapped inside a single ```json code block. "
            f"Do not output any text, explanation, markdown, commentary, or notes before or after the code block.\n\n"

            f"Read the following LinkedIn post carefully:\n"
            f"\"\"\"\n{post_content}\n\"\"\"\n\n"

            f"Your task is to write a thoughtful, professional, and discussion-worthy LinkedIn comment.\n\n"

            f"PRIMARY OBJECTIVE:\n"
            f"Write the kind of comment that a knowledgeable industry peer would naturally leave after reading the post.\n"
            f"The comment should contribute something useful to the conversation rather than merely reacting to it.\n\n"

            f"THE GOAL IS NOT TO:\n"
            f"- Praise the author\n"
            f"- Compliment the post\n"
            f"- Summarize the post\n"
            f"- Repeat the author's main point\n"
            f"- Sound like a corporate chatbot\n"
            f"- Sound like an AI assistant\n\n"

            f"THE GOAL IS TO:\n"
            f"- Advance the discussion\n"
            f"- Add a practical real-world consideration\n"
            f"- Introduce a nuance, tradeoff, edge case, or second-order implication\n"
            f"- Surface an observation that practitioners in the field would recognize\n"
            f"- Provide a perspective that was not explicitly stated in the original post\n\n"

            f"SILENT ANALYSIS:\n"
            f"Before writing the comment:\n"
            f"1. Identify the core idea of the post.\n"
            f"2. Identify a practical implication, overlooked consideration, edge case, tradeoff, or downstream consequence.\n"
            f"3. Build the comment around that insight.\n"
            f"4. Prefer practitioner-level observations over theoretical commentary.\n\n"

            f"TONE:\n"
            f"Write like an experienced professional sharing one useful thought while scrolling LinkedIn.\n"
            f"Sound intelligent, observant, practical, conversational, and natural.\n"
            f"Write as a reaction, not as a mini-article.\n"
            f"The comment should feel spontaneous rather than carefully crafted.\n\n"

            f"DO NOT SOUND LIKE:\n"
            f"- A motivational influencer\n"
            f"- A life coach\n"
            f"- A marketer\n"
            f"- A thought-leadership cliché generator\n"
            f"- An academic paper\n\n"

            f"HUMANNESS RULE:\n"
            f"The comment should feel like it was written by a busy but sharp professional who had one genuinely useful thought while reading the post.\n"
            f"No artificial profundity.\n"
            f"No exaggerated wisdom.\n"
            f"No performative intelligence.\n\n"

            f"LENGTH:\n"
            f"Preferred range: 80-220 characters including spaces.\n"
            f"Be concise.\n\n"

            f"FORMAT RULES:\n"
            f"- Single continuous line\n"
            f"- No newline characters\n"
            f"- No emojis\n"
            f"- No hashtags\n"
            f"- No markdown\n"
            f"- No greetings\n"
            f"- No sign-offs\n\n"

            f"AVOID COMMON AI PHRASES:\n"
            f"- This resonates deeply\n"
            f"- Insightful share\n"
            f"- Thanks for sharing\n"
            f"- Spot on\n"
            f"- Couldn't agree more\n"
            f"- Well said\n"
            f"- Great reminder\n"
            f"- Valuable perspective\n"
            f"- You've captured a crucial point\n"
            f"- Or similar generic praise\n\n"

            f"QUALITY CHECK:\n"
            f"The comment should make an informed reader think:\n"
            f"'That's a good point—I hadn't considered that angle.'\n\n"

            f"OUTPUT FORMAT — STRICTLY INSIDE A SINGLE JSON CODE BLOCK:\n"
            f"{{\n"
            f'  "comment": "Your direct single-line LinkedIn comment here"\n'
            f"}}\n"
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.first.fill(prompt)
        custom_random_wait(15, 30)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = page.get_by_test_id('send-button')
        send_button.click()
        
        custom_random_wait(30, 60)

        # STABLE Live Stream Check
        print("[STEP] Waiting for generated JSON code block to complete writing...", flush=True)
        code_block_locator = page.locator('#code-block-viewer pre')
        
        json_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)
            
            if code_block_locator.count() > 0:
                print("[OK] Code block visible, parsing text...", flush=True)
                
                last_length = 0
                max_check_cycles = 15
                
                for cycle in range(max_check_cycles):
                    time.sleep(15)
                    
                    current_text = code_block_locator.first.inner_text().strip()
                    current_length = len(current_text)
                    
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)
                    
                    if current_length > 0 and current_length == last_length:
                        if current_text.endswith("}"):
                            json_content = current_text
                            print("[OK] Content generation is fully finished.", flush=True)
                            break
                        else:
                            print("[WARNING] Generation paused but closing '}' is missing.", flush=True)
                        
                    last_length = current_length
                
                if json_content:
                    break
            
            if attempt < 5:
                print(f"[WARNING] Code block not fully compiled yet. Waiting...", flush=True)
                custom_random_wait(30, 60)
            else:
                print("❌ Max retries reached. Streaming failed. Exiting...", flush=True)
                try: browser.close()
                except: pass
                sys.exit(1)

        # JSON processing, validation and Updates
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]
                
                parsed_json = json.loads(json_content.strip())
                generated_comment_text = parsed_json.get("comment", "").strip()

                # Clean stray newlines
                generated_comment_text = generated_comment_text.replace("\n", " ").replace("\r", "")
                
                # =========================================================
                # APPEND COMMENT TO post_to_comment.json
                # =========================================================
                print(f"[STEP] Appending comment key to {POST_DATA_FILE.name}...", flush=True)
                post_data["comment"] = generated_comment_text
                
                with POST_DATA_FILE.open("w", encoding="utf-8") as f:
                    json.dump(post_data, f, indent=4, ensure_ascii=False)
                print(f"[OK] {POST_DATA_FILE.name} updated successfully with comment data.", flush=True)

                # =========================================================
                # UPDATE STATUS FILE ON SUCCESS
                # =========================================================
                print(f"[STEP] Updating {STATUS_FILE.name}...", flush=True)
                status_data["comment_generated"] = True
                
                with STATUS_FILE.open("w", encoding="utf-8") as f:
                    json.dump(status_data, f, indent=4, ensure_ascii=False)
                print(f"[OK] {STATUS_FILE.name} updated: comment_generated set to true.", flush=True)
                
            except json.JSONDecodeError as je:
                print(f"[ERROR] Content JSON parse error: {je}. Exiting...", flush=True)
                try: browser.close()
                except: pass
                sys.exit(1)
        else:
            print("[ERROR] No data fetched from ChatGPT. Exiting...", flush=True)
            try: browser.close()
            except: pass
            sys.exit(1)

        print("[STEP] Performing final wait before closure...", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        if 'page' in locals() and page:
            try:
                page.screenshot(path="error_screenshot.png", full_page=True)
                print("[OK] Error screenshot captured.", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Screenshot error: {screenshot_err}", flush=True)
        if browser:
            try: browser.close()
            except: pass
        sys.exit(1)

    finally:
        if browser:
            try: browser.close()
            except: pass

        try: pw_cm.__exit__(None, None, None)
        except: pass

        print("[DONE] Script finished cleanly", flush=True)


if __name__ == "__main__":
    run()