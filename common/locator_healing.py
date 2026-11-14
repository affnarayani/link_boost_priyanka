"""Self-healing locators backed by ``locators/changed_locators.json``.

When a DOM locator stops matching, the Gemini Vision fallback still completes the
task, and this module records what broke and the suggested fix. Each entry is::

    {"key": "...", "platform": "linkedin|chatgpt",
     "broke_locator": "<playwright expr>", "suggested_locator": "<playwright expr>"}

Behaviour:

* ``upsert_broke`` / ``remove`` write the JSON file immediately (live), so the
  state is always current rather than flushed at the end of a run.
* ``reconcile_startup`` runs once at process start (from
  :class:`common.interact.PageActions`):

  - ``auto_correct_locator = true``: apply every pending ``suggested_locator`` to
    the platform's ``locators/*.txt`` as an extra fallback line under the same
    key (the primary line is preserved), then remove that JSON entry.
  - ``auto_correct_locator = false``: only remove JSON entries whose
    ``suggested_locator`` already exists in the ``.txt``; keep the rest.

The JSON file is never filled with dummy values: an empty state is ``[]``.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from common import config
from common.locator_registry import (
    REGISTRIES,
    LocatorFileError,
    LocatorRegistry,
    validate_expression,
)

_ENTRY_KEYS = ("key", "platform", "broke_locator", "suggested_locator")


def _log(message: str) -> None:
    print(message, flush=True)


# --------------------------------------------------------------------------
# Low-level JSON helpers
# --------------------------------------------------------------------------
def load_entries() -> List[Dict[str, str]]:
    """Return the raw list of healing entries (empty list if missing/empty/bad)."""
    path = config.CHANGED_LOCATORS_FILE
    if not path.exists() or path.stat().st_size == 0:
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    if not isinstance(data, list):
        return []
    cleaned: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        entry = {key: str(item.get(key, "")) for key in _ENTRY_KEYS}
        if not entry["key"] or not entry["platform"]:
            continue
        cleaned.append(entry)
    return cleaned


def write_entries(entries: List[Dict[str, str]]) -> None:
    """Persist ``entries`` atomically; an empty list writes ``[]``."""
    path = config.CHANGED_LOCATORS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".changed_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(entries, handle, indent=4, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _find(entries: List[Dict[str, str]], key: str, platform: str) -> Optional[int]:
    for index, entry in enumerate(entries):
        if entry["key"] == key and entry["platform"] == platform:
            return index
    return None


# --------------------------------------------------------------------------
# Live updates
# --------------------------------------------------------------------------
def upsert_broke(key: str, platform: str, broke_locator: str, suggested_locator: str) -> None:
    """Append or update the single entry for ``(key, platform)`` and persist it."""
    if not key or not platform or not suggested_locator:
        return
    entries = load_entries()
    record = {
        "key": key,
        "platform": platform,
        "broke_locator": broke_locator or "",
        "suggested_locator": suggested_locator,
    }
    index = _find(entries, key, platform)
    if index is None:
        entries.append(record)
    else:
        entries[index] = record
    write_entries(entries)
    _log(f"[HEAL] Recorded suggested locator for {platform}:{key} -> {suggested_locator}")


def remove(key: str, platform: str) -> None:
    """Drop the entry for ``(key, platform)`` (no-op when absent) and persist it."""
    entries = load_entries()
    index = _find(entries, key, platform)
    if index is None:
        return
    entries.pop(index)
    write_entries(entries)
    _log(f"[HEAL] Removed healed entry for {platform}:{key}")


# --------------------------------------------------------------------------
# Locator-file editing
# --------------------------------------------------------------------------
def _append_fallback_line(path: Path, key: str, suggestion: str) -> bool:
    """Append ``key = suggestion`` directly under the key's existing block.

    Returns True when a new line was written, False when the suggestion is
    already present or the key is not found.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Locate the primary line for ``key`` (first non-comment ``key =`` line).
    primary_index = -1
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        body, _, _ = stripped.partition("::")
        candidate, sep, expression = body.partition("=")
        if sep and candidate.strip() == key:
            primary_index = index
            break

    if primary_index < 0:
        return False

    # If the suggestion is already present for this key, nothing to do.
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        body, _, _ = stripped.partition("::")
        candidate, sep, expression = body.partition("=")
        if sep and candidate.strip() == key and expression.strip() == suggestion.strip():
            return False

    # Find the end of this key's block (last consecutive same-key line).
    insert_at = primary_index + 1
    for index in range(primary_index + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            insert_at = index + 1
            continue
        body, _, _ = stripped.partition("::")
        candidate, sep, _ = body.partition("=")
        if sep and candidate.strip() == key:
            insert_at = index + 1
            continue
        break

    lines.insert(insert_at, f"{key} = {suggestion}")
    path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
    return True


# --------------------------------------------------------------------------
# Startup reconcile
# --------------------------------------------------------------------------
def _apply_for_platform(platform: str, registry: LocatorRegistry, entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Apply/keep/remove JSON entries for ``platform``; return the surviving list."""
    survivors: List[Dict[str, str]] = []
    changed = False

    for entry in entries:
        if entry["platform"] != platform:
            survivors.append(entry)
            continue

        key = entry["key"]
        suggestion = entry["suggested_locator"]

        if not registry.has(key):
            # Key no longer exists in the .txt: drop the stale entry silently.
            _log(f"[HEAL] Skipping unknown key {platform}:{key} (not in {registry.path.name})")
            changed = True
            continue

        if not suggestion:
            # Nothing actionable recorded; drop it.
            changed = True
            continue

        already_present = registry.has_expression(key, suggestion)

        if config.AUTO_CORRECT_LOCATOR:
            if already_present:
                # Developer already added it; just clear the JSON entry.
                _log(f"[HEAL] Suggestion already present for {platform}:{key}; clearing entry")
                changed = True
                continue
            if not validate_expression(suggestion):
                _log(f"[HEAL] Invalid suggested expression for {platform}:{key}; keeping entry")
                survivors.append(entry)
                continue
            if _append_fallback_line(registry.path, key, suggestion):
                _log(f"[HEAL] Applied fallback for {platform}:{key} -> {suggestion}")
                changed = True
                continue
            # Append failed (e.g. race); keep the entry for the next run.
            survivors.append(entry)
            continue

        # auto_correct_locator = false: only clear when it is already in the .txt.
        if already_present:
            _log(f"[HEAL] Suggestion present in file for {platform}:{key}; clearing entry")
            changed = True
            continue
        survivors.append(entry)

    if changed:
        write_entries(survivors)
    return survivors


def reconcile_startup() -> None:
    """Apply pending suggestions (or clear resolved entries) before any locator runs."""
    entries = load_entries()
    if not entries:
        # Ensure the file exists as [] rather than leaving it absent.
        if not config.CHANGED_LOCATORS_FILE.exists():
            write_entries([])
        return

    platforms = {entry["platform"] for entry in entries}
    for platform in platforms:
        registry = REGISTRIES.get(platform)
        if registry is None:
            _log(f"[HEAL] No registry for platform {platform!r}; leaving its entries untouched")
            continue
        remaining = _apply_for_platform(platform, registry, entries)
        entries = remaining
        # Reload the registry so resolutions see any newly appended fallback lines.
        try:
            registry.reload()
        except LocatorFileError as exc:
            _log(f"[HEAL] Could not reload {registry.path.name}: {exc}")


# --------------------------------------------------------------------------
# Live healing during a run
# --------------------------------------------------------------------------
def heal_live(
    page,
    registry: LocatorRegistry,
    key: str,
    params: Dict[str, str],
    description: str,
    vision,
) -> Optional[str]:
    """Ask Gemini for a new expression, validate it, and record it. Return it or None.

    Does nothing (returns None) when vision is disabled or the key is unknown, so
    a broken locator never crashes the run.
    """
    if vision is None or not getattr(vision, "enabled", False):
        return None
    if not registry.has(key):
        return None

    try:
        suggestion = vision.suggest_locator(page, description)
    except Exception as exc:  # pragma: no cover - defensive
        _log(f"[HEAL] Vision suggestion failed for {registry.platform}:{key}: {exc}")
        return None

    if not suggestion:
        return None

    if not validate_expression(suggestion):
        _log(f"[HEAL] Ignoring invalid suggested expression for {registry.platform}:{key}: {suggestion!r}")
        return None

    # Dry-run: the expression must build a real Locator on the live page AND match
    # at least one element. A syntactically valid expression that matches nothing
    # (e.g. a generic body/button selector) would otherwise be recorded and later
    # applied as a fallback that hijacks the real element.
    try:
        built = registry._build(page, suggestion, params)
    except Exception as exc:
        _log(f"[HEAL] Suggested expression did not build for {registry.platform}:{key}: {exc}")
        return None

    try:
        matches = built.count()
    except Exception as exc:
        _log(f"[HEAL] Could not count matches for {registry.platform}:{key}: {exc}")
        return None
    if matches <= 0:
        _log(
            f"[HEAL] Suggested expression matched nothing for {registry.platform}:{key}: {suggestion!r}"
        )
        return None

    try:
        broke = registry.primary_expression(key)
    except LocatorFileError:
        broke = ""

    upsert_broke(key, registry.platform, broke, suggestion)
    return suggestion