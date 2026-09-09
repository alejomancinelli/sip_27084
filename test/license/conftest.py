"""
Par de claves de prueba y config de mentira, para no depender de la máquina que corre.

Ed25519 se genera en microsegundos, así que cada corrida firma con una clave nueva: no
hay ninguna clave de prueba versionada que alguien pueda confundir con una de verdad.

La clave pública se mete en la tabla de `public_key` sólo mientras dura el test y se saca
al terminar, para que un test no pueda dejar el proceso con una clave puesta.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from system.license import fingerprint, public_key, schema

TEST_KEY_ID = "test-key"
TEST_CLIENT = "ACME"
TEST_PROJECT_ID = "LINEA_3"

# Huella sintética: tres fuentes, ninguna leída del equipo que corre los tests.
TEST_COMPONENTS = {
    fingerprint.SOURCE_BOARD_UUID: fingerprint.hash_value("board_uuid", "UUID-DE-PRUEBA-0001"),
    fingerprint.SOURCE_BOARD_SERIAL: fingerprint.hash_value("board_serial", "BOARD-0001"),
    fingerprint.SOURCE_DISK_SERIAL: fingerprint.hash_value("disk_serial", "DISK-0001"),
}


class FakeConfig:
    """Lo único que el manager le pide a la config es `get()` con ruta punteada."""

    def __init__(self, values: dict | None = None):
        self._values = values or {
            "project.client": TEST_CLIENT,
            "project.project_id": TEST_PROJECT_ID,
        }

    def get(self, key_path: str, default: object = None) -> object:
        return self._values.get(key_path, default)


@pytest.fixture
def sign_license():
    """
    Devuelve `sign(**overrides) -> token`, con una licencia válida por defecto.

    Los overrides son campos del payload: `sign(expires_at="2020-01-01")` emite una
    vencida sin tener que repetir los otros diez campos.
    """
    private_key = Ed25519PrivateKey.generate()
    raw_public = private_key.public_key().public_bytes_raw()
    public_key.PUBLIC_KEYS[TEST_KEY_ID] = schema.encode_b64url(raw_public)

    def sign(**overrides) -> str:
        payload = build_payload(**overrides)
        payload_bytes = schema.to_canonical_json(payload)
        return schema.build_token(payload_bytes, private_key.sign(payload_bytes))

    yield sign

    public_key.PUBLIC_KEYS.pop(TEST_KEY_ID, None)


def build_payload(**overrides) -> dict:
    """Payload de una licencia perpetua, válida y atada a `TEST_COMPONENTS`."""
    entitlements = {
        "max_cameras": 2,
        "features": ["core"],
        "model_hashes": {},
    }
    entitlements.update(overrides.pop("entitlements", {}))

    fingerprint_block = {
        "components": dict(TEST_COMPONENTS),
        "min_matches": 2,
    }
    fingerprint_block.update(overrides.pop("fingerprint", {}))

    payload = {
        "schema": schema.SCHEMA_VERSION,
        "license_id": "TEST-0001",
        "key_id": TEST_KEY_ID,
        "product": "cv_projects_template",
        "client": TEST_CLIENT,
        "project_id": TEST_PROJECT_ID,
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": None,
        "policy": "warn",
        "fingerprint": fingerprint_block,
        "entitlements": entitlements,
    }
    payload.update(overrides)
    return payload


def corrupt_payload(token: str, **overrides) -> str:
    """Reescribe el payload de un token dejando la firma vieja: la firma no tiene que cerrar."""
    payload_bytes, signature_bytes = schema.split_token(token)
    payload = json.loads(payload_bytes.decode("utf-8"))
    payload.update(overrides)
    return schema.build_token(schema.to_canonical_json(payload), signature_bytes)


def days_from_now(days: int) -> str:
    """Fecha ISO a N días de hoy, para vencimientos que no se pudren con el calendario."""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
