"""Withdraw one LinkedIn connection request per run.

Selects the oldest pending invitation (sent more than seven days ago and not
yet withdrawn), opens the profile, and withdraws it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
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


WITHDRAW_AFTER_DAYS = config.WITHDRAW_AFTER_DAYS
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _select_target(connections: list) -> tuple:
    """Return ``(item, index)`` for the oldest eligible invitation, or ``(None, -1)``.

    Eligibility means ``withdrawn is False`` and a timestamp more than
    ``WITHDRAW_AFTER_DAYS`` old. The oldest eligible entry wins so the result
    does not depend on the order of the JSON file.
    """
    cutoff = datetime.now() - timedelta(days=WITHDRAW_AFTER_DAYS)

    oldest_item = None
    oldest_index = -1
    oldest_time = None

    for index, item in enumerate(connections):
        if item.get("withdrawn") is not False:
            continue

        timestamp_str = item.get("timestamp")
        if not timestamp_str:
            continue

        try:
            item_time = datetime.strptime(timestamp_str, TIMESTAMP_FORMAT)
        except ValueError:
            print(f"[WARNING] Invalid date format for index {index}, skipping.", flush=True)
            continue

        if item_time >= cutoff:
            continue

        if oldest_time is None or item_time < oldest_time:
            oldest_item, oldest_index, oldest_time = item, index, item_time

    return oldest_item, oldest_index


def run() -> None:
    print("[START] Script started", flush=True)

    connections_path = Path(config.CONNECTIONS_FILE)
    connections = load_connections(connections_path)

    if not connections:
        print("[DONE] No data found in the connections JSON file.", flush=True)
        sys.exit(0)

    target_item, target_index = _select_target(connections)

    if target_item is None:
        print(
            "[INFO] No profiles found with withdrawn=False and a timestamp older than "
            f"{WITHDRAW_AFTER_DAYS} days. Exiting.",
            flush=True,
        )
        sys.exit(0)

    name = target_item.get("name", "Unknown")
    profile_link = target_item.get("profile_link")

    if not profile_link:
        print(f"[ERROR] Target profile '{name}' has no valid profile_link. Exiting.", flush=True)
        sys.exit(0)

    print(f"[ELIGIBLE] Found profile: {name} | Timestamp: {target_item['timestamp']}", flush=True)

    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        _playwright, browser, _context, page = login_and_get_context(is_headless=config.HEADLESS)
    except Exception as exc:
        print(f"[ERROR] Login session failed: {exc}", flush=True)
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

        print(f"[NAVIGATION] Navigating to the target profile: {profile_link}", flush=True)
        page.goto(profile_link, wait_until="load")
        custom_random_wait(3, 6)

        if LINKEDIN_UI.count(page, "profile_pending_withdraw_link") > 0:
            print("[ACTION] Pending button found. Opening the withdraw modal...", flush=True)
            LINKEDIN_UI.click(page, "profile_pending_withdraw_link", required=False)
            custom_random_wait(15, 30)

            if LINKEDIN_UI.count(page, "withdraw_confirmation_button") > 0:
                print("[ACTION] Clicking the withdraw confirmation button.", flush=True)
                LINKEDIN_UI.click(page, "withdraw_confirmation_button", required=False)
                custom_random_wait(15, 30)
            else:
                print("[INFO] Withdraw confirmation button not found or not visible.", flush=True)
        else:
            print("[INFO] 'Pending, click to withdraw' button not found on this profile.", flush=True)

        connections[target_index]["withdrawn"] = True
        save_connections(connections_path, connections)
        print(f"[SUCCESS] JSON state updated: withdrawn=True for {name}.", flush=True)

        print("[SHUTDOWN] Executing pre-close session buffer wait...", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except LocatorResolutionError as exc:
        print(f"[ERROR] {exc}", flush=True)
        capture_and_upload_error(page)
        sys.exit(1)
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