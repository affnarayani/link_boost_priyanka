"""Find a LinkedIn post to comment on and publish it to the pipeline state.

Copies the permalink of the first suitable feed post, reads its body text, and
records it in ``post_to_comment.json`` for the comment-generation stage.
"""

from __future__ import annotations

import sys
from pathlib import Path

from login import login_and_get_context

from common import config
from common.browser import (
    capture_and_upload_error,
    custom_random_wait,
    read_json,
    step_wait,
    write_json,
)
from common.interact import LINKEDIN_UI, LocatorResolutionError


MIN_POST_LENGTH = 150


def run() -> None:
    print("[START] Script started", flush=True)

    # 1. Pipeline gate
    status_path = Path(config.STATUS_FILE)
    if not status_path.exists():
        print(f"[ERROR] {status_path.name} not found.", flush=True)
        sys.exit(1)

    status_data = read_json(status_path, default={}) or {}
    if (
        status_data.get("post_to_comment_found") is not False
        or status_data.get("comment_generated") is not False
        or status_data.get("comment_posted") is not False
    ):
        print("[INFO] Status check failed: flags are not all 'false'.", flush=True)
        sys.exit(0)

    # 2. Session
    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        _playwright, browser, context, page = login_and_get_context(is_headless=config.HEADLESS)
        context.grant_permissions(["clipboard-read", "clipboard-write"])
    except Exception as exc:
        print(f"[ERROR] Login failed: {exc}", flush=True)
        sys.exit(1)

    try:
        page.goto(config.LINKEDIN_HOME_URL, wait_until="load")
        custom_random_wait(6, 12)

        # 3. Prefer the "Recent" feed sort so the newest posts surface first
        print("[STEP] Switching the feed sort to Recent...", flush=True)
        try:
            LINKEDIN_UI.click(page, "feed_sort_by_top_button", required=False)
            step_wait()
            LINKEDIN_UI.click(page, "feed_sort_recent_option", required=False)
            print("[INFO] Feed sorted to Recent...", flush=True)
            step_wait()
        except LocatorResolutionError as exc:
            print(f"[INFO] Could not change the sort option (or it is already Recent): {exc}", flush=True)

        # 4. Open the control menu of the first post
        print("[STEP] Locating the control menu for the first post...", flush=True)
        LINKEDIN_UI.click(page, "post_control_menu_button")
        step_wait()

        # 5. Copy the post link
        print("[STEP] Clicking 'Copy link to post'...", flush=True)
        LINKEDIN_UI.click(page, "post_copy_link_menu_item")
        step_wait()

        raw_url = page.evaluate("navigator.clipboard.readText()")
        trimmed_url = raw_url.split("?")[0]
        print(f"[INFO] Trimmed URL: {trimmed_url}", flush=True)

        # 6. Skip posts that were already commented on
        commented = read_json(config.COMMENTED_FILE, default=[]) or []
        if trimmed_url in commented:
            print("[INFO] URL already commented. Exiting.", flush=True)
            capture_and_upload_error(page)
            sys.exit(1)

        # 7. Read the post body
        page.goto(trimmed_url, wait_until="load")
        step_wait()
        post_content = LINKEDIN_UI.read_text(page, "post_content_text")

        if not post_content or len(post_content) < MIN_POST_LENGTH:
            print("[FAIL] Post content too short.", flush=True)
            capture_and_upload_error(page)
            sys.exit(1)

        # 8. Persist for the next stage
        write_json(config.POST_DATA_FILE, {"url": trimmed_url, "content": post_content})

        # 9. Advance the pipeline
        status_data["post_to_comment_found"] = True
        write_json(status_path, status_data)

        print("[SUCCESS] Process completed.", flush=True)
        custom_random_wait(15, 30)

    except LocatorResolutionError as exc:
        print(f"[ERROR] {exc}", flush=True)
        capture_and_upload_error(page)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] {exc}", flush=True)
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