"""Publish the generated LinkedIn post (text plus image) from ``post.json``."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any, Dict

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


def clean_and_format_post(post_data: Dict[str, Any]) -> str:
    """Collapse the generated sections into a single LinkedIn-ready body."""
    paragraphs = [
        re.sub(r"\n+", "\n", str(post_data.get(key, "")))
        for key in ("p1", "p2", "p3", "conclusion")
    ]
    combined_body = "\n\n".join(paragraphs)
    combined_body = re.sub(r"\n{3,}", "\n\n", combined_body)

    keywords = post_data.get("keywords", []) or []
    hashtags = " ".join(f"#{str(keyword).strip()}" for keyword in keywords)

    return f"{combined_body}\n\n{hashtags}"


def run() -> None:
    print("[START] Script started", flush=True)

    post_file = Path(config.POST_FILE)
    topics_file = Path(config.TOPICS_FILE)

    if not post_file.exists() or not topics_file.exists():
        print(f"[ERROR] Required files missing ({post_file.name} or {topics_file.name})", flush=True)
        return

    post_data = read_json(post_file, default={}) or {}
    topics_list = read_json(topics_file, default=[]) or []
    current_title = post_data.get("title")

    target_topic = None
    target_index = -1
    for index, item in enumerate(topics_list):
        if item.get("topic") == current_title:
            target_topic = item
            target_index = index
            break

    if target_topic is None:
        print(f"[SKIP] Topic '{current_title}' not found.", flush=True)
        return

    if not (
        target_topic.get("content_generated") is True
        and target_topic.get("image_generated") is True
        and target_topic.get("posted") is False
    ):
        print("[SKIP] Pre-conditions not met.", flush=True)
        return

    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        _playwright, browser, _context, page = login_and_get_context(is_headless=config.HEADLESS)
    except Exception as exc:
        print(f"[ERROR] Login session failed: {exc}", flush=True)
        sys.exit(1)

    try:
        page.goto(config.LINKEDIN_HOME_URL, wait_until="load")

        print("[STEP] Verifying login status...", flush=True)
        LINKEDIN_UI.wait_visible(
            page,
            "me_account_button",
            timeout_ms=config.LOGIN_DOM_TIMEOUT_MS,
            required=True,
        )
        step_wait()

        print("[STEP] Clicking 'Start a post'...", flush=True)
        LINKEDIN_UI.click(page, "start_a_post_button")
        step_wait()

        full_post_text = clean_and_format_post(post_data)

        # Focus the editor and reset any stale caret position
        print("[STEP] Focusing the editor and clearing the caret state...", flush=True)
        LINKEDIN_UI.click(page, "post_editor_input")
        time.sleep(1)

        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        time.sleep(1)

        print("[STEP] Typing the post content from the start...", flush=True)
        page.keyboard.type(full_post_text, delay=25)
        step_wait()

        print("[STEP] Uploading media...", flush=True)
        image_path = Path(config.IMAGE_FILE)
        with page.expect_file_chooser() as chooser_info:
            LINKEDIN_UI.click(page, "post_media_button")
        chooser_info.value.set_files(str(image_path))
        step_wait()

        LINKEDIN_UI.click(page, "post_next_button")
        step_wait()

        print("[STEP] Publishing...", flush=True)
        LINKEDIN_UI.click(page, "post_submit_button")
        step_wait()

        topics_list[target_index]["posted"] = True
        write_json(topics_file, topics_list)

        custom_random_wait(30, 60)

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