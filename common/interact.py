"""Interaction layer: DOM first, Gemini Vision as the automatic fallback.

Every action goes through this module so that a failing locator never crashes a
script while the vision fallback is available. The contract is:

1. Try the single DOM locator expression declared for the key, using the same
   timeout budget the script used before the fallback existed.
2. If it cannot be resolved or acted upon, ask Gemini Vision for the element and
   perform the action from the returned coordinates.
3. If both fail, raise :class:`LocatorResolutionError` so the caller can capture
   the error screenshot and exit non-zero.
"""

from __future__ import annotations

import time
from typing import Optional, Sequence

from playwright.sync_api import Locator, Page

from common import config
from common.locator_registry import CHATGPT, LINKEDIN, LocatorFileError, LocatorRegistry
from common.vision_fallback import VISION, VisionFallback
from common import locator_healing


class LocatorResolutionError(RuntimeError):
    """Raised when neither the DOM locators nor the vision fallback can act."""


def _log(message: str) -> None:
    print(message, flush=True)


# The startup reconcile must run exactly once per process, even though two
# PageActions instances (LINKEDIN_UI, CHATGPT_UI) are created at import time.
_STARTUP_RECONCILE_DONE = False


def _run_startup_reconcile_once() -> None:
    global _STARTUP_RECONCILE_DONE
    if _STARTUP_RECONCILE_DONE:
        return
    _STARTUP_RECONCILE_DONE = True
    try:
        locator_healing.reconcile_startup()
    except Exception as exc:  # pragma: no cover - never block the run
        _log(f"[HEAL] Startup reconcile failed: {exc}")


