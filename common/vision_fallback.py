"""Gemini Vision fallback layer.

Used whenever a DOM locator from ``locators/*.txt`` cannot be resolved or
cannot be acted upon. Sends a screenshot plus a natural-language description to
Gemini and turns the answer into a real Playwright interaction.

Model: ``gemini-3.5-flash-lite`` (low-latency multimodal, sub-1s typical).
SDK:   ``google-genai`` (``from google import genai``).
Auth:  ``GEMINI_API_KEY``. ``AQ.`` style keys must use the native
       ``generativelanguage.googleapis.com`` endpoint, which this SDK does.
"""

from __future__ import annotations

import io
import json
import re
import time
from typing import Any, Dict, Optional, Tuple

from PIL import Image, UnidentifiedImageError
from playwright.sync_api import Page

from common import config

_COORD_KEYS = ("x", "y")
_JSON_BLOB = re.compile(r"\{[\s\S]*\}")
_DEFAULT_TIMEOUT_SECONDS = config.VISION_REQUEST_TIMEOUT_SECONDS
_SCROLL_SETTLE_SECONDS = 1.0
_POST_CLICK_SETTLE_SECONDS = 0.4


def _log(message: str) -> None:
    print(message, flush=True)


class VisionUnavailableError(RuntimeError):
    """Raised when the Gemini client cannot be constructed or reached."""


