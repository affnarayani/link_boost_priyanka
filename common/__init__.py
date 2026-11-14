"""Shared building blocks for the LinkedIn and ChatGPT automation suite.

Submodules:
    config            Paths, URLs, environment tunables
    locator_registry  KEY=selector locator files and the parser
    vision_fallback   Gemini Vision element location and interaction
    browser           Randomised waits, screenshot upload, JSON helpers
    cookies           PBKDF2/AES-GCM cookie encryption and session loading
    chatgpt           ChatGPT session bootstrap shared by three scripts
    interact          DOM-first interaction with automatic vision fallback
"""

from common import (
    browser,
    chatgpt,
    config,
    cookies,
    interact,
    locator_registry,
    vision_fallback,
)

__all__ = [
    "browser",
    "chatgpt",
    "config",
    "cookies",
    "interact",
    "locator_registry",
    "vision_fallback",
]