class PageActions:
    """Platform-bound locator resolution and interaction helpers."""

    def __init__(
        self,
        registry: LocatorRegistry,
        vision: Optional[VisionFallback] = None,
        default_timeout_ms: Optional[int] = None,
    ) -> None:
        self.registry = registry
        self.vision = vision if vision is not None else VISION
        self.default_timeout_ms = default_timeout_ms or config.DEFAULT_DOM_TIMEOUT_MS
        _run_startup_reconcile_once()

    # ------------------------------------------------------------- internals
    def _timeout(self, timeout_ms: Optional[int]) -> int:
        return int(timeout_ms if timeout_ms is not None else self.default_timeout_ms)

    def _vision_unavailable_reason(self) -> Optional[str]:
        """Return why vision cannot run, or ``None`` when it is available."""
        if not self.vision.enabled:
            return "the vision fallback is disabled (VISION_FALLBACK_ENABLED=false)"
        return None

    def _fail(self, key: str, description: str, reason: str) -> LocatorResolutionError:
        return LocatorResolutionError(
            f"Could not resolve or act on {key!r} ({description!r}) via DOM or vision: {reason}"
        )

    def _record_heal(self, page: Page, key: str, description: str, **params: str) -> None:
        """Ask Gemini for a replacement expression and persist it. Never raises."""
        try:
            locator_healing.heal_live(page, self.registry, key, params, description, self.vision)
        except Exception as exc:  # pragma: no cover - healing must not break the run
            _log(f"[HEAL] Could not record suggestion for {key!r}: {exc}")

    # ------------------------------------------------------------ resolution
    def describe(self, key: str, **params: str) -> str:
        return self.registry.describe(key, params or None)

    def locator(self, page: Page, key: str, **params: str) -> Optional[Locator]:
        """Evaluate the primary DOM locator expression declared for ``key``."""
        try:
            return self.registry.locator(page, key, **params)
        except LocatorFileError:
            return None

    def _wait_first(self, locator: Locator, state: str, budget_ms: int) -> Optional[Locator]:
        """Return a candidate from ``locator`` that reaches ``state`` within ``budget_ms``.

        The expression may match several elements, so each one gets a share of the
        budget recomputed every iteration against the remaining time and the number
        of matches still untried, so a slow first element cannot starve later ones.
        """
        try:
            total = locator.count()
        except Exception:
            total = 0
        if total <= 0:
            try:
                locator.first.wait_for(state=state, timeout=budget_ms)
                return locator
            except Exception:
                return None

        candidates: Sequence[Locator] = [locator.nth(index) for index in range(total)]
        deadline = time.monotonic() + budget_ms / 1000.0

        for position, candidate in enumerate(candidates):
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            untried = len(candidates) - position
            slice_ms = max(1, remaining_ms // untried)
            try:
                candidate.wait_for(state=state, timeout=slice_ms)
                return candidate
            except Exception:
                continue
        return None

    def resolve(
        self,
        page: Page,
        key: str,
        *,
        timeout_ms: Optional[int] = None,
        state: str = "visible",
        **params: str,
    ) -> Optional[Locator]:
        """Return the DOM locator once it reaches ``state``, or ``None``.

        Declared expressions are tried in order: the primary first, then each
        fallback line under the same key. The first one that resolves wins.
        """
        try:
            locators = self.registry.locators(page, key, **params)
        except LocatorFileError:
            return None
        if not locators:
            return None

        # Split the budget across the declared expressions (primary + fallbacks).
        budget_ms = self._timeout(timeout_ms)
        slice_ms = max(1, budget_ms // len(locators))
        for locator in locators:
            resolved = self._wait_first(locator, state, slice_ms)
            if resolved is not None:
                return resolved
        return None

    def count(self, page: Page, key: str, **params: str) -> int:
        """Number of elements matched by the expression for ``key``."""
        locator = self.locator(page, key, **params)
        if locator is None:
            return 0
        try:
            return locator.count()
        except Exception:
            return 0

    def is_visible(self, page: Page, key: str, **params: str) -> bool:
        locator = self.locator(page, key, **params)
        if locator is None:
            return False
        try:
            if locator.first.is_visible():
                return True
        except Exception:
            return False
        try:
            return locator.count() > 0
        except Exception:
            return False

    # --------------------------------------------------------------- actions
    def wait_visible(
        self,
        page: Page,
        key: str,
        *,
        timeout_ms: Optional[int] = None,
        required: bool = False,
        **params: str,
    ) -> bool:
        description = self.describe(key, **params)
        locator = self.resolve(page, key, timeout_ms=timeout_ms, **params)
        if locator is not None:
            return True

        unavailable = self._vision_unavailable_reason()
        if unavailable:
            if required:
                raise self._fail(key, description, unavailable)
            return False

        _log(f"[FALLBACK] DOM wait failed for {key!r}; trying Gemini Vision")
        vision_timeout = min(
            self._timeout(timeout_ms) / 1000.0,
            float(config.VISION_WAIT_MAX_SECONDS),
        )
        if self.vision.wait_for(page, description, timeout=vision_timeout):
            return True

        # Only record a broke locator for required waits: optional waits run in
        # polling/conditional contexts where absence is normal, so healing there
        # would waste vision budget and pollute changed_locators.json.
        if required:
            self._record_heal(page, key, description, **params)
            raise self._fail(key, description, "element never appeared")
        return False

    def click(
        self,
        page: Page,
        key: str,
        *,
        timeout_ms: Optional[int] = None,
        required: bool = True,
        scroll: bool = True,
        **params: str,
    ) -> bool:
        description = self.describe(key, **params)
        locator = self.resolve(page, key, timeout_ms=timeout_ms, **params)

        if locator is not None:
            try:
                target = locator.first
                if scroll:
                    target.scroll_into_view_if_needed(timeout=config.ACTION_TIMEOUT_MS)
                target.click(timeout=config.ACTION_TIMEOUT_MS)
                return True
            except Exception as exc:
                _log(f"[FALLBACK] DOM click failed for {key!r}: {exc}")

        unavailable = self._vision_unavailable_reason()
        if unavailable:
            if required:
                raise self._fail(key, description, unavailable)
            return False

        _log(f"[FALLBACK] Clicking {key!r} via Gemini Vision")
        if self.vision.click(page, description):
            return True

        if required:
            self._record_heal(page, key, description, **params)
            raise self._fail(key, description, "vision could not locate a click target")
        return False

    def fill(
        self,
        page: Page,
        key: str,
        text: str,
        *,
        timeout_ms: Optional[int] = None,
        required: bool = True,
        **params: str,
    ) -> bool:
        description = self.describe(key, **params)
        locator = self.resolve(page, key, timeout_ms=timeout_ms, **params)

        if locator is not None:
            try:
                locator.first.fill(text, timeout=config.ACTION_TIMEOUT_MS)
                return True
            except Exception as exc:
                _log(f"[FALLBACK] DOM fill failed for {key!r}: {exc}")

        unavailable = self._vision_unavailable_reason()
        if unavailable:
            if required:
                raise self._fail(key, description, unavailable)
            return False

        _log(f"[FALLBACK] Filling {key!r} via Gemini Vision")
        if self.vision.fill(page, description, text):
            return True

        if required:
            self._record_heal(page, key, description, **params)
            raise self._fail(key, description, "vision could not locate a fill target")
        return False

    def read_text(
        self,
        page: Page,
        key: str,
        *,
        timeout_ms: Optional[int] = None,
        required: bool = True,
        **params: str,
    ) -> Optional[str]:
        description = self.describe(key, **params)
        locator = self.resolve(page, key, timeout_ms=timeout_ms, **params)

        if locator is not None:
            try:
                text = locator.first.inner_text(timeout=config.ACTION_TIMEOUT_MS)
                if text and text.strip():
                    return text.strip()
            except Exception as exc:
                _log(f"[FALLBACK] DOM text read failed for {key!r}: {exc}")

        unavailable = self._vision_unavailable_reason()
        if unavailable:
            if required:
                raise self._fail(key, description, unavailable)
            return None

        _log(f"[FALLBACK] Reading {key!r} via Gemini Vision")
        text = self.vision.read_text(page, description)
        if text and text.strip():
            return text.strip()

        if required:
            self._record_heal(page, key, description, **params)
            raise self._fail(key, description, "no readable text found")
        return None


# --------------------------------------------------------------------------
# Platform-bound helpers
# --------------------------------------------------------------------------
LINKEDIN_UI = PageActions(LINKEDIN, default_timeout_ms=config.DEFAULT_DOM_TIMEOUT_MS)
CHATGPT_UI = PageActions(CHATGPT, default_timeout_ms=config.DEFAULT_DOM_TIMEOUT_MS)