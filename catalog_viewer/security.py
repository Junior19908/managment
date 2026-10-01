"""Senha do aplicativo e controle de sessão.

A senha é armazenada como hash PBKDF2-HMAC-SHA256 com salt aleatório.
Hashes antigos (SHA-256 puro, 64 hex) continuam sendo aceitos e são
migrados automaticamente após o primeiro login bem-sucedido.
"""

import hashlib
import hmac
import os
from datetime import datetime, timedelta
from typing import Optional

PBKDF2_ALGO = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16
SESSION_HOURS = 24
MIN_PASSWORD_LENGTH = 4


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Gera ``pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>``."""
    salt = os.urandom(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{PBKDF2_ALGO}${iterations}${salt.hex()}${digest.hex()}"


def _is_legacy_hash(stored: str) -> bool:
    return len(stored) == 64 and all(c in "0123456789abcdef" for c in stored.lower())


def verify_password(password: str, stored: Optional[str]) -> bool:
    """Compara a senha com o hash armazenado (novo ou legado) em tempo constante."""
    if not stored or password is None:
        return False

    if _is_legacy_hash(stored):
        candidate = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return hmac.compare_digest(candidate, stored.lower())

    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != PBKDF2_ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def needs_rehash(stored: Optional[str]) -> bool:
    """True quando o hash está no formato legado ou com menos iterações."""
    if not stored:
        return False
    if _is_legacy_hash(stored):
        return True
    try:
        algo, iters, _, _ = stored.split("$")
        return algo != PBKDF2_ALGO or int(iters) < PBKDF2_ITERATIONS
    except ValueError:
        return True


def session_is_valid(expires_at: Optional[str], now: Optional[datetime] = None) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
    except (ValueError, TypeError):
        return False
    return (now or datetime.now()) < exp


def new_session_expiry(hours: int = SESSION_HOURS, now: Optional[datetime] = None) -> str:
    return ((now or datetime.now()) + timedelta(hours=hours)).isoformat()
