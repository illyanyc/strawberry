"""Service-token signing for Python gRPC clients, wire-compatible with ``libs/go/svcauth`` (SE-040,
ADR-0007). Token: ``v1.<base64url(claims)>.<base64url(ed25519 sig over "v1." + claims)>`` with
claims ``{iss, aud, iat, exp, jti}`` and a 60 s lifetime; sent as ``authorization: TB-Service <token>``.
The private seed comes from the mounted secret ``svc_<service>_ed25519`` (base64, 32 bytes).
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

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
