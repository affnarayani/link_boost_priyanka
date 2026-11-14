"""Post a comment on the target LinkedIn post and register the like.

Handles the three non-commentable post states (unavailable, comments disabled,
group-only) as successful outcomes, then drives the comment box, submits the
comment via keyboard, and likes the post.
"""

from __future__ import annotations

import sys
import time

from playwright.sync_api import Page

from login import login_and_get_context

from common import config
from common.browser import (
    capture_and_upload_error,
    custom_random_wait,
    read_json,
    reset_comment_status,
    write_json,
)
from common.interact import LINKEDIN_UI, LocatorResolutionError


def slow_scroll_to_bottom(page: Page, step_pixels: int = 250, delay_sec: float = 0.4) -> None:
    """Scroll gradually so lazily-rendered post elements load before interacting.

    The feed lives inside ``#workspace``, which captures the scroll. Give that
    container focus first, otherwise ``mouse.wheel`` scrolls the wrong element
    and the page never moves.
    """
    print("[STEP] Focusing the feed workspace before scrolling...", flush=True)
    workspace = LINKEDIN_UI.locator(page, "feed_workspace")
    if workspace is not None:
        try:
            workspace.first.focus()
        except Exception as exc:
            print(f"[WARNING] Could not focus #workspace directly: {exc}", flush=True)
            try:
                page.eval_on_selector("#workspace", "el => el.focus()")
            except Exception:
                page.locator("#workspace").first.click()

    print("[STEP] Scrolling down gradually to load post elements...", flush=True)

    while True:
        current_scroll = page.evaluate("window.scrollY")
        total_height = page.evaluate("document.body.scrollHeight - window.innerHeight")

        page.mouse.wheel(0, step_pixels)
        time.sleep(delay_sec)

        new_scroll = page.evaluate("window.scrollY")
        if new_scroll == current_scroll or new_scroll >= total_height:
            print("[OK] Page fully scrolled.", flush=True)
            break


def focus_active_editor(page: Page) -> None:
    """Force focus and caret placement into the active contenteditable element."""
    page.evaluate(
        """
        () => {
            const el = document.activeElement;
            if (!el) return;
            el.focus();
            if (el.getAttribute('contenteditable') === 'true') {
                const range = document.createRange();
                const selection = window.getSelection();
                range.selectNodeContents(el);
                range.collapse(false);
                selection.removeAllRanges();
                selection.addRange(range);
            }
        }
        """
    )


def is_comment_button_disabled(page: Page) -> bool:
    """Return True when the Comment button is rendered but cannot be interacted with.

    LinkedIn keeps the social-actions bar visible with a disabled Comment button
    when commenting is turned off. That state is more reliable than the
    "Comments have been turned off" notice, which is not always rendered.
    """
    locator = LINKEDIN_UI.locator(page, "post_comment_button")
    if locator is None:
        return False

    try:
        if locator.count() == 0:
            return False

        target = locator.first
        if not target.is_visible():
            return False

        if not target.is_enabled():
            return True

        aria_disabled = target.get_attribute("aria-disabled")
        if aria_disabled is not None and aria_disabled.strip().lower() == "true":
            return True

        pointer_events = target.evaluate(
            "el => window.getComputedStyle(el).pointerEvents"
        )
        if str(pointer_events).strip().lower() == "none":
            return True
    except Exception:
        return False

    return False


def focus_and_click_comment_box(page: Page, timeout_ms: int = 15_000) -> bool:
    """Locate, click, and focus the comment box via the locator registry."""
    print("[DOM SEARCH] Locating the comment box...", flush=True)
    if LINKEDIN_UI.click(page, "comment_box_editor", timeout_ms=timeout_ms, required=False):
        custom_random_wait(0.5, 1.0)
        focus_active_editor(page)
        print("[SUCCESS] Comment box located and focused.", flush=True)
        return True

    print("[ERROR] Comment box could not be located via DOM or vision.", flush=True)
    return False


def click_like_button(page: Page, timeout_ms: int = 10_000) -> bool:
    """Like the current post via the locator registry."""
    print("[DOM SEARCH] Locating the Like button...", flush=True)
    if LINKEDIN_UI.click(page, "post_like_button", timeout_ms=timeout_ms, required=False):
        print("[SUCCESS] Post liked.", flush=True)
        return True

    print("[WARNING] Could not locate or click the Like button.", flush=True)
    return False


def _mark_post_handled(target_url: str, status_data: dict, label: str) -> None:
    """Record a non-commentable post as handled and reset the pipeline flags."""
    print(f"[INFO] {label}. Treating as SUCCESS.", flush=True)

    commented = read_json(config.COMMENTED_FILE, default=[]) or []
    if not isinstance(commented, list):
        commented = []
    if target_url not in commented:
        commented.append(target_url)
        write_json(config.COMMENTED_FILE, commented)

    status_data["comment_posted"] = True
    write_json(config.STATUS_FILE, status_data)

    custom_random_wait(5, 10)
    reset_comment_status()
    print("[SUCCESS] Exiting safely with code 0.", flush=True)


