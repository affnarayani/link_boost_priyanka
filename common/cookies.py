"""Cookie encryption, decryption, and session loading shared by all scripts.

Payloads are PBKDF2-HMAC-SHA256 (200k iterations) + AES-256-GCM, stored as
base64 fields ``s`` (salt), ``n`` (nonce), and ``ct`` (ciphertext).
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from common import config

PathLike = Union[str, Path]


# --------------------------------------------------------------------------
# Primitive crypto
# --------------------------------------------------------------------------
def derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=config.PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def encrypt_bytes(data: bytes, password: str) -> Dict[str, str]:
    """Encrypt raw bytes into the ``{s, n, ct}`` payload format."""
    from secrets import token_bytes

    salt = token_bytes(16)
    nonce = token_bytes(12)
    key = derive_key(password.encode("utf-8"), salt)
    ciphertext = AESGCM(key).encrypt(nonce, data, None)
    return {
        "v": 1,
        "s": base64.b64encode(salt).decode("ascii"),
        "n": base64.b64encode(nonce).decode("ascii"),
        "ct": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_payload(payload: Dict[str, Any], password: str) -> bytes:
    """Decrypt a ``{s, n, ct}`` payload back into raw bytes."""
    salt = base64.b64decode(payload["s"])
    nonce = base64.b64decode(payload["n"])
    ciphertext = base64.b64decode(payload["ct"])

    key = derive_key(password.encode("utf-8"), salt)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise RuntimeError("Decryption failed (InvalidTag): wrong DECRYPT_KEY or corrupt file") from exc


# --------------------------------------------------------------------------
# Cookie normalisation
# --------------------------------------------------------------------------
def normalize_cookies(cookies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Coerce ``sameSite`` and ``partitionKey`` into Playwright-compatible shapes."""
    normalised: List[Dict[str, Any]] = []

    for cookie in cookies:
        if not isinstance(cookie, dict):
            continue

        partition_key = cookie.get("partitionKey")
        if isinstance(partition_key, dict):
            top_level = partition_key.get("topLevelSite")
            if top_level:
                cookie["partitionKey"] = str(top_level)
            else:
                cookie.pop("partitionKey", None)

        same_site = str(cookie.get("sameSite", "")).lower()
        if same_site in {"no_restriction", "none", "unspecified", "null"}:
            cookie["sameSite"] = "None"
        elif same_site == "lax":
            cookie["sameSite"] = "Lax"
        elif same_site == "strict":
            cookie["sameSite"] = "Strict"
        else:
            cookie["sameSite"] = "Lax"

        normalised.append(cookie)

    return normalised


def load_encrypted_cookies(file_path: PathLike, decrypt_key: str) -> List[Dict[str, Any]]:
    """Load, decrypt, and normalise a ``*.encrypted`` cookie file."""
    path = Path(file_path)
    print("[STEP] Loading and decrypting cookies...", flush=True)

    payload = json.loads(path.read_text(encoding="utf-8"))
    plaintext = decrypt_payload(payload, decrypt_key)
    cookies = json.loads(plaintext.decode("utf-8"))

    if isinstance(cookies, dict):
        nested = cookies.get("cookies")
        cookies = nested if isinstance(nested, list) else [cookies]

    normalised = normalize_cookies(cookies)
    print(f"[OK] Cookies loaded successfully ({len(normalised)} entries)", flush=True)
    return normalised


# --------------------------------------------------------------------------
# LinkedIn session cookie (login.py)
# --------------------------------------------------------------------------
def _read_session_cookie_from_disk() -> Tuple[Optional[Dict[str, Any]], bool]:
    """Return the stored ``li_at`` cookie and whether it has expired."""
    data: Optional[Dict[str, Any]] = None

    decrypt_key = config.env_str("DECRYPT_KEY")
    if decrypt_key and config.ENCRYPTED_COOKIE_FILE.exists():
        try:
            payload = json.loads(config.ENCRYPTED_COOKIE_FILE.read_text(encoding="utf-8"))
            plaintext = decrypt_payload(payload, decrypt_key)
            data = json.loads(plaintext.decode("utf-8"))
        except Exception:
            data = None

    if data is None and config.COOKIE_FILE.exists():
        try:
            data = json.loads(config.COOKIE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = None

    cookie = data.get(config.SESSION_COOKIE_NAME) if isinstance(data, dict) else None
    if not isinstance(cookie, dict) or "value" not in cookie:
        return None, False

    expiry = cookie.get("expires", cookie.get("expiry"))
    if isinstance(expiry, (int, float)) and float(expiry) <= time.time():
        return cookie, True

    if expiry:
        cookie["expires"] = float(expiry)
    return cookie, False


def _write_session_cookie_to_disk(cookie: Dict[str, Any]) -> None:
    """Optionally persist a plaintext cookie file for reuse across runs."""
    if config.ENCRYPTED_COOKIE_FILE.exists():
        return

    try:
        answer = input("Create plaintext cookies.json for session reuse? [y/N]: ")
    except EOFError:
        answer = "n"

    if str(answer).strip().lower() not in {"y", "yes"}:
        return

    payload = {
        config.SESSION_COOKIE_NAME: cookie,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    config.COOKIE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")