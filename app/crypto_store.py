"""Optional Fernet encryption for panel credentials stored in DB."""

from __future__ import annotations

import base64
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

_fernet = None


def _get_fernet(key: str):
    global _fernet
    if _fernet is not None:
        return _fernet
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning("cryptography not installed; panel secrets stored as plaintext")
        return None
    raw = hashlib.sha256(key.encode("utf-8")).digest()
    fkey = base64.urlsafe_b64encode(raw)
    _fernet = Fernet(fkey)
    return _fernet


def encrypt_secret(plain: str | None, *, encryption_key: str | None) -> str | None:
    if plain is None or plain == "":
        return plain
    if not encryption_key or not encryption_key.strip():
        return plain
    f = _get_fernet(encryption_key.strip())
    if f is None:
        return plain
    return f.encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(stored: str | None, *, encryption_key: str | None) -> str | None:
    if stored is None or stored == "":
        return stored
    if not encryption_key or not encryption_key.strip():
        return stored
    f = _get_fernet(encryption_key.strip())
    if f is None:
        return stored
    try:
        return f.decrypt(stored.encode("ascii")).decode("utf-8")
    except Exception:
        logger.warning("decrypt_secret failed; returning raw value")
        return stored
