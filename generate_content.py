import os
import sys
import json
import time
import base64
import random
import requests
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

COOKIES_DIR = Path("chatgpt_cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

PBKDF2_ITERATIONS = 200_000

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
# FILE PARSERS & VALIDATORS
# =========================
def can_run_script() -> bool:
    print("[STEP] Checking last post status in priyanka_linkedin_topics.json...", flush=True)
    topics_file = Path("priyanka_linkedin_topics.json")
    if not topics_file.exists():
        print("[WARNING] 'priyanka_linkedin_topics.json' nahi mili. Proceeding by default...", flush=True)
        return True
    
    try:
        with topics_file.open("r", encoding="utf-8") as f:
            topics = json.load(f)
    except Exception as e:
        print(f"[WARNING] Topics file read karne me dikkat: {e}. Proceeding...", flush=True)
        return True

    if not topics or not isinstance(topics, list):
        print("[OK] Topics list khali hai. Proceeding...", flush=True)
        return True

    # Sabse aakhri processed item dhoondhna jisme kaam shuru hua ho
    last_processed_item = None
    for item in topics:
        if isinstance(item, dict) and "content_generated" in item:
            last_processed_item = item

    # Agar koi bhi post processed nahi hai, toh yeh pehla post hai
    if last_processed_item is None:
        print("[OK] Pehla post detected (No previous history found). Proceeding...", flush=True)
        return True

    # Teeno key-value pairs ka True hona mandatory hai
    cg = last_processed_item.get("content_generated") is True
    ig = last_processed_item.get("image_generated") is True
    p = last_processed_item.get("posted") is True

    if cg and ig and p:
        print(f"[OK] Last post '{last_processed_item.get('topic')}' ke teeno keys True hain. Script aage badh rahi hai.", flush=True)
        return True
    else:
        print(f"[INFO] Script halted! Last post '{last_processed_item.get('topic')}' complete nahi hai: content_generated={cg}, image_generated={ig}, posted={p}.", flush=True)
        return False


def get_next_topic_from_json() -> str:
    print("[STEP] Reading priyanka_linkedin_topics.json...", flush=True)
    topics_file = Path("priyanka_linkedin_topics.json")
    if not topics_file.exists():
        raise FileNotFoundError("❌ 'priyanka_linkedin_topics.json' file nahi mila.")
    
    with topics_file.open("r", encoding="utf-8") as f:
        topics = json.load(f)
    
    if not topics:
        raise ValueError("❌ 'priyanka_linkedin_topics.json' khali hai.")
    
    # Unprocessed topic ko sequential order (index 0) se select karna
    for item in topics:
        if "content_generated" not in item:
            selected_topic = item["topic"]
            print(f"[OK] Selected next topic: '{selected_topic}'", flush=True)
            return selected_topic
            
    raise ValueError("❌ 'priyanka_linkedin_topics.json' mein koi naya unprocessed topic nahi mila.")


def update_topic_status_in_json(topic_text: str):
    print("[STEP] Updating topic status in priyanka_linkedin_topics.json...", flush=True)
    topics_file = Path("priyanka_linkedin_topics.json")
    if not topics_file.exists():
        return
        
    with topics_file.open("r", encoding="utf-8") as f:
        topics = json.load(f)
        
    for item in topics:
        if item.get("topic") == topic_text:
            item["content_generated"] = True
            item["image_generated"] = False
            item["posted"] = False
            break
            
    with topics_file.open("w", encoding="utf-8") as f:
        json.dump(topics, f, indent=4, ensure_ascii=False)
    print("[OK] Topic status successfully updated (content_generated=True) in priyanka_linkedin_topics.json", flush=True)

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

    # Last post check condition validation
    if not can_run_script():
        print("[INFO] Conditions match nahi hui. Exiting script gracefully...", flush=True)
        sys.exit(0)

    # Har baar new run par post.json ke content ko clear kar dena (sirf status pass hone par)
    post_file = Path("post.json")
    with post_file.open("w", encoding="utf-8") as f:
        f.write("")
    print("[OK] 'post.json' cleared/initialized at the start of the run.", flush=True)

    # Get next unprocessed topic
    try:
        topic = get_next_topic_from_json()
    except Exception as e:
        print(f"[ERROR] Configurations files read karne me dikkat aayi: {e}", flush=True)
        sys.exit(1)

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
        page.goto(
            "https://chatgpt.com/",
            wait_until="load"
        )
        print("[OK] URL opened successfully (Logged In)", flush=True)

        # 30 to 60 seconds random wait after page load
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
        
        # Fallback Strategy for Textbox Locators
        textbox = page.get_by_role('textbox', name='Chat with ChatGPT')
        
        if textbox.count() == 0:
            print("[INFO] Fallback 1: Searching for 'Ask anything' paragraph inside textbox context...", flush=True)
            textbox = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Ask anything')).first
            
        if textbox.count() == 0:
            print("[INFO] Fallback 2: Searching via CSS Selector '#prompt-textarea'...", flush=True)
            textbox = page.locator('#prompt-textarea')

        # Trigger action if found
        if textbox.count() > 0:
            textbox.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya (All strategies failed).")
            
        custom_random_wait(15, 30)

        # Smart prompt engineering optimized for LinkedIn Post format (Clean & High Engagement)
        prompt = (
            f"IMPORTANT: Your entire response must be wrapped inside a single ```json code block. "
            f"STRICTLY: Do not output any text, explanation, markdown, commentary, or notes before or after the JSON code block.\n\n"

            f"You are an elite LinkedIn ghostwriter who specializes in creating high-performing educational posts that generate engagement, saves, shares, and comments.\n\n"

            f"Write a LinkedIn post on the topic: '{topic}'.\n\n"

            f"PRIMARY OBJECTIVE:\n"
            f"Create a post that teaches one valuable concept while keeping readers engaged until the end.\n"
            f"The post should feel naturally written by a knowledgeable professional, not by an AI, textbook, lawyer, journalist, or academic writer.\n\n"

            f"TARGET AUDIENCE:\n"
            f"Professionals, founders, students, working adults, and general readers with little or no prior knowledge of the topic.\n\n"

            f"LINKEDIN WRITING RULES:\n"
            f"- Target length: 150-220 words.\n"
            f"- Use short paragraphs.\n"
            f"- Optimize for mobile reading.\n"
            f"- Use natural line breaks frequently.\n"
            f"- Write in a conversational yet authoritative tone.\n"
            f"- Avoid jargon whenever possible.\n"
            f"- If technical terms are necessary, explain them simply.\n"
            f"- Avoid sounding like an article, textbook, legal document, or lecture.\n"
            f"- Avoid generic filler.\n"
            f"- Avoid repeating the same point.\n"
            f"- Avoid phrases such as:\n"
            f"  'It is important to understand'\n"
            f"  'Understanding this helps'\n"
            f"  'In conclusion'\n"
            f"  'Let's discuss'\n"
            f"  'Here's why'\n\n"

            f"CONTENT REQUIREMENTS:\n"
            f"- Start with a strong curiosity-driven hook.\n"
            f"- The first 2 lines must make readers want to continue.\n"
            f"- Introduce a common misconception, surprising fact, or misunderstood aspect of the topic.\n"
            f"- Explain the concept in a simple and practical way.\n"
            f"- Include at least one insight that makes readers think:\n"
            f"  'I didn't know that.'\n"
            f"- Focus on clarity over completeness.\n"
            f"- Readers should learn one useful thing in under 30 seconds.\n\n"

            f"POST STRUCTURE:\n"
            f"1. Hook\n"
            f"2. Misconception or surprising insight\n"
            f"3. Clear explanation\n"
            f"4. Practical takeaway\n"
            f"5. Comment-driving question directly related to the topic\n\n"

            f"OUTPUT FORMAT:\n"
            f"Return ONLY this JSON structure:\n\n"

            f"{{\n"
            f'  "title": "{topic}",\n'
            f'  "p1": "Hook and curiosity-driven opening...",\n'
            f'  "p2": "Misconception or surprising insight...",\n'
            f'  "p3": "Explanation and practical takeaway...",\n'
            f'  "conclusion": "Topic-related question designed to encourage comments...",\n'
            f'  "keywords": ["hashtag1", "hashtag2", "hashtag3", "hashtag4", "hashtag5"]\n'
            f"}}\n\n"

            f"STRICT RULES:\n"
            f"- 'title' must exactly match '{topic}'.\n"
            f"- 'keywords' must contain exactly 5 relevant hashtags without the '#' symbol.\n"
            f"- Do not use markdown headings.\n"
            f"- Do not use numbered lists.\n"
            f"- Do not use bullet points.\n"
            f"- Do not include emojis unless they genuinely improve readability.\n"
            f"- Ensure the conclusion question is directly related to the main insight of the post.\n"
            f"- Return valid JSON only inside a single code block.\n"
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.first.fill(prompt)
        custom_random_wait(15, 30)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = page.get_by_test_id('send-button')
        send_button.click()
        
        # Initial wait for generation to start properly
        custom_random_wait(30, 60)

        # STABLE 15-SECOND POLLING LIVE STREAM CHECK
        print("[STEP] Waiting for generated JSON code block to complete writing (15s checks)...", flush=True)
        code_block_locator = page.locator('#code-block-viewer pre')
        
        json_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)
            
            if code_block_locator.count() > 0:
                print("[OK] Code block visible, parsing live text size variations...", flush=True)
                
                last_length = 0
                max_check_cycles = 15  # 15 cycles * 15 seconds = ~3.7 minutes max wait per attempt
                
                for cycle in range(max_check_cycles):
                    time.sleep(15)
                    
                    current_text = code_block_locator.first.inner_text().strip()
                    current_length = len(current_text)
                    
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)
                    
                    if current_length > 0 and current_length == last_length:
                        if current_text.endswith("}"):
                            json_content = current_text
                            print("[OK] Content generation is fully finished and finalized.", flush=True)
                            break
                        else:
                            print("[WARNING] Text generation paused but JSON bracket '}' is missing. Waiting further...", flush=True)
                        
                    last_length = current_length
                
                if json_content:
                    break
            
            if attempt < 5:
                print(f"[WARNING] Code block completely write nahi hua ya block mila nahi. Next retry window...", flush=True)
                custom_random_wait(30, 60)
            else:
                print("❌ Max retries reached. Streaming complete nahi ho payi. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)

        # JSON parsing, validation and Topic Update in JSON file
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]
                
                parsed_json = json.loads(json_content.strip())
                
                # Title sync check
                parsed_json["title"] = topic
                
                print("[STEP] Saving to post.json...", flush=True)
                with post_file.open("w", encoding="utf-8") as f:
                    json.dump(parsed_json, f, indent=4, ensure_ascii=False)
                print("[OK] LinkedIn post successfully saved to post.json", flush=True)
                
                # Status update triggers on success verification
                update_topic_status_in_json(topic)
                
            except json.JSONDecodeError as je:
                print(f"[ERROR] Content JSON parse karne me fail hua: {je}. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)
        else:
            print("[ERROR] Save skip kiya gaya kyunki koi data fetch nahi hua. Exiting script...", flush=True)
            try:
                browser.close()
            except:
                pass
            sys.exit(1)

        print("[STEP] Performing random wait before normal browser closure...", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                upload_to_tmpfiles(screenshot_path)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
        if browser:
            try:
                browser.close()
            except:
                pass
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