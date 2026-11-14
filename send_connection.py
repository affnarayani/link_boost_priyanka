"""Send one LinkedIn connection request per run.

Picks the first profile from ``scraped_connections.json`` that has not been
contacted yet, sends the invitation, and records the attempt with a timestamp.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

from login import login_and_get_context

from common import config
from common.browser import (
    capture_and_upload_error,
    custom_random_wait,
    load_connections,
    save_connections,
)
from common.interact import LINKEDIN_UI, LocatorResolutionError


def run() -> None:
    print("[START] Script started", flush=True)

    connections_path = Path(config.CONNECTIONS_FILE)
    connections = load_connections(connections_path)

    if not connections:
        print("[DONE] No connection data to process.", flush=True)
        return

    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        _playwright, browser, _context, page = login_and_get_context(is_headless=config.HEADLESS)
    except Exception as exc:
        print(f"[ERROR] Login session failed: {exc}", flush=True)
        capture_and_upload_error(page=None)
        sys.exit(1)

    try:
        print(f"[STEP] Opening the LinkedIn feed: {config.LINKEDIN_HOME_URL}", flush=True)
        page.goto(config.LINKEDIN_HOME_URL, wait_until="load")

        print("[STEP] Verifying login status via the account menu...", flush=True)
        LINKEDIN_UI.wait_visible(
            page,
            "me_account_button",
            timeout_ms=config.LOGIN_DOM_TIMEOUT_MS,
            required=True,
        )
        print("[SUCCESS] Login succeeded: account menu detected.\n", flush=True)

        for index, item in enumerate(connections):
            if "sent" in item:
                continue

            name = item.get("name", "Unknown")
            profile_link = item.get("profile_link")

            if not profile_link:
                print(f"[SKIP] Missing profile link for index {index}", flush=True)
                continue

            print(f"[PROCESSING] Moving to profile: {name} ({profile_link})", flush=True)

            try:
                page.goto(profile_link, wait_until="load")
                custom_random_wait(3, 6)

                escaped_name = re.escape(str(name))
                if LINKEDIN_UI.count(page, "profile_invite_link", name=escaped_name) > 0:
                    print(f"[ACTION] Invite link found for {name}. Clicking now...", flush=True)
                    LINKEDIN_UI.click(page, "profile_invite_link", required=False, name=escaped_name)
                    custom_random_wait(15, 30)

                    if LINKEDIN_UI.count(page, "connect_send_without_note_button") > 0:
                        print("[ACTION] Clicking 'Send without a note'...", flush=True)
                        LINKEDIN_UI.click(
                            page, "connect_send_without_note_button", required=False
                        )
                        custom_random_wait(15, 30)
                    else:
                        print("[INFO] 'Send without a note' popup missing or request auto-sent.", flush=True)
                else:
                    print(f"[INFO] Connect / Invite link not found for {name}.", flush=True)

            except LocatorResolutionError as item_error:
                print(f"[WARNING] Error handling item {name}: {item_error}", flush=True)

            item["sent"] = True
            item["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            item["withdrawn"] = False

            save_connections(connections_path, connections)
            print(
                f"[SUCCESS] Log updated for {name}. Requesting browser teardown.\n",
                flush=True,
            )
            break

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
            _playwright.stop()
        except Exception:
            pass
        print("[DONE] Script execution environment torn down cleanly.", flush=True)


if __name__ == "__main__":
    run()