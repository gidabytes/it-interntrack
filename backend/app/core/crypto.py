"""Fernet field-level encryption for data at rest.

Fernet (AES-128-CBC + HMAC-SHA256, URL-safe base64 token) gives authenticated
symmetric encryption, so a tampered ciphertext fails to decrypt rather than
silently yielding garbage.

Key handling
------------
``FERNET_KEYS`` is a comma-separated list of base64 keys. The FIRST key is the
primary and performs all new encryption; every key in the list is tried on
decryption. That is what makes key rotation possible without downtime:

    1. generate a new key, prepend it   -> FERNET_KEYS=new,old
    2. new writes use `new`, old rows still readable via `old`
    3. run `python -m app.cli rotate-encryption` to re-encrypt in place
    4. drop `old` from the list

Keys live in the environment or a secrets file - NEVER in the database they
protect, and never in version control.

Scope and limits (state these plainly in any security review)
-------------------------------------------------------------
* This protects database files, backups and dumps at rest. It does NOT protect
  against a compromised application process, which holds the keys in memory.
* Encrypted columns are opaque to SQL: no LIKE, no ORDER BY, no useful index.
  Where lookup by an encrypted value is required, a deterministic HMAC "blind
  index" column is stored alongside it (see ``blind_index``).
* Blind indexes are deterministic by design, so they leak equality: two rows
  with the same email produce the same index. That is the trade-off that makes
  lookup possible, and it is why the blind-index key is separate from the
  encryption keys.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import warnings
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import String, Text, TypeDecorator

FERNET_TOKEN_PREFIX = "gAAAAA"


class EncryptionNotConfigured(RuntimeError):
    """Raised when encryption is required but no keys are available."""


def generate_key() -> str:
    """Generate a new Fernet key, base64 text suitable for FERNET_KEYS."""
    return Fernet.generate_key().decode()


def generate_blind_index_key() -> str:
    """Generate a 32-byte key for blind indexes, as hex."""
    return os.urandom(32).hex()


def _raw_setting(name: str) -> str:
    """Read a key from the environment, then from the .env-backed settings.

    The environment wins so that tests and key-rotation scripts can override a
    value in-process without rewriting .env.
    """
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        from app.core.config import settings

        return str(getattr(settings, name, "") or "").strip()
    except Exception:  # pragma: no cover - config unavailable
        return ""


def _configured_keys() -> list[str]:
    raw = _raw_setting("FERNET_KEYS")
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


@lru_cache(maxsize=1)
def _cipher() -> MultiFernet | None:
    """Build the MultiFernet from the configured keys.

    Returns None when encryption is disabled, which is permitted only in
    development. ``require_encryption()`` is what enforces the production rule.
    """
    keys = _configured_keys()
    if not keys:
        return None
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except (ValueError, TypeError) as exc:  # malformed key
        raise EncryptionNotConfigured(
            "FERNET_KEYS contains a malformed Fernet key. Each key must be "
            "32 url-safe base64-encoded bytes; generate one with "
            "`python -m app.cli generate-key`."
        ) from exc


def reset_cipher_cache() -> None:
    """Clear the cached cipher. Used by tests and by key rotation."""
    _cipher.cache_clear()
    _blind_index_key.cache_clear()


def encryption_enabled() -> bool:
    return _cipher() is not None


def require_encryption(debug: bool = False) -> None:
    """Fail fast at startup when encryption is missing outside development."""
    if encryption_enabled():
        return
    message = (
        "Field-level encryption is not configured: FERNET_KEYS is empty. "
        "Sensitive intern data would be stored in plaintext. Generate a key "
        "with `python -m app.cli generate-key` and set FERNET_KEYS."
    )
    if debug:
        warnings.warn(message + " Continuing because DEBUG is enabled.", stacklevel=2)
        return
    raise EncryptionNotConfigured(message)


def encrypt_text(value: str) -> str:
    """Encrypt a string to a Fernet token. Passthrough when disabled."""
    cipher = _cipher()
    if cipher is None:
        return value
    return cipher.encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    """Decrypt a Fernet token.

    Values that are not Fernet tokens are returned unchanged, so a database
    written before encryption was switched on stays readable. Values that LOOK
    like tokens but fail authentication raise, because that means either key
    loss or tampering - both of which must be loud, not silently ignored.
    """
    cipher = _cipher()
    if cipher is None:
        return token
    if not token.startswith(FERNET_TOKEN_PREFIX):
        return token
    try:
        return cipher.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionNotConfigured(
            "Failed to decrypt a stored value. The current FERNET_KEYS cannot "
            "read this ciphertext - either a key was removed before re-encryption "
            "or the data was tampered with."
        ) from exc


@lru_cache(maxsize=1)
def _blind_index_key() -> bytes | None:
    raw = _raw_setting("BLIND_INDEX_KEY")
    if not raw:
        return None
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


def blind_index(value: str | None, *, length: int = 32) -> str | None:
    """Deterministic, keyed lookup hash for an encrypted value.

    HMAC-SHA256 under a dedicated key, truncated to ``length`` hex chars. Keyed
    rather than a bare hash so an attacker with the database cannot confirm a
    guessed email by hashing it themselves.
    """
    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    key = _blind_index_key()
    if key is None:
        # No blind-index key: fall back to an unkeyed digest so lookups still
        # function in development. Documented as weaker, never for production.
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    else:
        digest = hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:length]


class EncryptedString(TypeDecorator):
    """A String column transparently encrypted with Fernet at rest.

    The database stores ciphertext; application code reads and writes plain
    Python strings. Note the stored length: a Fernet token is roughly
    4/3 * (plaintext + 57) bytes, so the underlying column is sized generously.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: int = 512, **kwargs):
        super().__init__(length=length, **kwargs)

    def process_bind_param(self, value, dialect):  # noqa: D102
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        if value == "":
            return ""
        return encrypt_text(value)

    def process_result_value(self, value, dialect):  # noqa: D102
        if value is None or value == "":
            return value
        return decrypt_text(value)


class EncryptedText(EncryptedString):
    """Same as EncryptedString for long free-text fields."""

    impl = Text
    cache_ok = True

    def __init__(self, **kwargs):
        TypeDecorator.__init__(self, **kwargs)
