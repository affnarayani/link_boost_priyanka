# Plan: Self-healing locators + `changed_locators.json` + `auto_correct_locator`

## Goal
When a DOM locator stops matching, the existing Gemini Vision fallback already fixes the
task in the current run. We now also:

1. **Detect** the DOM failure (the signal already present in `common/interact.py`).
2. **Ask Gemini** to return a real Playwright expression for the same element.
3. **Persist** `{key, platform, broke_locator, suggested_locator}` into
   `locators/changed_locators.json` **live** (append/update as it happens, not at the end).
4. **Remove** a JSON entry as soon as that locator works again in the `.txt`
   (reconcile: primary-or-fallback matches).
5. Add **`auto_correct_locator: true|false`** to `json/config.json`:
   - `true`  → at program start, apply each pending `suggested_locator` into the `.txt`
               as a **duplicate fallback line under the same key** (primary preserved),
               then remove that JSON entry, then run.
   - `false` → at program start, for each JSON entry check whether the `suggested_locator`
               is already present in the `.txt`; if yes remove the JSON entry, if no keep it.
6. Empty state = `[]` (never dummy values).

## Key design decisions (confirmed with user)
- **Broken signal**: DOM resolution/click/fill/read fails → vision is triggered. That is the
  moment to record a broke locator.
- **New locator source**: Gemini returns a Playwright expression (validated against the AST
  whitelist + dry-run count on the live page) → stored as `suggested_locator`.
- **JSON schema**: `[{ "key": "...", "platform": "linkedin|chatgpt",
  "broke_locator": "<expr>", "suggested_locator": "<expr>" }]`.
- **Apply target format**: duplicate keys on separate lines. First line = primary, later
  same-key lines = fallbacks tried in order. NOT `.or_()` chaining.
  ```
  login_email_input = page.get_by_role("textbox", name="Email or phone") :: description
  login_email_input = page.get_by_test_id("email-input")
  ```
  Fallback lines carry no `::` description (vision uses the primary's description).
- **Resolution order**: primary → fallback lines (in file order) → Gemini Vision.
- **Vision OFF + auto_correct OFF**: skip writing new broke entries that run (can't produce a
  suggested_locator); still do the reconcile removal.
- **Unknown key in JSON** (key no longer in `.txt`): skip silently during reconcile.
- **Reconcile runs once in `PageActions`/`LocatorRegistry` init, before any locator is used**;
  new broke entries are appended live during the run.

## File-by-file changes

### 1) `json/config.json`
Add a top-level section:
```json
"locator_healing": { "auto_correct_locator": true }
```
(place near the `vision` block). Default `true`.

### 2) `common/config.py`
Add constants:
- `AUTO_CORRECT_LOCATOR = bool(_value("locator_healing", "auto_correct_locator", True))`
- `CHANGED_LOCATORS_FILE = _path("paths", "changed_locators_file", "locators/changed_locators.json")`

### 3) `locators/changed_locators.json`
Create with `[]`. (Also add path to `config.json` `paths` section as `changed_locators_file`.)

### 4) `common/locator_registry.py` — parser change (CORE)
Change the data model from one-expression-per-key to **primary + ordered fallbacks per key**:
- `LocatorEntry` gains `expressions: Tuple[str, ...]` (index 0 = primary) and keeps
  `expression` (primary) for back-compat, plus `description` (from the primary line only;
  fallback lines with a `::` description are still accepted but only the first seen wins).
- `_parse_entry` → split into `_parse_line(raw) -> (key, expression, description)`.
- `_load` no longer raises on duplicate keys. It groups consecutive/all lines by key:
  - First occurrence of a key sets `description` (if present).
  - Each subsequent same-key line appends to `expressions`.
  - Duplicate identical expressions are de-duplicated (preserve order).
  - Validate EVERY expression via `_compile`.
- `LocatorEntry.expressions` returns the full ordered tuple.
- `locator(page, key, ...)` still returns the PRIMARY locator (index 0) — used by
  `count`/`is_visible`/`locator()` callers unchanged.
- New method `locators(page, key, ...) -> List[Locator]` returns one Locator per expression
  (primary first), for the fallback-resolution path.
- New helpers used by healing:
  - `expression_strings(key) -> Tuple[str, ...]`
  - `has_expression(key, expr) -> bool` (exact string match after strip)
  - `primary_expression(key) -> str`
  - `set_fallback_expressions(key, exprs)` is NOT stored on the registry; instead the file is
    the source of truth. Healing edits the file directly (see module below). The registry is
    reloaded after an edit (`registry.reload()`).

### 5) `common/locator_healing.py` — NEW module
Responsibilities:
- `load_entries() -> List[dict]` (returns `[]` if file missing/empty/malformed).
- `write_entries(list)` (writes atomically; empty list → `[]`).
- `upsert_broke(key, platform, broke_expr, suggested_expr)` — append new entry OR update the
  existing entry for the same `(key, platform)`:
  - If an entry for `(key, platform)` exists: set `broke_locator`/`suggested_locator` to the
    newest observed values (keep single entry per key+platform, no duplicates).
  - Else append a new entry.
  - Writes immediately (live).
- `remove(key, platform)` — drop the matching entry (live).
- `apply_suggestions_to_file(registry, platform) -> List[str]`:
  - For each JSON entry matching `platform`:
    - Resolve the registry for `platform`. If `registry.has(key)` is False → skip (unknown key).
    - If `auto_correct_locator` is True:
      - If `suggested_locator` already present as a line (exact match) → do nothing to the file.
      - Else append a new line `key = <suggested_locator>` directly under the primary line
        for that key (after the last existing line for that key), preserving description on the
        primary only. De-dup against existing expressions.
      - Remove the JSON entry.
      - Reload that registry.
    - If `auto_correct_locator` is False:
      - If `suggested_locator` already present in the `.txt` → remove the JSON entry.
      - Else keep the JSON entry.
- `reconcile_startup()` — the single entry point called from `PageActions.__init__`:
  - For each platform present in JSON, run `apply_suggestions_to_file`.
  - Reload registries so subsequent resolutions see the new file.
- `heal_live(page, registry, key, params, description, vision) -> Optional[str]`:
  - Called when DOM fails and vision is about to run (or right after a successful vision action).
  - Asks Gemini for a Playwright expression for `description` given the current page screenshot.
  - Validates via `locator_registry._compile` (AST whitelist) and dry-runs
    `page.eval_on_selector`-style count: build the Locator with `registry` scope and check
    `count() >= 0` does not raise. If invalid, returns `None` (no JSON write).
  - If `vision.enabled` is False → return `None` (skip writing new entries).
  - Returns the validated expression (caller upserts).

### 6) `common/vision_fallback.py` — NEW method
Add `suggest_locator(page, description) -> Optional[str]`:
- Sends a JSON-mode prompt: "Return ONLY a single Playwright Python expression (using `page`
  and `re` in scope) that uniquely locates: <description>. No markdown, no explanation."
