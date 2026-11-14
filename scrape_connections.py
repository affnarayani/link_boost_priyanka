"""Scrape LinkedIn people-search results into ``scraped_connections.json``.

Walks paginated search results, keeps only cards that expose both a profile
link and an invite link, and stops after three consecutive empty pages.
"""

from __future__ import annotations

import re
import sys
import time

from login import login_and_get_context

from common import config
from common.browser import (
    append_to_json_list,
    capture_and_upload_error,
    clear_json_list,
    custom_random_wait,
)
from common.interact import LINKEDIN_UI, LocatorResolutionError


MAX_NAME_LENGTH = config.MAX_NAME_LENGTH
EMPTY_PAGE_LIMIT = config.EMPTY_PAGE_LIMIT


def _collect_page_links(page) -> list:
    """Return every hyperlink currently rendered on the search results page."""
    locator = LINKEDIN_UI.locator(page, "search_result_links")
    if locator is None:
        return []
    try:
        return locator.all()
    except Exception:
        return []


def run() -> None:
    print("[START] Script started", flush=True)

    clear_json_list(config.CONNECTIONS_FILE)

    print("[STEP] Initializing session via login.py...", flush=True)
    try:
        _playwright, browser, _context, page = login_and_get_context(is_headless=config.HEADLESS)
    except Exception as exc:
        print(f"[ERROR] Login session failed: {exc}", flush=True)
        sys.exit(1)

    try:
        print(f"[STEP] Opening the LinkedIn feed: {config.LINKEDIN_HOME_URL}", flush=True)
        page.goto(config.LINKEDIN_HOME_URL, wait_until="load")

        print(
            "[VALIDATE] Searching for the login verification locator: 'Me' button "
            f"(timeout: {config.LOGIN_DOM_TIMEOUT_MS} ms)...",
            flush=True,
        )
        LINKEDIN_UI.wait_visible(
            page,
            "me_account_button",
            timeout_ms=config.LOGIN_DOM_TIMEOUT_MS,
            required=True,
        )
        print("[SUCCESS] Login verified via the account menu. Proceeding to search...", flush=True)

        current_page = 1
        empty_pages_count = 0

        while True:
            url_to_navigate = (
                config.LINKEDIN_PEOPLE_SEARCH_URL
                if current_page == 1
                else f"{config.LINKEDIN_PEOPLE_SEARCH_URL}&page={current_page}"
            )
            print(f"[STEP] Navigating to search page {current_page}: {url_to_navigate}", flush=True)
            page.goto(url_to_navigate, wait_until="load")
            page.wait_for_timeout(config.SEARCH_PAGE_SETTLE_MS)

            all_links = _collect_page_links(page)

            if not all_links:
                print(f"[INFO] No role links found on page {current_page}.", flush=True)
                capture_and_upload_error(page)
                sys.exit(1)

            profiles_scraped_on_this_page = 0
            processed_names: set = set()

            for link in all_links:
                try:
                    raw_text = link.inner_text()
                    if not raw_text:
                        continue

                    normalized_text = re.sub(r"\s+", " ", raw_text).strip()

                    if not normalized_text or len(normalized_text) > MAX_NAME_LENGTH:
                        continue

                    if "Verified" in normalized_text:
                        clean_name = re.sub(r"\s+Verified$", "", normalized_text).strip()
                    else:
                        clean_name = normalized_text

                    if not clean_name or clean_name in processed_names:
                        continue

                    escaped_name = re.escape(clean_name)

                    name_matches = LINKEDIN_UI.count(
                        page, "search_result_profile_link", name=escaped_name
                    )
                    connect_matches = LINKEDIN_UI.count(
                        page, "search_result_invite_link", name=clean_name
                    )

                    if name_matches > 0 and connect_matches > 0:
                        processed_names.add(clean_name)

                        profile_url = None
                        try:
                            profile_locator = LINKEDIN_UI.locator(
                                page, "search_result_profile_link", name=escaped_name
                            )
                            profile_url = profile_locator.first.get_attribute("href")
                        except Exception:
                            profile_url = None

                        if profile_url and profile_url.startswith("/"):
                            profile_url = f"{config.LINKEDIN_PROFILE_BASE_URL}{profile_url}"

                        print(
                            f"[SCRAPE] Match found strictly via the specified locators: {clean_name}",
                            flush=True,
                        )
                        append_to_json_list(
                            config.CONNECTIONS_FILE,
                            {"name": clean_name, "profile_link": profile_url},
                        )
                        profiles_scraped_on_this_page += 1

                except Exception:
                    continue

            print(
                f"[PAGE SUMMARY] Page {current_page} done. Appended: {profiles_scraped_on_this_page}",
                flush=True,
            )

            if profiles_scraped_on_this_page == 0:
                empty_pages_count += 1
            else:
                empty_pages_count = 0

            if empty_pages_count >= EMPTY_PAGE_LIMIT:
                print(
                    f"[TERMINATE] {EMPTY_PAGE_LIMIT} consecutive pages with 0 results. Stopping workflow.",
                    flush=True,
                )
                break

            current_page += 1
            time.sleep(config.SEARCH_PAGE_PAUSE_MS / 1000.0)

        print("[SUCCESS] All rules executed. Preparing final window teardown.", flush=True)
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