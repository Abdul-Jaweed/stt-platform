"""Credentials and session tokens for the browser UI.

The platform's machine authentication is an API key (``api/security.py``): the
key's tenant *is* the scope, and nothing the client says can change it. The UI
needs the same guarantee without putting a key in a browser bundle, so a login
exchanges a username and password for a **signed, expiring session token** that
carries the server-assigned tenant. Both paths resolve to a scope the same way.

Two deliberate choices:

* Passwords are stored as ``scrypt`` hashes. ``STT_UI_PASSWORD`` is a
  development convenience; ``STT_UI_PASSWORD_HASH`` (the value this module
  produces) is what a deployment should set, so no plaintext password ever
  reaches a process environment or a compose file.
* The token is ``v1.<payload>.<hmac>`` — signed, not encrypted. It holds a
  username, a tenant and an expiry, and the signature is what makes it
  trustworthy; there is nothing secret in it, so a client reading it learns
  nothing it did not already know.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from stt_platform.domain.errors import Unauthorized

TOKEN_VERSION = "v1"
#: scrypt parameters: 2**14 iterations is the interactive-login sweet spot —
#: ~50 ms on a modern core, and 16 MiB of memory per guess for an attacker.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
#: Anything shorter than this is refused outright in production.
MIN_PRODUCTION_PASSWORD_LENGTH = 12


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """``scrypt$<n>$<r>$<p>$<salt>$<hash>`` — self-describing, so parameters
    can be raised later without invalidating existing hashes."""
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return (
        f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}"
        f"${_b64e(salt)}${_b64e(digest)}"
    )


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time verification; a malformed hash is a plain ``False``."""
    if not password or not stored:
        return False
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = _b64d(parts[4]), _b64d(parts[5])
    except (ValueError, TypeError):
        return False
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(expected)
        )
    except (ValueError, MemoryError):  # absurd parameters in a stored hash
        return False
    return hmac.compare_digest(digest, expected)


def is_weak_password(password: str | None) -> bool:
    """The production floor: an empty, short, or default password."""
    if not password:
        return True
    return len(password) < MIN_PRODUCTION_PASSWORD_LENGTH


@dataclass(frozen=True)
class SessionClaims:
    username: str
    tenant: str
    issued_at: float
    expires_at: float


class SessionTokens:
    """Issue and verify ``v1.<payload>.<hmac>`` session tokens."""

    def __init__(self, secret: str, ttl_s: int = 12 * 3600) -> None:
        if not secret:
            raise ValueError("a session secret is required to sign tokens")
        self._secret = secret.encode("utf-8")
        self.ttl_s = int(ttl_s)

    def issue(
        self, username: str, tenant: str, *, now: float | None = None
    ) -> str:
        issued = time.time() if now is None else now
        payload = {
            "sub": username,
            "tenant": tenant,
            "iat": issued,
            "exp": issued + self.ttl_s,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        encoded = _b64e(raw.encode("utf-8"))
        signature = hmac.new(self._secret, encoded.encode("ascii"), hashlib.sha256)
        return f"{TOKEN_VERSION}.{encoded}.{_b64e(signature.digest())}"

    def verify(self, token: str, *, now: float | None = None) -> SessionClaims:
        """The claims behind ``token``, or ``Unauthorized`` — never a guess."""
        try:
            version, encoded, signature = token.split(".")
            if version != TOKEN_VERSION:
                raise ValueError("unknown token version")
            expected = hmac.new(
                self._secret, encoded.encode("ascii"), hashlib.sha256
            ).digest()
            if not hmac.compare_digest(_b64d(signature), expected):
                raise ValueError("bad signature")
            payload = json.loads(_b64d(encoded))
            claims = SessionClaims(
                username=str(payload["sub"]),
                tenant=str(payload["tenant"]),
                issued_at=float(payload["iat"]),
                expires_at=float(payload["exp"]),
            )
        except Unauthorized:
            raise
        except Exception as exc:  # malformed, tampered, or not a token at all
            raise Unauthorized("your session is invalid or has expired") from exc
        moment = time.time() if now is None else now
        if moment >= claims.expires_at:
            raise Unauthorized("your session has expired")
        return claims