- Parses JSON `{ "locator": "<expr>" }` (reuse `_parse_json`).
- Returns the raw string or `None`. (Validation/AST-check happens in `locator_healing`.)

### 7) `common/interact.py` — wire healing into resolution (CORE)
- `PageActions.__init__`: call `locator_healing.reconcile_startup()` once (guarded by a module
  flag so it runs a single time even though two `PageActions` instances are created at import).
- `resolve()` / `click()` / `fill()` / `read_text()` / `wait_visible()`:
  - When the DOM path fails (locator is None or action raises) AND before/after the vision
    fallback runs, call a helper `self._record_heal(page, key, params, description)` that:
    - Only attempts when `vision.enabled`.
    - Calls `vision.suggest_locator(page, description)` → `expr`.
    - Validates `expr` (AST + dry-run count on page). If valid:
      - `broke = primary expression of key (or the expression that was tried)`.
      - `locator_healing.upsert_broke(key, platform, broke, suggested=expr)`.
    - Wrapped in try/except so healing never breaks the run.
- **Platform detection**: `PageActions` already knows its `registry.platform` ("linkedin"/"chatgpt").
- The existing `.or_()` chains in the `.txt` remain valid single expressions and are untouched;
  new duplicate-key fallback lines are the new mechanism used only by auto_correct.

### 8) `locators/*.txt` header comment update
Update the `# ...` header in both `linkedin_locators.txt` and `chatgpt_locators.txt` to
document the new "duplicate key = fallback line" convention (primary first, no `::` needed on
fallback lines) and mention `changed_locators.json`.

## Data flow (runtime)
```
script → LINKEDIN_UI/CHATGPT_UI.<action>(page, key, ...)
        └─ PageActions.__init__ already ran reconcile_startup():
              auto_correct=true  → apply pending suggestions to .txt, remove JSON entries, reload
              auto_correct=false → remove JSON entries whose suggestion is already in .txt
        └─ resolve(): try primary expr
              └─ if fail: try each fallback expr (duplicate-key lines)
                    └─ if all DOM fail:
                          vision.<action>  (existing behavior; task still completes)
                          heal_live(): vision.suggest_locator → validate → upsert_broke(json)
        └─ on next run, that key now has a fallback line and no JSON entry
```

## Edge cases handled
- Empty/first run → file created as `[]`; no dummy values.
- Vision disabled → no new entries; reconcile removal still runs.
- Unknown key in JSON → skipped silently.
- Suggested already present in `.txt` (auto_correct=false) → JSON entry removed.
- Suggested already present (auto_correct=true) → no duplicate line; JSON entry removed.
- Duplicate identical expressions de-duped.
- Invalid Gemini expression (fails AST or raises on count) → not written.

## Validation
- `python -c "import common.locator_registry, common.interact, common.locator_healing"` (import smoke test).
- `python -m py_compile` on all changed files.
- Manual: run a script that intentionally references a broken locator; confirm JSON gains an
  entry; set `auto_correct_locator=true`, rerun, confirm `.txt` gains a fallback line and JSON
  is emptied to `[]`.

## Out of scope
- No change to login flow behavior beyond healing.
- No change to vision model/SDK.