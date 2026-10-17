"""Service-token verification, wire-compatible with ``libs/go/svcauth`` (SE-040, ADR-0007).

Token: ``v1.<base64url(claims JSON)>.<base64url(ed25519 signature over "v1." + claims)>`` with
claims ``{iss, aud, iat, exp, jti}``; lifetime <= 60 s, 10 s leeway, audience = this service.
Callers' public keys come from mounted secrets ``svc_<caller>_ed25519.pub`` (base64, 32 bytes).
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

TOKEN_TTL = 60
LEEWAY = 10
SCHEME = "TB-Service "


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


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Verifier:
    def __init__(
        self, service: str, caller_keys: dict[str, Ed25519PublicKey], now: Callable[[], float] = time.time
    ) -> None:
        self._service = service
        self._keys = caller_keys
        self._now = now

    @classmethod
    def from_secrets(cls, service: str, callers: list[str], secrets_dir: str | Path) -> Verifier:
        keys = {}
        for caller in callers:
            path = Path(secrets_dir) / f"svc_{caller}_ed25519.pub"
            if path.exists():
                raw = base64.b64decode(path.read_text(encoding="utf-8").strip())
                keys[caller] = Ed25519PublicKey.from_public_bytes(raw)
        return cls(service, keys)

    def verify(self, token: str) -> Claims:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            raise TokenError("malformed token")
        try:
            payload = _b64url_decode(parts[1])
            sig = _b64url_decode(parts[2])
            raw = json.loads(payload)
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
        if claims.audience != self._service:
            raise TokenError("wrong audience")
        return claims


def mint(issuer: str, audience: str, key: Ed25519PrivateKey, now: float | None = None, jti: str = "t") -> str:
    """Mint a token like the Go signer (tests and local tooling)."""
    issued = int(now if now is not None else time.time())
    claims = {"iss": issuer, "aud": audience, "iat": issued, "exp": issued + TOKEN_TTL, "jti": jti}
    body = "v1." + _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    return body + "." + _b64url_encode(key.sign(body.encode()))
