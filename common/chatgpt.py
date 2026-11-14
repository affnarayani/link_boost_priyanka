"""ChatGPT session bootstrap shared by the content, comment, and image scripts.

Picks a random encrypted cookie file, decrypts it, and returns a ready-to-use
Playwright context on chatgpt.com.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional, Tuple

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from common import config
from common.cookies import load_encrypted_cookies
from common.interact import CHATGPT_UI, LocatorResolutionError


def pick_cookie_file() -> Path:
    """Choose a random ``*.encrypted`` ChatGPT cookie file."""
    candidates = sorted(config.CHATGPT_COOKIES_DIR.glob("*.encrypted"))
    if not candidates:
        raise RuntimeError(
            f"No .encrypted cookie files found in '{config.CHATGPT_COOKIES_DIR.name}/'"
        )

    chosen = random.choice(candidates)
    print(f"[OK] Randomly selected cookie file: {chosen.name}", flush=True)
    return chosen


def launch_chatgpt_session(
    headless: Optional[bool] = None,
    decrypt_key: Optional[str] = None,
    open_page: bool = True,
) -> Tuple[object, object, object, object, object]:
    """Return ``(playwright_cm, playwright, browser, context, page)``.

    ``playwright_cm`` is the context manager returned by ``Stealth.use_sync``;
    callers must invoke ``playwright_cm.__exit__(None, None, None)`` on teardown.
    """
    key = decrypt_key or config.env_str("DECRYPT_KEY", required=True)
    cookies = load_encrypted_cookies(pick_cookie_file(), key)

    stealth = Stealth()
    playwright_cm = stealth.use_sync(sync_playwright())
    playwright_instance = playwright_cm.__enter__()

    browser = playwright_instance.chromium.launch(
        headless=config.HEADLESS if headless is None else headless,
        args=config.BROWSER_LAUNCH_ARGS,
    )
    context = browser.new_context(no_viewport=config.NO_VIEWPORT, user_agent=config.USER_AGENT)
    context.grant_permissions(["clipboard-read", "clipboard-write"])

    print("[STEP] Adding cookies to the browser context...", flush=True)
    context.add_cookies(cookies)

    page = context.new_page()
    print("[OK] Cookies added successfully", flush=True)

    if open_page:
        print(f"[STEP] Opening ChatGPT: {config.CHATGPT_URL}", flush=True)
        page.goto(config.CHATGPT_URL, wait_until="load")
        print("[OK] URL opened successfully (logged in)", flush=True)

    return playwright_cm, playwright_instance, browser, context, page


def verify_chatgpt_session(page, timeout_ms: Optional[int] = None) -> bool:
    """Return True when the ChatGPT profile menu button is detectable."""
    print("[STEP] Checking login success via the profile button...", flush=True)
    try:
        CHATGPT_UI.wait_visible(
            page,
            "chatgpt_profile_menu_button",
            timeout_ms=timeout_ms or 15_000,
            required=True,
        )
    except LocatorResolutionError:
        print("[WARNING] Profile button not detected; proceeding with caution...", flush=True)
        return False

    label = "User Account"
    try:
        label = CHATGPT_UI.locator(page, "chatgpt_profile_menu_button").first.get_attribute("aria-label") or label
    except Exception:
        pass

    print(f"[OK] LOGIN SUCCESS: profile button found -> '{label}'", flush=True)
    return True


def teardown(playwright_cm, browser) -> None:
    """Close the browser and stop Playwright, swallowing teardown errors."""
    if browser:
        try:
            browser.close()
        except Exception:
            pass
    try:
        playwright_cm.__exit__(None, None, None)
    except Exception:
        pass
    print("[DONE] Script finished", flush=True)