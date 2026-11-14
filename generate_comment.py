"""Generate a LinkedIn comment for the target post using ChatGPT."""

from __future__ import annotations

import json
import sys
from typing import Optional

from playwright.sync_api import Page

from common import config
from common.browser import (
    capture_and_upload_error,
    custom_random_wait,
    read_json,
    write_json,
)
from common.chatgpt import launch_chatgpt_session, teardown, verify_chatgpt_session
from common.interact import CHATGPT_UI, LocatorResolutionError


def _wait_for_code_block(page: Page, attempts: int = 5, cycles: int = 15) -> Optional[str]:
    """Poll the ChatGPT code block until the JSON payload stops growing."""
    print("[STEP] Waiting for the generated JSON code block to complete...", flush=True)

    locator = CHATGPT_UI.resolve(page, "chatgpt_response_code_block", timeout_ms=config.CHATGPT_RESPONSE_TIMEOUT_MS)

    if locator is None:
        print("[FALLBACK] Code block not found in the DOM; asking Gemini Vision to read it.", flush=True)
        text = CHATGPT_UI.vision.read_text(page, CHATGPT_UI.describe("chatgpt_response_code_block"))
        return text if text and text.strip().endswith("}") else None

    json_content = None
    for attempt in range(1, attempts + 1):
        print(f"[STEP] Checking the code block (attempt {attempt}/{attempts})...", flush=True)

        last_length = 0
        for cycle in range(cycles):
            custom_random_wait(15, 30)

            try:
                current_text = locator.first.inner_text().strip()
            except Exception:
                break

            current_length = len(current_text)
            print(
                f"[STREAM INFO] Cycle {cycle + 1}: previous length = {last_length}, "
                f"current length = {current_length}",
                flush=True,
            )

            if current_length > 0 and current_length == last_length:
                if current_text.endswith("}"):
                    json_content = current_text
                    print("[OK] Content generation is fully finished.", flush=True)
                    break
                print("[WARNING] Generation paused but the closing '}' is missing.", flush=True)

            last_length = current_length

        if json_content:
            break

        if attempt < attempts:
            print("[WARNING] Code block not fully compiled yet. Waiting for the next window...", flush=True)
            custom_random_wait(30, 60)

    return json_content


BUILD_PROMPT = (
    "IMPORTANT: Your entire response must be wrapped inside a single ```json code block. "
    "Do not output any text, explanation, markdown, commentary, or notes before or after the code block.\n\n"
    "Read the following LinkedIn post carefully:\n"
    '"""\n{post_content}\n"""\n\n'
    "Your task is to write a thoughtful, professional, and discussion-worthy LinkedIn comment.\n\n"
    "PRIMARY OBJECTIVE:\n"
    "Write the kind of comment that a knowledgeable industry peer would naturally leave after reading the post.\n"
    "The comment should contribute something useful to the conversation rather than merely reacting to it.\n\n"
    "THE GOAL IS NOT TO:\n"
    "- Praise the author\n"
    "- Compliment the post\n"
    "- Summarize the post\n"
    "- Repeat the author's main point\n"
    "- Sound like a corporate chatbot\n"
    "- Sound like an AI assistant\n\n"
    "THE GOAL IS TO:\n"
    "- Advance the discussion\n"
    "- Add a practical real-world consideration\n"
    "- Introduce a nuance, tradeoff, edge case, or second-order implication\n"
    "- Surface an observation that practitioners in the field would recognize\n"
    "- Provide a perspective that was not explicitly stated in the original post\n\n"
    "SILENT ANALYSIS:\n"
    "Before writing the comment:\n"
    "1. Identify the core idea of the post.\n"
    "2. Identify a practical implication, overlooked consideration, edge case, tradeoff, or downstream consequence.\n"
    "3. Build the comment around that insight.\n"
    "4. Prefer practitioner-level observations over theoretical commentary.\n\n"
    "TONE:\n"
    "Write like an experienced professional sharing one useful thought while scrolling LinkedIn.\n"
    "Sound intelligent, observant, practical, conversational, and natural.\n"
    "Write as a reaction, not as a mini-article.\n"
    "If someone got a certification, appraisal, promotion or got hired. Simply praise him/her and nothing more.\n"
    "The comment should feel spontaneous rather than carefully crafted.\n\n"
    "DO NOT SOUND LIKE:\n"
    "- A motivational influencer\n"
    "- A life coach\n"
    "- A marketer\n"
    "- A thought-leadership cliché generator\n"
    "- An academic paper\n\n"
    "HUMANNESS RULE:\n"
    "The comment should feel like it was written by a busy but sharp professional who had one genuinely useful thought while reading the post.\n"
    "No artificial profundity.\n"
    "No exaggerated wisdom.\n"
    "No performative intelligence.\n\n"
    "LENGTH:\n"
    "Preferred range: 80-220 characters including spaces.\n"
    "Be concise.\n\n"
    "FORMAT RULES:\n"
    "- Single continuous line\n"
    "- No newline characters\n"
    "- No emojis\n"
    "- No hashtags\n"
    "- No markdown\n"
    "- No greetings\n"
    "- No sign-offs\n\n"
    "AVOID COMMON AI PHRASES:\n"
    "- This resonates deeply\n"
    "- Insightful share\n"
    "- Thanks for sharing\n"
    "- Spot on\n"
    "- Couldn't agree more\n"
    "- Well said\n"
    "- Great reminder\n"
    "- Valuable perspective\n"
    "- You've captured a crucial point\n"
    "- Or similar generic praise\n\n"
    "QUALITY CHECK:\n"
    "The comment should make an informed reader think:\n"
    "'That's a good point—I hadn't considered that angle.'\n\n"
    "OUTPUT FORMAT — STRICTLY INSIDE A SINGLE JSON CODE BLOCK:\n"
    "{{\n"
    '  "comment": "Your direct single-line LinkedIn comment here"\n'
    "}}\n"
)