def run() -> None:
    print("[START] Script started", flush=True)

    # 1. Pipeline gate
    if not config.STATUS_FILE.exists():
        print(f"[ERROR] {config.STATUS_FILE.name} not found. Execution stopped.", flush=True)
        sys.exit(0)

    try:
        status_data = read_json(config.STATUS_FILE, default={}) or {}
    except Exception as exc:
        print(f"[ERROR] Could not parse {config.STATUS_FILE.name}: {exc}", flush=True)
        sys.exit(0)

    if not (
        status_data.get("post_to_comment_found") is True
        and status_data.get("comment_generated") is True
        and status_data.get("comment_posted") is False
    ):
        print("[INFO] Status requirements not met. Exiting...", flush=True)
        sys.exit(0)

    # 2. Read the comment payload
    if not config.POST_DATA_FILE.exists():
        print(f"[ERROR] {config.POST_DATA_FILE.name} not found!", flush=True)
        sys.exit(0)

    try:
        post_data = read_json(config.POST_DATA_FILE, default={}) or {}
        target_url = str(post_data.get("url", "")).strip()
        comment_text = str(post_data.get("comment", "")).strip()
    except Exception as exc:
        print(f"[ERROR] Could not read {config.POST_DATA_FILE.name}: {exc}", flush=True)
        sys.exit(0)

    # 3. Session
    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        _playwright, browser, _context, page = login_and_get_context(is_headless=config.HEADLESS)
    except Exception as exc:
        print(f"[ERROR] Login session failed: {exc}", flush=True)
        sys.exit(1)

    try:
        print(f"[STEP] Navigating to the target post: {target_url}", flush=True)
        page.goto(target_url, wait_until="domcontentloaded", timeout=config.ACTION_TIMEOUT_MS)
        custom_random_wait(4, 8)

        # 4. Non-commentable post states
        if LINKEDIN_UI.is_visible(page, "post_unavailable_link"):
            _mark_post_handled(target_url, status_data, "'This post cannot be displayed' found")
            return

        if LINKEDIN_UI.is_visible(page, "comments_turned_off_text"):
            _mark_post_handled(target_url, status_data, "'Comments have been turned off' found")
            return

        if LINKEDIN_UI.is_visible(page, "group_members_only_text"):
            _mark_post_handled(target_url, status_data, "'Only group members can' restriction found")
            return

        if is_comment_button_disabled(page):
            _mark_post_handled(target_url, status_data, "'Comment' button is disabled")
            return

        # 5. Locate the comment box
        slow_scroll_to_bottom(page, step_pixels=250, delay_sec=0.4)
        custom_random_wait(2, 3)

        # The social-actions bar only renders reliably after scrolling, so the
        # disabled-Comment check runs here as well as above.
        if is_comment_button_disabled(page):
            _mark_post_handled(target_url, status_data, "'Comment' button is disabled")
            return

        if not focus_and_click_comment_box(page, timeout_ms=15_000):
            raise LocatorResolutionError("Could not locate or focus the comment box.")

        custom_random_wait(1, 2)

        # 6. Type the comment
        print("[STEP] Typing comment...", flush=True)
        page.keyboard.type(comment_text, delay=70)
        focus_active_editor(page)

        # Fallback for editors that swallow keyboard input
        page.evaluate(
            """
            (text) => {
                const active = document.activeElement;
                if (active && (active.isContentEditable || active.getAttribute('contenteditable') === 'true')) {
                    if (active.innerText.trim() === '') {
                        document.execCommand('insertText', false, text);
                        active.dispatchEvent(new Event('input', { bubbles: true }));
                    }
                }
            }
            """,
            comment_text,
        )

        custom_random_wait(1, 2)

        # 7. Submit via keyboard (three tabs then Enter)
        print("[STEP] Submitting the comment using the keyboard sequence (3 TABs + ENTER)...", flush=True)
        for index in range(1, 4):
            page.keyboard.press("Tab")
            print(f"[KEYBOARD] Pressed TAB ({index}/3)", flush=True)
            custom_random_wait(1, 2)

        page.keyboard.press("Enter")
        print("[KEYBOARD] Pressed ENTER to post the comment.", flush=True)
        custom_random_wait(6, 12)

        # 8. Like the post
        print("[STEP] Locating the Like button...", flush=True)
        click_like_button(page, timeout_ms=10_000)

        # 9. Append to history
        commented = read_json(config.COMMENTED_FILE, default=[]) or []
        if not isinstance(commented, list):
            commented = []
        if target_url not in commented:
            commented.append(target_url)
            write_json(config.COMMENTED_FILE, commented)

        # 10. Advance the pipeline
        status_data["comment_posted"] = True
        write_json(config.STATUS_FILE, status_data)

        print("[STEP] Finalizing...", flush=True)
        custom_random_wait(15, 30)
        reset_comment_status()

    except LocatorResolutionError as exc:
        print(f"[ERROR] {exc}", flush=True)
        capture_and_upload_error(page)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] Script crashed: {exc}", flush=True)
        capture_and_upload_error(page)
        sys.exit(1)
    finally:
        if browser:
            browser.close()
        try:
            _playwright.stop()
        except Exception:
            pass


if __name__ == "__main__":
    run()