"""Service-token signing for Python gRPC clients, wire-compatible with ``libs/go/svcauth`` (SE-040,
ADR-0007). Token: ``v1.<base64url(claims)>.<base64url(ed25519 sig over "v1." + claims)>`` with
claims ``{iss, aud, iat, exp, jti}`` and a 60 s lifetime; sent as ``authorization: TB-Service <token>``.
The private seed comes from the mounted secret ``svc_<service>_ed25519`` (base64, 32 bytes).
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tbcore.secrets import get_secret

TOKEN_TTL = 60
SCHEME = "TB-Service "


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def key_secret_name(service: str) -> str:
    return "svc_" + service.replace("-", "_") + "_ed25519"


class Signer:
    def __init__(self, service: str, seed: bytes) -> None:
        if len(seed) != 32:
            raise ValueError("svcauth: seed must be 32 bytes")
        self.service = service
        self._key = Ed25519PrivateKey.from_private_bytes(seed)

    @classmethod
    def from_secrets(cls, service: str, secrets_dir: str | Path = "/run/secrets") -> Signer:
        return cls(service, base64.b64decode(get_secret(key_secret_name(service), secrets_dir)))

    def token(self, audience: str, now: float | None = None) -> str:
        issued = int(now if now is not None else time.time())
        claims = {
            "iss": self.service,
            "aud": audience,
            "iat": issued,
            "exp": issued + TOKEN_TTL,
            "jti": _b64url(os.urandom(12)),
        }
        body = "v1." + _b64url(json.dumps(claims, separators=(",", ":")).encode())
        return body + "." + _b64url(self._key.sign(body.encode()))

    def metadata(self, audience: str) -> tuple[tuple[str, str], ...]:
        return (("authorization", SCHEME + self.token(audience)),)


LEEWAY = 10


class TokenError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Claims:
    issuer: str
    audience: str
    issued_at: int
    expires: int
    token_id: str


def _b64url_decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class Verifier:
    """Verifies tokens for one audience against callers' public keys (SE-040; same rules as Go and
    the IBKR adapter: lifetime <= 60 s, 10 s leeway, audience = this service)."""

    def __init__(
        self, service: str, caller_keys: dict[str, Ed25519PublicKey], now: Callable[[], float] = time.time
    ) -> None:
        self.service = service
        self._keys = caller_keys
        self._now = now

    @classmethod
    def from_secrets(cls, service: str, callers: list[str], secrets_dir: str | Path = "/run/secrets") -> Verifier:
        keys = {}
        for caller in callers:
            path = Path(secrets_dir) / (key_secret_name(caller) + ".pub")
            if path.exists():
                raw = base64.b64decode(path.read_text(encoding="utf-8").strip())
                keys[caller] = Ed25519PublicKey.from_public_bytes(raw)
        return cls(service, keys)

    def verify(self, token: str) -> Claims:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            raise TokenError("malformed token")
        try:
            raw = json.loads(_b64url_decode(parts[1]))
            sig = _b64url_decode(parts[2])
            claims = Claims(
                issuer=str(raw["iss"]),
                audience=str(raw["aud"]),
                issued_at=int(raw["iat"]),
                expires=int(raw["exp"]),
                token_id=str(raw.get("jti", "")),
            )
        except (binascii.Error, ValueError, KeyError, TypeError) as exc:
            raise TokenError("malformed token") from exc
        key = self._keys.get(claims.issuer)
        if key is None:
            raise TokenError("unknown issuer")
        try:
            key.verify(sig, f"{parts[0]}.{parts[1]}".encode())
        except InvalidSignature as exc:
            raise TokenError("bad signature") from exc
        now = self._now()
        if (
            now > claims.expires + LEEWAY
            or now < claims.issued_at - LEEWAY
            or claims.expires - claims.issued_at > TOKEN_TTL
        ):
            raise TokenError("expired or not yet valid")
        if claims.audience != self.service:
            raise TokenError("wrong audience")
        return claims
