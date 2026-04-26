"""Encrypt/decrypt panel credentials using Fernet."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet


def _derive_fernet_key(raw_key: str) -> bytes:
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_secret(raw_key: str, plaintext: str) -> str:
    f = Fernet(_derive_fernet_key(raw_key))
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(raw_key: str, token: str) -> str:
    f = Fernet(_derive_fernet_key(raw_key))
    return f.decrypt(token.encode("ascii")).decode("utf-8")
