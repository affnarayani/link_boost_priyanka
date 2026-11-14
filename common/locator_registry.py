"""Centralised locator management.

Locators are declared once in ``locators/<platform>_locators.txt`` and never
written inline in Python. Each line is a literal Playwright expression, so the
registry never needs to learn how a locator is expressed::

    key_name = page.get_by_role("button", name="Me", exact=True) :: Me account button

Rules:

* The expression is evaluated with ``page`` and ``re`` in scope and the result
  must be a Playwright ``Locator``. Edit the expression exactly as you would
  edit the equivalent Python call.
* ``{curly_braces}`` are placeholders filled by the calling script via
  ``registry.resolve(page, key, name=...)``. A missing parameter raises
  :class:`LocatorFileError`.
* A key may declare MORE THAN ONE expression on consecutive lines. The first
  line is the *primary*; subsequent lines for the same key are ordered
  *fallbacks* tried only when the primary (and earlier fallbacks) fail. This is
  the format used by the self-healing flow, which appends a suggested locator as
  a new fallback line under an existing key instead of chaining ``.or_(...)``.
* Everything after ``::`` is the natural-language description handed to Gemini
  Vision when the expression matches nothing. Only the primary line needs a
  description; fallback lines may omit it (the primary's description is used).
* Expressions are validated against a small AST whitelist before evaluation and
  compiled code objects are cached per expression string.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from playwright.sync_api import Locator, Page

from common import config


class LocatorFileError(RuntimeError):
    """Raised when a locator file is missing, malformed, or refers to an unknown key."""


@dataclass(frozen=True)
class LocatorEntry:
    key: str
    expressions: Tuple[str, ...]
    description: str

    @property
    def expression(self) -> str:
        """The primary expression (first declared line for this key)."""
        return self.expressions[0]

    @property
    def fallbacks(self) -> Tuple[str, ...]:
        """Ordered fallback expressions (everything after the primary)."""
        return self.expressions[1:]


_ALLOWED_NODES = (
    ast.Expression,
    ast.Call,
    ast.Attribute,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.keyword,
    ast.Starred,
    ast.JoinedStr,
    ast.FormattedValue,
    ast.Tuple,
    ast.List,
)

_COMPILED: Dict[str, "object"] = {}


def _compile(expression: str) -> object:
    """Validate and compile ``expression`` once, caching the code object."""
    cached = _COMPILED.get(expression)
    if cached is not None:
        return cached

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise LocatorFileError(f"Invalid locator expression {expression!r}: {exc}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise LocatorFileError(
                f"Disallowed syntax {type(node).__name__} in locator expression {expression!r}"
            )

    code = compile(tree, "<locator>", "eval")
    _COMPILED[expression] = code
    return code


def validate_expression(expression: str) -> bool:
    """Return True when ``expression`` passes the AST whitelist (no evaluation)."""
    try:
        _compile(expression)
    except LocatorFileError:
        return False
    return True


def _substitute(expression: str, params: Mapping[str, str]) -> str:
    """Fill ``{placeholder}`` values; a missing parameter is a hard error."""
    try:
        return expression.format(**params)
    except KeyError as exc:
        raise LocatorFileError(
            f"Missing placeholder {exc} for locator expression {expression!r}"
        ) from None
    except (IndexError, ValueError) as exc:
        raise LocatorFileError(
            f"Malformed placeholder in locator expression {expression!r}: {exc}"
        ) from None


def _parse_line(line: str) -> Tuple[str, str, str]:
    """Split ``key = expression :: description`` into ``(key, expression, description)``."""
    body, _, description = line.partition("::")
    key, sep, expression = body.partition("=")
    if not sep:
        raise LocatorFileError(f"Locator line is missing '=': {line!r}")

    key = key.strip()
    if not key:
        raise LocatorFileError(f"Locator line has an empty key: {line!r}")

    expression = expression.strip()
    if not expression:
        raise LocatorFileError(f"Locator {key!r} has an empty expression")

    _compile(expression)

    return key, expression, description.strip()


def _humanise(key: str) -> str:
    return key.replace("_", " ").strip()


class LocatorRegistry:
    """Loads and resolves locator declarations from a platform text file."""

    def __init__(self, path: Path, platform: str):
        self.path = Path(path)
        self.platform = platform
        self._entries: Dict[str, LocatorEntry] = {}
        self._load()

    # ---------------------------------------------------------------- loading
    def _load(self) -> None:
        if not self.path.exists():
            raise LocatorFileError(f"Locator file not found: {self.path}")

        entries: Dict[str, LocatorEntry] = {}
        order: List[str] = []
        descriptions: Dict[str, str] = {}
        expressions: Dict[str, List[str]] = {}

        for line_number, raw_line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                key, expression, description = _parse_line(line)
            except LocatorFileError as exc:
                raise LocatorFileError(f"{self.path.name}:{line_number}: {exc}") from exc

            if key not in expressions:
                order.append(key)
                expressions[key] = []
                descriptions[key] = ""

            # First description seen for a key wins (primary line carries it).
            if description and not descriptions[key]:
                descriptions[key] = description

            if expression not in expressions[key]:
                expressions[key].append(expression)

        for key in order:
            entries[key] = LocatorEntry(
                key=key,
                expressions=tuple(expressions[key]),
                description=descriptions[key],
            )

        if not entries:
            raise LocatorFileError(f"{self.path.name} declares no locators")

        self._entries = entries

    def reload(self) -> None:
        """Re-read the locator file from disk (used after a self-healing edit)."""
        self._load()

    # ---------------------------------------------------------------- lookup
    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(sorted(self._entries))

    def has(self, key: str) -> bool:
        return key in self._entries

    def entry(self, key: str) -> LocatorEntry:
        try:
            return self._entries[key]
        except KeyError:
            known = ", ".join(self.keys)
            raise LocatorFileError(
                f"Unknown locator key {key!r} in {self.path.name}. Known keys: {known}"
            ) from None

    def describe(self, key: str, params: Optional[Mapping[str, str]] = None) -> str:
        entry = self.entry(key)
        description = entry.description or _humanise(key)
        if params:
            try:
                description = description.format(**params)
            except (KeyError, IndexError):
                pass
        return description

    # ---------------------------------------------------------- expressions
    def expression_strings(self, key: str) -> Tuple[str, ...]:
        """All declared expressions for ``key`` (primary first, then fallbacks)."""
        return self.entry(key).expressions

    def primary_expression(self, key: str) -> str:
        """The primary (first declared) expression for ``key``."""
        return self.entry(key).expressions[0]

    def has_expression(self, key: str, expression: str) -> bool:
        """True when ``expression`` is already declared (exactly) for ``key``."""
        if not self.has(key):
            return False
        return expression.strip() in self.entry(key).expressions

    # ------------------------------------------------------------- resolution
    def _build(self, page: Page, expression: str, params: Mapping[str, str]) -> Locator:
        source = _substitute(expression, params)
        code = _compile(source)
        scope = {"__builtins__": {}, "page": page, "re": re}
        value = eval(code, scope)  # noqa: S307 - AST-validated, no builtins
        if not isinstance(value, Locator):
            raise LocatorFileError(
                f"Locator expression {source!r} returned "
                f"{type(value).__name__}, expected a Playwright Locator"
            )
        return value

    def locator(self, page: Page, key: str, **params: str) -> Locator:
        """Evaluate the primary declared expression for ``key``."""
        return self._build(page, self.primary_expression(key), params)

    def locators(self, page: Page, key: str, **params: str) -> List[Locator]:
        """Evaluate every declared expression for ``key`` (primary first)."""
        return [self._build(page, expression, params) for expression in self.expression_strings(key)]

    def resolve(self, page: Page, key: str, **params: str) -> Locator:
        """Alias of :meth:`locator`, kept for readability at call sites."""
        return self.locator(page, key, **params)


# --------------------------------------------------------------------------
# Platform registries
# --------------------------------------------------------------------------
LINKEDIN = LocatorRegistry(config.LINKEDIN_LOCATORS_FILE, "linkedin")
CHATGPT = LocatorRegistry(config.CHATGPT_LOCATORS_FILE, "chatgpt")

REGISTRIES: Dict[str, LocatorRegistry] = {
    "linkedin": LINKEDIN,
    "chatgpt": CHATGPT,
}


def for_platform(platform: str) -> LocatorRegistry:
    try:
        return REGISTRIES[platform]
    except KeyError:
        raise LocatorFileError(
            f"No locator registry for platform {platform!r}. "
            f"Available: {', '.join(sorted(REGISTRIES))}"
        ) from None