def run() -> None:
    print("[START] Script started", flush=True)

    # 1. Pipeline gate
    if not config.STATUS_FILE.exists():
        print(f"[ERROR] {config.STATUS_FILE.name} not found. Exiting...", flush=True)
        sys.exit(0)

    try:
        status_data = read_json(config.STATUS_FILE, default={}) or {}
    except Exception as exc:
        print(f"[ERROR] Could not parse {config.STATUS_FILE.name}: {exc}. Exiting...", flush=True)
        sys.exit(0)

    if not (
        status_data.get("post_to_comment_found") is True
        and status_data.get("comment_generated") is False
        and status_data.get("comment_posted") is False
    ):
        print(f"[INFO] Status requirements not met ({status_data}). Exiting...", flush=True)
        sys.exit(0)

    # 2. Read the post content
    if not config.POST_DATA_FILE.exists():
        print(f"[ERROR] {config.POST_DATA_FILE.name} not found. Exiting...", flush=True)
        sys.exit(0)

    try:
        post_data = read_json(config.POST_DATA_FILE, default={}) or {}
        post_content = str(post_data.get("content", "")).strip()
    except Exception as exc:
        print(f"[ERROR] Could not read {config.POST_DATA_FILE.name}: {exc}", flush=True)
        sys.exit(0)

    if not post_content:
        print(f"[ERROR] 'content' is empty in {config.POST_DATA_FILE.name}. Exiting...", flush=True)
        sys.exit(0)

    playwright_cm = None
    browser = None
    page = None
    try:
        playwright_cm, _playwright, browser, _context, page = launch_chatgpt_session()
        custom_random_wait(30, 60)
        verify_chatgpt_session(page)

        print("[STEP] Locating the chat textbox...", flush=True)
        CHATGPT_UI.click(page, "chatgpt_chat_input")

        prompt = BUILD_PROMPT.format(post_content=post_content)

        print("[STEP] Entering the prompt into the textbox...", flush=True)
        CHATGPT_UI.fill(page, "chatgpt_chat_input", prompt)
        custom_random_wait(15, 30)

        print("[STEP] Locating and clicking the send button...", flush=True)
        CHATGPT_UI.click(page, "chatgpt_send_button")
        custom_random_wait(30, 60)

        json_content = _wait_for_code_block(page)

        if not json_content:
            print("[ERROR] No data fetched from ChatGPT. Exiting...", flush=True)
            capture_and_upload_error(page)
            sys.exit(1)

        try:
            print("[STEP] Parsing the content as JSON...", flush=True)
            if json_content.startswith("```json"):
                json_content = json_content.split("```json", 1)[1]
            if json_content.endswith("```"):
                json_content = json_content.rsplit("```", 1)[0]

            parsed_json = json.loads(json_content.strip())
            generated_comment_text = str(parsed_json.get("comment", "")).strip()
            generated_comment_text = generated_comment_text.replace("\n", " ").replace("\r", "")

            print(f"[STEP] Appending the 'comment' key to {config.POST_DATA_FILE.name}...", flush=True)
            post_data["comment"] = generated_comment_text
            write_json(config.POST_DATA_FILE, post_data)
            print(f"[OK] {config.POST_DATA_FILE.name} updated with the comment data.", flush=True)

            print(f"[STEP] Updating {config.STATUS_FILE.name}...", flush=True)
            status_data["comment_generated"] = True
            write_json(config.STATUS_FILE, status_data)
            print(f"[OK] {config.STATUS_FILE.name} updated: comment_generated set to true.", flush=True)

        except json.JSONDecodeError as exc:
            print(f"[ERROR] Content JSON parse error: {exc}. Exiting...", flush=True)
            capture_and_upload_error(page)
            sys.exit(1)

        print("[STEP] Performing a final wait before closure...", flush=True)
        custom_random_wait(15, 30)

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