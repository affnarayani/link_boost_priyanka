"""Browser helpers shared by every automation script.

Randomised pacing, error-screenshot capture with upload, and the small JSON
file helpers used by the pipeline state files.
"""

from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Union

import requests
from playwright.sync_api import Page

from common import config

PathLike = Union[str, Path]


# --------------------------------------------------------------------------
# Human-like pacing
# --------------------------------------------------------------------------
def custom_random_wait(min_sec: float = None, max_sec: float = None) -> float:
    """Sleep for a random interval so actions do not look machine-timed."""
    low = config.CUSTOM_WAIT_MIN_SECONDS if min_sec is None else min_sec
    high = config.CUSTOM_WAIT_MAX_SECONDS if max_sec is None else max_sec
    seconds = random.uniform(low, high)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)
    return seconds


def step_wait(min_sec: float = None, max_sec: float = None) -> float:
    """Shorter pause used between individual UI steps."""
    low = config.STEP_WAIT_MIN_SECONDS if min_sec is None else min_sec
    high = config.STEP_WAIT_MAX_SECONDS if max_sec is None else max_sec
    seconds = random.uniform(low, high)
    print(f"[WAIT] Dynamic step delay: sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)
    return seconds


# --------------------------------------------------------------------------
# Screenshot capture and upload
# --------------------------------------------------------------------------
def upload_to_onlyfiles(screenshot_path: PathLike) -> Union[str, None]:
    """Upload a screenshot to onlyfiles.com and return the direct link (48h expiry)."""
    try:
        with open(screenshot_path, "rb") as file:
            response = requests.post(
                config.ONLYFILES_UPLOAD_URL,
                files={"file": file},
                data={"expire": config.ONLYFILES_EXPIRY_SECONDS},
                timeout=config.ONLYFILES_TIMEOUT_SECONDS,
            )
    except requests.RequestException as exc:
        print(f"[WARNING] Screenshot upload failed: {exc}", flush=True)
        return None

    if response.status_code != 200:
        print(f"[WARNING] Upload failed with status {response.status_code}", flush=True)
        return None

    try:
        payload = response.json()
        direct_url = payload["data"]["file"]["url"]["full"]
    except (ValueError, KeyError, TypeError) as exc:
        print(f"[WARNING] Unexpected upload response: {exc}", flush=True)
        return None

    print(f"[LINK] Direct link (expires in 48 hours): {direct_url}", flush=True)
    return direct_url


def capture_and_upload_error(page: Union[Page, None], label: str = "error") -> Union[str, None]:
    """Capture a full-page screenshot to ``error/error_screenshot.png`` and upload it.

    Any existing file at that path is overwritten. Never raises.
    """
    if page is None:
        return None

    target = config.ERROR_SCREENSHOT_FILE
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=True)
        print(f"[OK] {label} screenshot captured: {target}", flush=True)
    except Exception as exc:
        print(f"[WARNING] Could not capture screenshot: {exc}", flush=True)
        return None
    return upload_to_onlyfiles(target)


# --------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------
def read_json(path: PathLike, default: Any = None) -> Any:
    target = Path(path)
    if not target.exists():
        return default
    with target.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: PathLike, data: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)


def clear_json_list(path: PathLike) -> None:
    print(f"[INIT] Clearing contents of {Path(path).name}...", flush=True)
    write_json(path, [])


def append_to_json_list(path: PathLike, entry: Dict[str, Any]) -> None:
    target = Path(path)
    existing: List[Dict[str, Any]] = []
    if target.exists() and target.stat().st_size > 0:
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except (ValueError, OSError):
            existing = []
    existing.append(entry)
    write_json(target, existing)


def load_connections(path: PathLike) -> List[Dict[str, Any]]:
    target = Path(path)
    if not target.exists():
        print(f"[ERROR] {target.name} not found.", flush=True)
        return []
    with target.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


def save_connections(path: PathLike, data: List[Dict[str, Any]]) -> None:
    write_json(path, data)
    print(f"[INFO] Saved status updates to {Path(path).name}", flush=True)


def reset_comment_status() -> Dict[str, Any]:
    """Reset the comment pipeline flags back to ``false``."""
    reset = {
        "post_to_comment_found": False,
        "comment_generated": False,
        "comment_posted": False,
    }
    write_json(config.STATUS_FILE, reset)
    return reset