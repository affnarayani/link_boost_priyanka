"""Generate a LinkedIn post body with ChatGPT and store it in ``post.json``."""

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


def can_run_script() -> bool:
    """Return True when the previous topic in the list has been fully processed."""
    print(f"[STEP] Checking the last post status in {config.TOPICS_FILE.name}...", flush=True)

    if not config.TOPICS_FILE.exists():
        print(f"[WARNING] '{config.TOPICS_FILE.name}' not found. Proceeding by default...", flush=True)
        return True

    try:
        topics = json.loads(config.TOPICS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[WARNING] Could not read the topics file ({exc}). Proceeding...", flush=True)
        return True

    if not topics or not isinstance(topics, list):
        print("[OK] Topics list is empty. Proceeding...", flush=True)
        return True

    last_processed_item = None
    for item in topics:
        if isinstance(item, dict) and "content_generated" in item:
            last_processed_item = item

    if last_processed_item is None:
        print("[OK] First post detected (no previous history found). Proceeding...", flush=True)
        return True

    content_generated = last_processed_item.get("content_generated") is True
    image_generated = last_processed_item.get("image_generated") is True
    posted = last_processed_item.get("posted") is True

    if content_generated and image_generated and posted:
        print(
            f"[OK] Last post '{last_processed_item.get('topic')}' has all three keys True. "
            f"Continuing.",
            flush=True,
        )
        return True

    print(
        f"[INFO] Script halted! Last post '{last_processed_item.get('topic')}' is incomplete: "
        f"content_generated={content_generated}, image_generated={image_generated}, posted={posted}.",
        flush=True,
    )
    return False


def get_next_topic_from_json() -> str:
    """Return the first topic that has not been processed yet."""
    print(f"[STEP] Reading {config.TOPICS_FILE.name}...", flush=True)
    if not config.TOPICS_FILE.exists():
        raise FileNotFoundError(f"'{config.TOPICS_FILE.name}' not found.")

    topics = read_json(config.TOPICS_FILE, default=[]) or []
    if not topics:
        raise ValueError(f"'{config.TOPICS_FILE.name}' is empty.")

    for item in topics:
        if isinstance(item, dict) and "content_generated" not in item:
            selected = item["topic"]
            print(f"[OK] Selected next topic: '{selected}'", flush=True)
            return selected

    raise ValueError(f"No new unprocessed topic found in '{config.TOPICS_FILE.name}'.")


def update_topic_status_in_json(topic_text: str) -> None:
    """Mark a topic as content-generated."""
    print(f"[STEP] Updating topic status in {config.TOPICS_FILE.name}...", flush=True)
    if not config.TOPICS_FILE.exists():
        return

    topics = read_json(config.TOPICS_FILE, default=[]) or []
    for item in topics:
        if item.get("topic") == topic_text:
            item["content_generated"] = True
            item["image_generated"] = False
            item["posted"] = False
            break

    write_json(config.TOPICS_FILE, topics)
    print(
        f"[OK] Topic status updated (content_generated=True) in {config.TOPICS_FILE.name}",
        flush=True,
    )


def _wait_for_code_block(page: Page, attempts: int = 5, cycles: int = 15) -> Optional[str]:
    """Poll the ChatGPT code block until the JSON payload stops growing."""
    print("[STEP] Waiting for the JSON code block to finish streaming...", flush=True)

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
                    print("[OK] Content generation finished.", flush=True)
                    break
                print("[WARNING] Generation paused but the closing '}' is missing.", flush=True)

            last_length = current_length

        if json_content:
            break

        if attempt < attempts:
            print("[WARNING] Code block not compiled yet. Waiting for the next window...", flush=True)
            custom_random_wait(30, 60)

    return json_content


BUILD_PROMPT = (
    "IMPORTANT: Your entire response must be wrapped inside a single ```json code block. "
    "STRICTLY: Do not output any text, explanation, markdown, commentary, or notes before or after the JSON code block.\n\n"
    "You are an elite LinkedIn ghostwriter who specializes in creating high-performing educational posts that generate engagement, saves, shares, and comments.\n\n"
    "Write a LinkedIn post on the topic: '{topic}'.\n\n"
    "PRIMARY OBJECTIVE:\n"
    "Create a post that teaches one valuable concept while keeping readers engaged until the end.\n"
    "The post should feel naturally written by a knowledgeable professional, not by an AI, textbook, lawyer, journalist, or academic writer.\n\n"
    "TARGET AUDIENCE:\n"
    "Professionals, founders, students, working adults, and general readers with little or no prior knowledge of the topic.\n\n"
    "LINKEDIN WRITING RULES:\n"
    "- Target length: 150-220 words.\n"
    "- Use short paragraphs.\n"
    "- Optimize for mobile reading.\n"
    "- Use natural line breaks frequently.\n"
    "- Write in a conversational yet authoritative tone.\n"
    "- Avoid jargon whenever possible.\n"
    "- If technical terms are necessary, explain them simply.\n"
    "- Avoid sounding like an article, textbook, legal document, or lecture.\n"
    "- Avoid generic filler.\n"
    "- Avoid repeating the same point.\n"
    "- Avoid phrases such as:\n"
    "  'It is important to understand'\n"
    "  'Understanding this helps'\n"
    "  'In conclusion'\n"
    "  'Let's discuss'\n"
    "  'Here's why'\n\n"
    "CONTENT REQUIREMENTS:\n"
    "- Start with a strong curiosity-driven hook.\n"
    "- The first 2 lines must make readers want to continue.\n"
    "- Introduce a common misconception, surprising fact, or misunderstood aspect of the topic.\n"
    "- Explain the concept in a simple and practical way.\n"
    "- Include at least one insight that makes readers think:\n"
    "  'I didn't know that.'\n"
    "- Focus on clarity over completeness.\n"
    "- Readers should learn one useful thing in under 30 seconds.\n\n"
    "POST STRUCTURE:\n"
    "1. Hook\n"
    "2. Misconception or surprising insight\n"
    "3. Clear explanation\n"
    "4. Practical takeaway\n"
    "5. Comment-driving question directly related to the topic\n\n"
    "OUTPUT FORMAT:\n"
    "Return ONLY this JSON structure:\n\n"
    "{{\n"
    '  "title": "{topic}",\n'
    '  "p1": "Hook and curiosity-driven opening...",\n'
    '  "p2": "Misconception or surprising insight...",\n'
    '  "p3": "Explanation and practical takeaway...",\n'
    '  "conclusion": "Topic-related question designed to encourage comments...",\n'
    '  "keywords": ["hashtag1", "hashtag2", "hashtag3", "hashtag4", "hashtag5"]\n'
    "}}\n\n"
    "STRICT RULES:\n"
    "- 'title' must exactly match '{topic}'.\n"
    "- 'keywords' must contain exactly 5 relevant hashtags without the '#' symbol.\n"
    "- Do not use markdown headings.\n"
    "- Do not use numbered lists.\n"
    "- Do not use bullet points.\n"
    "- Do not include emojis unless they genuinely improve readability.\n"
    "- Ensure the conclusion question is directly related to the main insight of the post.\n"
    "- Return valid JSON only inside a single code block.\n"
)


def run() -> None:
    print("[START] Script started", flush=True)

    if not can_run_script():
        print("[INFO] Conditions not met. Exiting gracefully...", flush=True)
        sys.exit(0)

    # Only reset the output file once the pre-conditions passed
    write_json(config.POST_FILE, {})
    print(f"[OK] '{config.POST_FILE.name}' cleared/initialized after condition validation.", flush=True)

    try:
        topic = get_next_topic_from_json()
    except Exception as exc:
        print(f"[ERROR] Could not read the configuration files: {exc}", flush=True)
        sys.exit(1)

    playwright_cm = None
    browser = None
    page = None
    try:
        playwright_cm, _playwright, browser, _context, page = launch_chatgpt_session()
        custom_random_wait(30, 60)
        verify_chatgpt_session(page)

        print("[STEP] Locating the chat textbox...", flush=True)
        CHATGPT_UI.click(page, "chatgpt_chat_input")
        custom_random_wait(15, 30)

        print("[STEP] Entering the prompt into the textbox...", flush=True)
        CHATGPT_UI.fill(page, "chatgpt_chat_input", BUILD_PROMPT.format(topic=topic))
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
            parsed_json["title"] = topic

            print(f"[STEP] Saving to {config.POST_FILE.name}...", flush=True)
            write_json(config.POST_FILE, parsed_json)
            print(f"[OK] LinkedIn post saved to {config.POST_FILE.name}", flush=True)

            update_topic_status_in_json(topic)

        except json.JSONDecodeError as exc:
            print(f"[ERROR] Failed to parse the content JSON: {exc}. Exiting...", flush=True)
            capture_and_upload_error(page)
            sys.exit(1)

        print("[STEP] Performing a random wait before normal browser closure...", flush=True)
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