"""LinkedIn session bootstrap.

Establishes an authenticated Playwright context, preferring a stored ``li_at``
cookie and falling back to email/password credentials. Every locator is
resolved through the shared registry so the Gemini Vision fallback applies to
the login flow as well.
"""

from __future__ import annotations

import sys
import time

from playwright.sync_api import Page, sync_playwright
from playwright_stealth import Stealth

from common import config
from common.cookies import (
    _read_session_cookie_from_disk,
    _write_session_cookie_to_disk,
)
from common.interact import LINKEDIN_UI, LocatorResolutionError


def _handle_challenge_if_present(page: Page) -> None:
    """Block until a LinkedIn checkpoint challenge is solved by a human."""
    if config.CHALLENGE_PREFIX in page.url or config.CHALLENGE_V2_PREFIX in page.url:
        print("[CAPTCHA] Human verification required. Please solve it in the browser.", flush=True)
        page.wait_for_url(
            lambda url: config.CHALLENGE_PREFIX not in url and config.CHALLENGE_V2_PREFIX not in url,
            timeout=0,
        )
        print("[CAPTCHA] Challenge completed.", flush=True)


def _verify_login(page: Page, timeout_ms: int) -> bool:
    """Return True when the account menu becomes visible, i.e. the session is valid."""
    try:
        LINKEDIN_UI.wait_visible(
            page,
            "me_account_button",
            timeout_ms=timeout_ms,
            required=True,
        )
        return True
    except LocatorResolutionError as exc:
        print(f"[LOGIN] Could not verify the session: {exc}", flush=True)
        return False


def login_and_get_context(is_headless: bool = config.HEADLESS):
    """Return ``(playwright, browser, context, page)`` for an authenticated session."""
    stealth = Stealth()
    playwright_cm = stealth.use_sync(sync_playwright())
    playwright_instance = playwright_cm.__enter__()

    browser = playwright_instance.chromium.launch(
        headless=is_headless,
        args=config.BROWSER_LAUNCH_ARGS,
    )
    context = browser.new_context(no_viewport=config.NO_VIEWPORT, user_agent=config.USER_AGENT)
    page = context.new_page()

    # 1. Cookie-based session reuse
    cookie, expired = _read_session_cookie_from_disk()
    if cookie and not expired:
        page.goto(config.LINKEDIN_BASE_URL)
        clean_cookie = {
            "name": config.SESSION_COOKIE_NAME,
            "value": cookie["value"],
            "domain": cookie.get("domain", config.LINKEDIN_COOKIE_DOMAIN),
            "path": cookie.get("path", "/"),
            "secure": True,
            "sameSite": "Lax",
        }
        if "expires" in cookie:
            clean_cookie["expires"] = float(cookie["expires"])

        context.add_cookies([clean_cookie])
        page.goto(config.LINKEDIN_HOME_URL)

        if _verify_login(page, config.LOGIN_DOM_TIMEOUT_MS):
            print("[COOKIE] Login successful.", flush=True)
            return playwright_instance, browser, context, page

        print("[COOKIE] Session invalid. Falling back to credentials.", flush=True)

    # 2. Credential login
    email = config.env_str("EMAIL")
    password = config.env_str("PASSWORD")
    if not email or not password:
        raise RuntimeError("Missing EMAIL or PASSWORD in the environment/.env file")

    page.goto(config.LINKEDIN_LOGIN_URL)
    LINKEDIN_UI.fill(page, "login_email_input", email)
    LINKEDIN_UI.fill(page, "login_password_input", password)
    LINKEDIN_UI.click(page, "login_sign_in_button")

    try:
        page.wait_for_load_state("domcontentloaded")
        _handle_challenge_if_present(page)
    except Exception:
        pass

    try:
        LINKEDIN_UI.wait_visible(
            page,
            "me_account_button",
            timeout_ms=config.LOGIN_DOM_TIMEOUT_MS,
            required=True,
        )
        print("[LOGIN] Success via credentials.", flush=True)

        all_cookies = context.cookies()
        session = next(
            (item for item in all_cookies if item["name"] == config.SESSION_COOKIE_NAME),
            None,
        )
        if session:
            _write_session_cookie_to_disk(session)
    except LocatorResolutionError as exc:
        print(f"[LOGIN] Could not verify login via the account menu: {exc}", flush=True)

    return playwright_instance, browser, context, page


def main() -> int:
    playwright_instance = None
    browser_instance = None
    try:
        playwright_instance, browser_instance, _context, _page = login_and_get_context()
        print("\n--- Browser Active ---", flush=True)

        is_active = [True]
        browser_instance.on("disconnected", lambda: is_active.clear())

        while is_active:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nExiting via Ctrl+C...", flush=True)
    except Exception as exc:
        print(f"CRITICAL ERROR: {exc}", flush=True)
        return 1
    finally:
        try:
            if browser_instance:
                browser_instance.close()
        except Exception:
            pass

        try:
            if playwright_instance:
                playwright_instance.stop()
        except Exception:
            pass

        print("Process finished cleanly.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())