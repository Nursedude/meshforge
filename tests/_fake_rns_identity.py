"""A stand-in for ``RNS.Identity`` that keeps the REAL library's file semantics.

Why this exists (BIC-15 F1, 2026-10-14): the operator-line and broadcast-bridge
fixtures modelled an unreadable identity as ``from_file`` RAISING. The pinned
library never raises there — ``Identity.load`` catches every error (open
failure, short key, bad key) and ``from_file`` returns ``None``
(``RNS/Identity.py`` ``from_file``/``load``/``load_private_key``). A fixture
that raises pins the author, not the wire, so the ``except`` branch it tested
was dead code in production and ``None`` flowed into
``register_delivery_identity`` instead.

``make_identity_class()`` returns a FRESH subclass per call so a fixture can
hang ``recall = MagicMock(...)`` on it without leaking into other tests.
"""
from __future__ import annotations

import hashlib
import os

KEY_BYTES = 64  # X25519 private (32) + Ed25519 private (32), as RNS stores them


class FakeIdentity:
    def __init__(self, create_keys: bool = True):
        self._prv = os.urandom(KEY_BYTES) if create_keys else None
        self.hash = hashlib.sha256(self._prv).digest()[:16] if self._prv else None

    def get_private_key(self) -> bytes:
        return self._prv

    @classmethod
    def from_file(cls, path):
        # Real semantics: any failure -> None, never an exception.
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except Exception:
            return None
        if len(data) != KEY_BYTES:
            return None
        ident = cls(create_keys=False)
        ident._prv = data
        ident.hash = hashlib.sha256(data).digest()[:16]
        return ident

    def to_file(self, path):
        # Real semantics: plain open("wb") — NOT atomic, umask mode.
        with open(path, "wb") as fh:
            fh.write(self._prv)
        return True


def make_identity_class():
    return type("Identity", (FakeIdentity,), {})