class VisionFallback:
    """Screenshot-driven element location and interaction via Gemini Vision."""

    def __init__(
        self,
        model_name: Optional[str] = None,
        api_key: Optional[str] = None,
        max_calls: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> None:
        self.model_name = model_name or config.GEMINI_MODEL or "gemini-3.5-flash-lite"
        self.api_key = api_key if api_key is not None else config.GEMINI_API_KEY
        self.max_calls = max_calls if max_calls is not None else config.VISION_FALLBACK_MAX_CALLS
        self.enabled = config.VISION_FALLBACK_ENABLED if enabled is None else enabled
        self._client: Any = None
        self._calls = 0
        self._warned_about_key = False

    # ------------------------------------------------------------- lifecycle
    @property
    def calls_made(self) -> int:
        return self._calls

    @property
    def budget_exhausted(self) -> bool:
        return self._calls >= self.max_calls

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.enabled:
            raise VisionUnavailableError("Vision fallback is disabled by configuration")
        if not self.api_key:
            raise VisionUnavailableError(
                "GEMINI_API_KEY is not set; cannot use the Gemini Vision fallback"
            )
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise VisionUnavailableError(
                "The 'google-genai' package is not installed. "
                "Run: pip install google-genai"
            ) from exc

        self._client = genai.Client(api_key=self.api_key)
        _log(f"[VISION] Gemini client ready (model={self.model_name})")
        return self._client

    def _consume_budget(self, description: str) -> bool:
        if not self.enabled:
            return False
        if self.budget_exhausted:
            _log(
                f"[VISION] Call budget of {self.max_calls} exhausted; "
                f"skipping vision lookup for {description!r}"
            )
            return False
        self._calls += 1
        return True

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _screenshot(page: Page) -> Tuple[bytes, int, int]:
        png = page.screenshot(full_page=False, type="png")
        try:
            with Image.open(io.BytesIO(png)) as image:
                return png, image.width, image.height
        except UnidentifiedImageError:
            return png, 0, 0

    @staticmethod
    def _scroll_state(page: Page) -> Tuple[float, float]:
        scroll_y = page.evaluate("window.scrollY")
        max_scroll_y = page.evaluate("document.body.scrollHeight - window.innerHeight")
        return float(scroll_y or 0), float(max_scroll_y or 0)

    def _scroll_down(self, page: Page) -> bool:
        before, limit = self._scroll_state(page)
        if before >= limit:
            return False
        page.evaluate("window.scrollBy(0, window.innerHeight)")
        time.sleep(_SCROLL_SETTLE_SECONDS)
        after, _ = self._scroll_state(page)
        if after == before:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(_SCROLL_SETTLE_SECONDS)
            after, _ = self._scroll_state(page)
        return after != before

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        match = _JSON_BLOB.search(text or "")
        if not match:
            return None
        try:
            payload = json.loads(match.group())
        except (ValueError, TypeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _norm_to_px(value: Any, dimension: int) -> Optional[int]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if numeric < 0 or dimension <= 0:
            return None
        bounded = max(0.0, min(1000.0, numeric))
        return max(0, min(dimension - 1, int(bounded / 1000.0 * dimension)))

    def _generate(
        self, page: Page, prompt: str, json_mode: bool = False
    ) -> Tuple[str, int, int]:
        """Send a prompt plus one screenshot; return ``(text, width, height)``."""
        client = self._get_client()
        png, width, height = self._screenshot(page)

        config_kwargs: Dict[str, Any] = {"temperature": config.VISION_TEMPERATURE}
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        try:
            from google.genai import types

            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="MINIMAL")
            contents = [
                prompt,
                types.Part.from_bytes(data=png, mime_type="image/png"),
            ]
            response = client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )
        except Exception as exc:
            raise VisionUnavailableError(f"Gemini request failed: {exc}") from exc

        return (getattr(response, "text", "") or "").strip(), width, height

    # ------------------------------------------------------------ public API
    def locate(self, page: Page, description: str) -> Optional[Tuple[int, int]]:
        """Return the centre point of a described element in viewport pixels."""
        if not self._consume_budget(description):
            return None

        prompt = (
            f'Find this element in the screenshot: "{description}". '
            'Return the centre point as JSON with keys "x" and "y" on a 0-1000 scale '
            "relative to the image dimensions. "
            'If the element is not present, return {"x": -1, "y": -1}. '
            "Return ONLY valid JSON, nothing else."
        )
        try:
            raw, width, height = self._generate(page, prompt, json_mode=True)
        except VisionUnavailableError as exc:
            _log(f"[VISION] Unavailable: {exc}")
            return None

        data = self._parse_json(raw)
        if not data:
            _log(f"[VISION] Unparseable response: {raw[:200]!r}")
            return None

        x = self._norm_to_px(data.get(_COORD_KEYS[0]), width)
        y = self._norm_to_px(data.get(_COORD_KEYS[1]), height)
        if x is None or y is None:
            return None
        return x, y

    def exists(self, page: Page, description: str) -> bool:
        return self.locate(page, description) is not None

    def click(self, page: Page, description: str) -> bool:
        """Click a described element, scrolling the viewport if it is off-screen."""
        coordinates = self.locate(page, description)
        if coordinates is None:
            for _ in range(max(0, config.VISION_CLICK_SCROLL_ATTEMPTS)):
                if not self._scroll_down(page):
                    break
                coordinates = self.locate(page, description)
                if coordinates is not None:
                    break
        if coordinates is None:
            _log(f"[VISION] Could not locate {description!r} for click")
            return False

        x, y = coordinates
        page.mouse.click(x, y)
        _log(f"[VISION] Clicked {description!r} at ({x}, {y})")
        return True

    def fill(self, page: Page, description: str, text: str) -> bool:
        """Focus a described field and type into it."""
        if not self.click(page, description):
            return False
        time.sleep(_POST_CLICK_SETTLE_SECONDS)
        page.keyboard.type(text, delay=20)
        return True

    def read_text(self, page: Page, description: str) -> Optional[str]:
        """Read the visible text of a described element."""
        if not self._consume_budget(description):
            return None

        prompt = (
            f'Read and return ONLY the visible text content of: "{description}" '
            "in this screenshot. Return only the text, nothing else."
        )
        try:
            text, _, _ = self._generate(page, prompt)
        except VisionUnavailableError as exc:
            _log(f"[VISION] Unavailable: {exc}")
            return None
        return text or None

    def wait_for(
        self,
        page: Page,
        description: str,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_polls: Optional[int] = None,
    ) -> bool:
        """Poll for a described element, bounded by both time and call count.

        Every poll costs one Gemini request, so the poll count is capped
        independently of the timeout. Without this cap a long timeout on a
        scrollable page would spend one call per second and drain the run
        budget.
        """
        polls = max(1, config.VISION_WAIT_MAX_POLLS if max_polls is None else max_polls)
        deadline = time.monotonic() + timeout

        for _ in range(polls):
            if time.monotonic() >= deadline:
                break
            if self.exists(page, description):
                return True
            if not self._scroll_down(page):
                return False
            time.sleep(_SCROLL_SETTLE_SECONDS)
        return False

    def suggest_locator(self, page: Page, description: str) -> Optional[str]:
        """Ask Gemini for a single Playwright expression that locates ``description``.

        Returns the raw expression string (caller is responsible for validating it
        against the AST whitelist and dry-running it), or ``None`` when the model
        is unavailable, disabled, or returns nothing usable.
        """
        if not self._consume_budget(description):
            return None

        prompt = (
            f'Find this element in the screenshot: "{description}". '
            "Return ONLY a single Playwright Python expression that uniquely locates this "
            'element, as JSON with a single key "locator". The expression is evaluated with '
            "`page` (a Playwright Page) and `re` in scope, and must evaluate to a Locator. "
            "Prefer get_by_test_id, then get_by_role, then get_by_text, then a CSS locator; "
            "chain alternates with .or_(...). Do not include markdown, backticks, or any text "
            'outside the JSON. If you cannot produce one, return {"locator": ""}.'
        )
        try:
            raw, _, _ = self._generate(page, prompt, json_mode=True)
        except VisionUnavailableError as exc:
            _log(f"[VISION] Unavailable: {exc}")
            return None

        data = self._parse_json(raw)
        if not data:
            _log(f"[VISION] Unparseable locator suggestion: {raw[:200]!r}")
            return None

        locator = str(data.get("locator", "") or "").strip()
        if not locator:
            return None
        return locator


# --------------------------------------------------------------------------
# Shared instance (client is created lazily on first use)
# --------------------------------------------------------------------------
VISION = VisionFallback()