"""Tests del autocontrol del verificador: una librería editada no puede aceptar licencias.

En el build acelerado `cryptography` va suelta y se puede editar. Lo que se fija acá es
que un `verify()` que ya no verifica se detecta antes de mirar la licencia, y que la
librería de verdad pasa el control sin que ninguna licencia legítima lo note.
"""

import pytest
from cryptography.exceptions import InvalidSignature

from system.license import verify


class HollowKey:
    """Lo que deja un `verify()` vaciado: no levanta nunca."""

    @classmethod
    def from_public_bytes(cls, raw_key: bytes) -> "HollowKey":
        return cls()

    def verify(self, signature: bytes, data: bytes):
        return None


class RejectingKey(HollowKey):
    """Una librería rota del otro lado: no acepta ni una firma buena."""

    def verify(self, signature: bytes, data: bytes):
        raise InvalidSignature()


def test_the_real_library_passes_the_canary():
    assert verify._is_verifier_sound()


def test_a_legitimate_license_still_verifies(sign_license):
    assert verify.verify_token(sign_license())


@pytest.mark.parametrize("fake_key", [HollowKey, RejectingKey])
def test_a_tampered_verifier_rejects_every_license(monkeypatch, sign_license, fake_key):
    """Un verificador vaciado aceptaría la firma de cualquiera: la licencia no pasa."""
    token = sign_license()
    monkeypatch.setattr(verify, "Ed25519PublicKey", fake_key)
    with pytest.raises(verify.LicenseSignatureError, match="autocontrol"):
        verify.verify_token(token)
