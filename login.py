import os
import sys
import time
import json
import base64
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page
from playwright_stealth import Stealth

# --- Configuration ---
HEADLESS = True
LOGIN_URL = "https://www.linkedin.com/login"
HOME_URL = "https://www.linkedin.com/feed/"
BASE_URL = "https://www.linkedin.com/"
CHALLENGE_PREFIX = "https://www.linkedin.com/checkpoint/challenge"
CHALLENGE_V2_PREFIX = "https://www.linkedin.com/checkpoint/challengesV2/"

COOKIE_FILE = os.path.join(os.path.dirname(__file__), "cookies.json")
ENCRYPTED_COOKIE_FILES = [
    os.path.join(os.path.dirname(__file__), "cookies.json.encrypted")
]
SESSION_COOKIE_NAME = "li_at"
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

# --- Cookie Helpers ---

def _read_session_cookie_from_disk() -> Tuple[Optional[dict], bool]:
    data = None
    try:
        target_path = next((p for p in ENCRYPTED_COOKIE_FILES if os.path.exists(p)), None)
        if target_path:
            load_dotenv()
            key = os.getenv("DECRYPT_KEY")
            if key:
                with open(target_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                s, n, ct = [base64.b64decode(payload.get(k, "")) for k in ["s", "n", "ct"]]
                if s and n and ct:
                    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=s, iterations=200_000)
                    k = kdf.derive(key.encode("utf-8"))
                    data = json.loads(AESGCM(k).decrypt(n, ct, None).decode("utf-8"))
    except Exception:
        data = None

    if data is None and os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception: pass

    cookie = data.get(SESSION_COOKIE_NAME) if data else None
    if not isinstance(cookie, dict) or "value" not in cookie:
        return None, False

    expiry = cookie.get("expires") or cookie.get("expiry")
    if isinstance(expiry, (int, float)) and float(expiry) <= time.time():
        return cookie, True
    
    if expiry:
        cookie["expires"] = float(expiry)
    return cookie, False

def _write_session_cookie_to_disk(cookie: dict) -> None:
    if any(os.path.exists(p) for p in ENCRYPTED_COOKIE_FILES): return
    try:
        answer = input("Create plaintext cookies.json for session reuse? [y/N]: ")
    except EOFError:
        answer = "n"
    if str(answer).strip().lower() in {"y", "yes"}:
        payload = {SESSION_COOKIE_NAME: cookie, "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

# --- Browser Logic ---

def _handle_challenge_if_present(page: Page):
    if CHALLENGE_PREFIX in page.url or CHALLENGE_V2_PREFIX in page.url:
        print("[Captcha] Human verification required. Please solve it in the browser.", flush=True)
        page.wait_for_url(lambda url: CHALLENGE_PREFIX not in url and CHALLENGE_V2_PREFIX not in url, timeout=0)
        print("[Captcha] Challenge completed.", flush=True)

def login_and_get_context(is_headless: bool = HEADLESS):
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    # Stealth remove karne ke liye upar ke 3 line hata ke neeche ke 1 line ko activate karna hoga
    # pw = sync_playwright().start()
    
    browser = pw.chromium.launch(headless=is_headless, args=["--start-maximized", "--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        no_viewport=True,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    # 1. Cookie Login
    cookie, expired = _read_session_cookie_from_disk()
    if cookie and not expired:
        page.goto(BASE_URL)
        clean_cookie = {
            "name": SESSION_COOKIE_NAME,
            "value": cookie["value"],
            "domain": cookie.get("domain", ".linkedin.com"),
            "path": cookie.get("path", "/"),
            "secure": True,
            "sameSite": "Lax"
        }
        if "expires" in cookie:
            clean_cookie["expires"] = float(cookie["expires"])

        context.add_cookies([clean_cookie])
        page.goto(HOME_URL)
        
        try:
            page.get_by_role("button", name="Me", exact=True).wait_for(state="visible", timeout=60000)
            print("[Cookie] Login successful.", flush=True)
            return pw, browser, context, page
        except:
            print("[Cookie] Session invalid. Proceeding to credentials.", flush=True)

    # 2. Credential Login
    load_dotenv()
    email, password = os.getenv("EMAIL"), os.getenv("PASSWORD")
    if not email or not password:
        raise RuntimeError("Missing EMAIL/PASSWORD in .env")

    page.goto(LOGIN_URL)
    page.get_by_role("textbox", name="Email or phone").fill(email)
    page.get_by_role("textbox", name="Password").fill(password)

    page.get_by_role("button", name="Sign in", exact=True).click()

    try:
        page.wait_for_load_state("domcontentloaded")
        _handle_challenge_if_present(page)
    except: pass

    try:
        me_button = page.get_by_role("button", name="Me", exact=True)
        me_button.wait_for(state="visible", timeout=15000)
        print("[Login] Success via credentials.", flush=True)
        
        all_cookies = context.cookies()
        session = next((c for c in all_cookies if c["name"] == SESSION_COOKIE_NAME), None)
        if session:
            _write_session_cookie_to_disk(session)
    except Exception as e:
        print(f"[Login] Could not verify login via 'Me' button: {e}", flush=True)

    return pw, browser, context, page

def main():
    pw_instance = None
    browser_instance = None
    try:
        pw_instance, browser_instance, context, page = login_and_get_context()
        print("\n--- Browser Active ---", flush=True)
        
        # State tracking
        is_active = [True]
        browser_instance.on("disconnected", lambda: is_active.clear())

        while is_active:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nExiting via Ctrl+C...", flush=True)
    except Exception as e:
        print(f"CRITICAL ERROR: {e}", flush=True)
        return 1
    finally:
        # SILENT CLEANUP: Surrounding with broad try-except to swallow "already closed" errors
        try:
            if browser_instance:
                browser_instance.close()
        except:
            pass
        
        try:
            if pw_instance:
                pw_instance.stop()
        except:
            pass
            
        print("Process Finished Cleanly.", flush=True)
    return 0

if __name__ == "__main__":
    sys.exit(main())