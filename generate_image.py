"""Generate a LinkedIn hero image with ChatGPT and save it to ``image/image.png``.

Simple flow: enter prompt → wait for image → click Share → click Download → save.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from playwright.sync_api import Page

from common import config
from common.browser import (
    capture_and_upload_error,
    custom_random_wait,
    read_json,
    write_json,
)
from common.chatgpt import launch_chatgpt_session, teardown
from common.interact import CHATGPT_UI, LocatorResolutionError


MAX_RETRIES = config.CHATGPT_MAX_RETRIES
CONTINUE_PROMPT = "Please continue generating the image"

BUILD_PROMPT = """
Create image for a LinkedIn post with title: "{post_title}". Image must in the ratio 1:1. This will be a hero image to this LinkedIn post. Image must be engaging. The core idea of the image must revolve around these keywords: {post_keywords}. Output only the final image directly.
"""


# --------------------------------------------------------------------------
# Status helpers
# --------------------------------------------------------------------------
def can_run_image_script() -> tuple:
    """Return ``(should_run, topic)`` based on the last processed topic."""
    print(f"[STEP] Checking status in {config.TOPICS_FILE.name}...", flush=True)

    if not config.TOPICS_FILE.exists():
        print(f"[INFO] '{config.TOPICS_FILE.name}' not found. Execution stopped.", flush=True)
        return False, None

    try:
        topics = read_json(config.TOPICS_FILE, default=[]) or []
    except Exception as exc:
        print(f"[ERROR] Could not read the topics file: {exc}.", flush=True)
        return False, None

    if not topics or not isinstance(topics, list):
        print("[INFO] Topics list is empty.", flush=True)
        return False, None

    last_processed_item = None
    for item in topics:
        if isinstance(item, dict) and "content_generated" in item:
            last_processed_item = item

    if last_processed_item is None:
        print("[INFO] No processed entry found.", flush=True)
        return False, None

    content_generated = last_processed_item.get("content_generated") is True
    image_generated = last_processed_item.get("image_generated") is True

    if content_generated and not image_generated:
        print(
            f"[OK] Last post '{last_processed_item.get('topic')}' passed validation. "
            f"Running image generation.",
            flush=True,
        )
        return True, last_processed_item.get("topic")

    print(
        f"[INFO] Script halted! Condition not met: "
        f"content_generated={content_generated}, image_generated={image_generated}.",
        flush=True,
    )
    return False, None


def update_image_status_in_json(topic_text: str) -> None:
    """Mark a topic as image-generated."""
    print(f"[STEP] Updating image status in {config.TOPICS_FILE.name}...", flush=True)
    if not config.TOPICS_FILE.exists():
        return

    topics = read_json(config.TOPICS_FILE, default=[]) or []
    for item in topics:
        if item.get("topic") == topic_text:
            item["image_generated"] = True
            break

    write_json(config.TOPICS_FILE, topics)
    print(
        f"[OK] Image status updated (image_generated=True) in {config.TOPICS_FILE.name}",
        flush=True,
    )


# --------------------------------------------------------------------------
# Interaction helpers
# --------------------------------------------------------------------------
def enter_text_with_keyboard(page: Page, key: str, text: str, timeout_ms: Optional[int] = None) -> bool:
    """Click the target field and type into it with real key events.

    Falls back to Gemini Vision when the DOM locator cannot be resolved.
    """
    locator = CHATGPT_UI.resolve(page, key, timeout_ms=timeout_ms or config.CHATGPT_TEXT_KEYBOARD_TIMEOUT_MS)
    if locator is not None:
        locator.first.click()
        page.keyboard.type(text, delay=20)
        return True
    return CHATGPT_UI.vision.fill(page, CHATGPT_UI.describe(key), text)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def run() -> None:
    print("[START] Script started", flush=True)

    can_run, topic = can_run_image_script()
    if not can_run:
        print("[INFO] Pre-conditions not met. Exiting gracefully...", flush=True)
        sys.exit(0)

    print(f"[STEP] Loading {config.POST_FILE.name}...", flush=True)
    if not config.POST_FILE.exists():
        print(f"[ERROR] '{config.POST_FILE.name}' not found.", flush=True)
        sys.exit(1)

    post_data = read_json(config.POST_FILE, default={}) or {}
    post_title = post_data.get("title", topic)
    post_keywords = post_data.get("keywords", []) or []
    print(f"[OK] Post title: {post_title}", flush=True)

    playwright_cm = None
    browser = None
    page = None
    try:
        playwright_cm, _playwright, browser, context, page = launch_chatgpt_session()
        custom_random_wait(30, 60)

        # Dismiss the workspace switcher if it is showing
        if CHATGPT_UI.is_visible(page, "chatgpt_existing_workspace_open_button"):
            CHATGPT_UI.click(page, "chatgpt_existing_workspace_open_button", required=False)
            print("[STEP] 'Open' button clicked on the workspace row.", flush=True)
            custom_random_wait(30, 60)

        if CHATGPT_UI.is_visible(page, "chatgpt_create_image_button"):
            CHATGPT_UI.click(page, "chatgpt_create_image_button", required=False)
            print("[STEP] 'Create an image' button clicked.", flush=True)
            custom_random_wait(6, 12)

        print("[STEP] Locating the chat textbox...", flush=True)
        CHATGPT_UI.click(page, "chatgpt_image_chat_input")

        formatted = BUILD_PROMPT.format(post_title=post_title, post_keywords=post_keywords)
        prompt_text = " ".join(formatted.split())
        print(f"[STEP] Filling the prompt: '{prompt_text}'", flush=True)

        if not enter_text_with_keyboard(page, "chatgpt_image_chat_input", prompt_text):
            raise LocatorResolutionError("Could not enter the image prompt into the composer.")

        custom_random_wait(6, 12)
        page.keyboard.press("Enter")
        print("[OK] Prompt sent successfully", flush=True)

        # ---- Wait for the image generation (share button appears) -------------
        share_button_visible = False

        for attempt in range(1, MAX_RETRIES + 1):
            print(f"[STEP] Waiting for image generation... attempt {attempt}/{MAX_RETRIES}", flush=True)
            custom_random_wait(30, 60)

            if CHATGPT_UI.is_visible(page, "chatgpt_share_image_button"):
                share_button_visible = True
                print("[OK] 'Share this image' button located successfully!", flush=True)
                break

            if attempt == 5:
                print(
                    f"[INFO] 5 retries complete without an image. Sending '{CONTINUE_PROMPT}'...",
                    flush=True,
                )
                try:
                    if enter_text_with_keyboard(page, "chatgpt_image_chat_input", CONTINUE_PROMPT):
                        page.wait_for_timeout(config.CHATGPT_CONTINUE_PROMPT_SETTLE_MS)
                        page.keyboard.press("Enter")
                        print("[OK] 'Continue generating' prompt sent.", flush=True)
                except Exception as exc:
                    print(f"[WARNING] Could not send the continue prompt: {exc}", flush=True)

        if not share_button_visible:
            print("[ERROR] 'Share this image' button not found after retries. Exiting.", flush=True)
            capture_and_upload_error(page)
            sys.exit(1)

        # ---- Click Share button ---------------------------------------------
        print("[STEP] Clicking 'Share this image' button...", flush=True)
        CHATGPT_UI.click(page, "chatgpt_share_image_button")
        custom_random_wait(15, 30)

        # ---- Click Download button ------------------------------------------
        print("[STEP] Clicking 'Download' button...", flush=True)
        config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with page.expect_download(timeout=config.DOWNLOAD_TIMEOUT_MS) as download_info:
                CHATGPT_UI.click(page, "chatgpt_download_button")
            download = download_info.value
            download.save_as(str(config.IMAGE_FILE))
            print(f"[OK] Image saved to {config.IMAGE_FILE}", flush=True)
        except Exception as exc:
            print(f"[ERROR] Download failed: {exc}", flush=True)
            capture_and_upload_error(page)
            sys.exit(1)

        update_image_status_in_json(topic)
        custom_random_wait(30, 60)
        print("[DONE] Image download successfully processed.", flush=True)

    except SystemExit:
        raise
    except LocatorResolutionError as exc:
        print(f"[ERROR] {exc}", flush=True)
        capture_and_upload_error(page)
        sys.exit(1)
    except Exception as exc:
        print(f"[ERROR] {exc}", flush=True)
        capture_and_upload_error(page)
        sys.exit(1)
    finally:
        teardown(playwright_cm, browser)


if __name__ == "__main__":
    run()