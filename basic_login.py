"""Minimal LinkedIn login check.

Loads an encrypted LinkedIn cookie file, opens the feed, and verifies that the
account menu is present. Useful as a quick smoke test for the saved session.
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common import config
from common.browser import capture_and_upload_error, custom_random_wait
from common.cookies import load_encrypted_cookies
from common.interact import LINKEDIN_UI, LocatorResolutionError


def run(decrypt_key: str) -> None:
    print("[START] Script started", flush=True)

    cookies = load_encrypted_cookies(config.LINKEDIN_ENCRYPTED_COOKIES_FILE, decrypt_key)

    stealth = Stealth()
    playwright_cm = stealth.use_sync(sync_playwright())
    playwright_instance = playwright_cm.__enter__()

    browser = None
    page = None
    try:
        browser = playwright_instance.chromium.launch(
            headless=config.HEADLESS,
            args=config.BROWSER_LAUNCH_ARGS,
        )

        context = browser.new_context(no_viewport=True, user_agent=config.USER_AGENT)
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        context.add_cookies(cookies)

        page = context.new_page()

        print(f"[STEP] Opening LinkedIn feed: {config.LINKEDIN_HOME_URL}", flush=True)
        page.goto(config.LINKEDIN_HOME_URL, wait_until="load")

        print("[STEP] Verifying login status via the account menu...", flush=True)
        try:
            LINKEDIN_UI.wait_visible(
                page,
                "me_account_button",
                timeout_ms=config.LOGIN_DOM_TIMEOUT_MS,
                required=True,
            )
            print("[SUCCESS] Login succeeded: account menu detected.", flush=True)
        except LocatorResolutionError as exc:
            print(f"[ERROR] Could not verify the session: {exc}", flush=True)
            capture_and_upload_error(page)
            sys.exit(1)

        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as exc:
        print(f"[ERROR] Script execution failed: {exc}", flush=True)
        capture_and_upload_error(page)
        sys.exit(1)

    finally:
        if browser:
            try:
                browser.close()
            except Exception:
                pass

        try:
            playwright_cm.__exit__(None, None, None)
        except Exception:
            pass

        print("[DONE] Script execution environment torn down cleanly.", flush=True)


if __name__ == "__main__":
    key = config.env_str("DECRYPT_KEY", required=True)
    run(key)