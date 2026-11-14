"""Shared configuration for the LinkedIn and ChatGPT automation suite.

Almost every tunable lives in ``json/config.json``. This module loads that file,
validates it, and re-exports the values as plain constants so the rest of the
suite can keep using ``config.SOME_NAME``.

Secrets never live in ``config.json``: ``DECRYPT_KEY``, ``GEMINI_API_KEY``,
``EMAIL`` and ``PASSWORD`` are read from ``.env`` (or the process environment)
and nothing in ``config.json`` shadows them.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "json" / "config.json"


def _resolve(relative: str) -> Path:
    """Turn a ``config.json`` path value into an absolute path under the repo."""
    candidate = Path(relative)
    if candidate.is_absolute():
        return candidate
    # A bare filename such as "cookies.json" belongs to the repo root, while a
    # value that already contains a separator ("json/post.json") is relative to
    # the repo root as well. Resolve against ROOT_DIR in both cases.
    return ROOT_DIR / candidate


# --------------------------------------------------------------------------
# config.json loading
# --------------------------------------------------------------------------
def _load_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"Configuration file not found: {CONFIG_FILE}")

    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError(f"Malformed JSON in {CONFIG_FILE}: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"{CONFIG_FILE} must contain a JSON object")
    return data


_CONFIG: Dict[str, Any] = _load_config()


def _section(name: str) -> Dict[str, Any]:
    section = _CONFIG.get(name)
    if section is None:
        raise RuntimeError(f"{CONFIG_FILE} is missing the {name!r} section")
    if not isinstance(section, dict):
        raise RuntimeError(f"{CONFIG_FILE} section {name!r} must be an object")
    return section


def _value(section: str, key: str, default: Any = None) -> Any:
    value = _section(section).get(key, default)
    return default if value is None else value


def _require(section: str, key: str) -> Any:
    if key not in _section(section):
        raise RuntimeError(f"{CONFIG_FILE} section {section!r} is missing {key!r}")
    return _section(section)[key]


def _number(section: str, key: str, default: Any) -> Any:
    value = _value(section, key, default)
    return value


def _path(section: str, key: str, default: str) -> Path:
    return _resolve(str(_value(section, key, default)))


def _str_list(section: str, key: str, default: List[str]) -> List[str]:
    value = _value(section, key, default)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"{CONFIG_FILE} section {section!r} key {key!r} must be a list of strings")
    return list(value)


# --------------------------------------------------------------------------
# Environment (secrets only; config.json never holds these)
# --------------------------------------------------------------------------
load_dotenv(ROOT_DIR / ".env")


def env_str(name: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"{name} is missing from the environment or .env file")
    return value


# --------------------------------------------------------------------------
# Browser
# --------------------------------------------------------------------------
HEADLESS = bool(_value("browser", "headless", True))
USER_AGENT = str(_value("browser", "user_agent", ""))
BROWSER_LAUNCH_ARGS = _str_list(
    "browser", "launch_args", ["--start-maximized", "--disable-blink-features=AutomationControlled"]
)
NO_VIEWPORT = bool(_value("browser", "no_viewport", True))

# --------------------------------------------------------------------------
# Human-like pacing (seconds)
# --------------------------------------------------------------------------
STEP_WAIT_MIN_SECONDS = float(_number("wait", "step_min_seconds", 6.0))
STEP_WAIT_MAX_SECONDS = float(_number("wait", "step_max_seconds", 12.0))
CUSTOM_WAIT_MIN_SECONDS = float(_number("wait", "custom_min_seconds", 15.0))
CUSTOM_WAIT_MAX_SECONDS = float(_number("wait", "custom_max_seconds", 30.0))

# --------------------------------------------------------------------------
# Timeouts (milliseconds)
# --------------------------------------------------------------------------
DEFAULT_DOM_TIMEOUT_MS = int(_number("timeout_ms", "default_dom", 15_000))
LOGIN_DOM_TIMEOUT_MS = int(_number("timeout_ms", "login_dom", 120_000))
ACTION_TIMEOUT_MS = int(_number("timeout_ms", "action", 10_000))
DOWNLOAD_TIMEOUT_MS = int(_number("timeout_ms", "download", 60_000))
CHATGPT_RESPONSE_TIMEOUT_MS = int(_number("timeout_ms", "chatgpt_response", 30_000))
CHATGPT_TEXT_KEYBOARD_TIMEOUT_MS = int(_number("timeout_ms", "chatgpt_text_keyboard", 15_000))

# --------------------------------------------------------------------------
# Sites and entry points
# --------------------------------------------------------------------------
LINKEDIN_BASE_URL = str(_require("linkedin", "base_url"))
LINKEDIN_LOGIN_URL = str(_require("linkedin", "login_url"))
LINKEDIN_HOME_URL = str(_require("linkedin", "home_url"))
LINKEDIN_PROFILE_BASE_URL = str(_require("linkedin", "profile_base_url"))
LINKEDIN_COOKIE_DOMAIN = str(_value("linkedin", "cookie_domain", ".linkedin.com"))
LINKEDIN_PEOPLE_SEARCH_URL = str(_require("linkedin", "people_search_url"))

CHALLENGE_PREFIX = str(_require("linkedin", "challenge_prefix"))
CHALLENGE_V2_PREFIX = str(_require("linkedin", "challenge_v2_prefix"))

CHATGPT_URL = str(_require("chatgpt", "url"))

# --------------------------------------------------------------------------
# Cookie encryption
# --------------------------------------------------------------------------
PBKDF2_ITERATIONS = int(_number("crypto", "pbkdf2_iterations", 200_000))
SESSION_COOKIE_NAME = str(_require("linkedin", "session_cookie_name"))

LINKEDIN_COOKIES_DIR = _path("paths", "linkedin_cookies_dir", "linkedin_cookies")
COOKIE_FILE = _path("paths", "plaintext_cookie_file", "cookies.json")
ENCRYPTED_COOKIE_FILE = _path("paths", "encrypted_cookie_file", "linkedin_cookies/linkedin_cookies.json.encrypted")
LINKEDIN_ENCRYPTED_COOKIES_FILE = ENCRYPTED_COOKIE_FILE
CHATGPT_COOKIES_DIR = _path("paths", "chatgpt_cookies_dir", "chatgpt_cookies")

# --------------------------------------------------------------------------
# Pipeline state files
# --------------------------------------------------------------------------
LOCATORS_DIR = _path("paths", "locators_dir", "locators")
LINKEDIN_LOCATORS_FILE = _path("paths", "linkedin_locators", "locators/linkedin_locators.txt")
CHATGPT_LOCATORS_FILE = _path("paths", "chatgpt_locators", "locators/chatgpt_locators.txt")
CHANGED_LOCATORS_FILE = _path("paths", "changed_locators_file", "locators/changed_locators.json")

JSON_DIR = _path("paths", "json_dir", "json")
STATUS_FILE = _path("paths", "status_file", "json/comment_status.json")
POST_DATA_FILE = _path("paths", "post_data_file", "json/post_to_comment.json")
COMMENTED_FILE = _path("paths", "commented_file", "json/commented.json")
TOPICS_FILE = _path("paths", "topics_file", "json/priyanka_linkedin_topics.json")
POST_FILE = _path("paths", "post_file", "json/post.json")
CONNECTIONS_FILE = _path("paths", "connections_file", "json/scraped_connections.json")

IMAGE_DIR = _path("paths", "image_dir", "image")
IMAGE_FILE = _path("paths", "image_file", "image/image.png")

ERROR_SCREENSHOT_DIR = _path("paths", "error_dir", "error")
ERROR_SCREENSHOT_FILE = _path("paths", "error_screenshot_file", "error/error_screenshot.png")

# --------------------------------------------------------------------------
# Screenshot hosting
# --------------------------------------------------------------------------
ONLYFILES_UPLOAD_URL = str(_require("onlyfiles", "upload_url"))
ONLYFILES_EXPIRY_SECONDS = int(_number("onlyfiles", "expiry_seconds", 172800))
ONLYFILES_TIMEOUT_SECONDS = int(_number("onlyfiles", "request_timeout_seconds", 120))

# --------------------------------------------------------------------------
# Gemini Vision fallback
# --------------------------------------------------------------------------
GEMINI_API_KEY = env_str("GEMINI_API_KEY")
GEMINI_MODEL = str(_value("vision", "model", "gemini-3.5-flash-lite"))
VISION_FALLBACK_ENABLED = bool(_value("vision", "enabled", True))

# Worst case per operation: click may spend 1 + VISION_CLICK_SCROLL_ATTEMPTS
# calls, and wait_for one call per poll. Keep the run budget comfortably above
# that so a single lookup cannot disable the fallback for the rest of the run.
VISION_FALLBACK_MAX_CALLS = int(_number("vision", "max_calls", 150))
VISION_CLICK_SCROLL_ATTEMPTS = int(_number("vision", "click_scroll_attempts", 5))
VISION_WAIT_MAX_POLLS = int(_number("vision", "wait_max_polls", 5))
VISION_WAIT_MAX_SECONDS = int(_number("vision", "wait_max_seconds", 15))
VISION_REQUEST_TIMEOUT_SECONDS = int(_number("vision", "request_timeout_seconds", 60))
VISION_TEMPERATURE = float(_number("vision", "temperature", 0.0))

# --------------------------------------------------------------------------
# Locator self-healing
# --------------------------------------------------------------------------
# When true, at program start each pending suggestion in
# locators/changed_locators.json is applied to the corresponding locators/*.txt
# as an extra fallback line under the same key, and the JSON entry is removed.
# When false, entries are only removed once their suggested_locator already
# exists in the .txt file (no automatic edit of the locator file).
AUTO_CORRECT_LOCATOR = bool(_value("locator_healing", "auto_correct_locator", True))

# --------------------------------------------------------------------------
# Script tunables
# --------------------------------------------------------------------------
CHATGPT_MAX_RETRIES = int(_number("chatgpt", "max_retries", 10))
CHATGPT_SHARED_URL_MARKER = str(_value("chatgpt", "shared_url_marker", "chatgpt.com/s/"))
CHATGPT_COPY_POPUP_SETTLE_MS = int(_number("chatgpt", "copy_popup_settle_ms", 2000))
CHATGPT_CONTINUE_PROMPT_SETTLE_MS = int(_number("chatgpt", "continue_prompt_settle_ms", 3000))
WITHDRAW_AFTER_DAYS = int(_number("linkedin", "withdraw_after_days", 7))
EMPTY_PAGE_LIMIT = int(_number("pipeline", "empty_page_limit", 3))
MAX_NAME_LENGTH = int(_number("pipeline", "max_name_length", 80))
SEARCH_PAGE_SETTLE_MS = int(_number("pipeline", "search_page_settle_ms", 5000))
POST_NAVIGATE_SETTLE_MS = int(_number("pipeline", "post_navigate_settle_ms", 2000))
SEARCH_PAGE_PAUSE_MS = int(_number("pipeline", "search_page_pause_ms", 2000))

# --------------------------------------------------------------------------
# Cron generation (update_timing.py)
# --------------------------------------------------------------------------
TIMING_MIN_HOUR = int(_number("timing_cron", "min_hour", 2))
TIMING_MAX_HOUR = int(_number("timing_cron", "max_hour", 22))
TIMING_MIN_GAP_MINUTES = int(_number("timing_cron", "min_gap_minutes", 15))