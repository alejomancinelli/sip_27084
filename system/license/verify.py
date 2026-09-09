"""
Verificación de la firma Ed25519 de una licencia.

Módulo puro salvo por la librería de criptografía: sin Qt, sin ConfigManager, sin I/O.
No lee el archivo —eso es del `manager`— ni interpreta los campos —eso es `schema`—:
recibe un token y contesta si la firma cierra.

**Sin `cryptography` instalada, nada verifica.** El repo tiene la regla de que un módulo
al que le falta su librería degrada a no-op y expone la misma API; para un verificador,
«no-op» sería aceptar cualquier cosa, así que acá la degradación es al revés: sin la
librería, toda licencia queda rechazada con el motivo explícito. Un candado que no puede
cerrar tiene que quedar cerrado, no abierto.

Ed25519 y no RSA: la clave pública son 32 bytes que entran en una línea del código, la
firma son 64, y no hay parámetros que elegir mal.
"""

from system.license import public_key, schema

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover — depende del entorno, no de la lógica
    Ed25519PublicKey = None
    InvalidSignature = Exception


class LicenseSignatureError(Exception):
    """La firma no cierra, o no hay con qué verificarla."""


def is_available() -> bool:
    """True si el entorno tiene la librería de criptografía."""
    return Ed25519PublicKey is not None


def verify_token(token: str) -> bytes:
    """
    Devuelve los bytes del payload sólo si la firma del token cierra.

    Levanta `LicenseSignatureError` si no hay librería, si el `key_id` no está en la
    tabla de claves o si la firma no corresponde; `schema.LicenseFormatError` si el token
    ni siquiera tiene la forma de una licencia.
    """
    payload_bytes, signature_bytes = schema.split_token(token)

    if not is_available():
        raise LicenseSignatureError(
            "Falta la librería 'cryptography': este build no puede verificar licencias."
        )

    key_id = schema.read_key_id(payload_bytes)
    if not key_id:
        raise LicenseSignatureError("La licencia no dice con qué clave se firmó (key_id).")

    raw_key = public_key.get_public_key(key_id)
    if raw_key is None:
        if not public_key.has_any_key():
            raise LicenseSignatureError(
                "Este build no trae ninguna clave pública: no puede validar licencias."
            )
        raise LicenseSignatureError(
            f"La licencia se firmó con la clave '{key_id}', que este build no conoce. "
            f"Hace falta una versión más nueva del software."
        )

    try:
        Ed25519PublicKey.from_public_bytes(raw_key).verify(signature_bytes, payload_bytes)
    except InvalidSignature as e:
        raise LicenseSignatureError("La firma no corresponde al contenido de la licencia.") from e
    except ValueError as e:
        raise LicenseSignatureError(f"La firma no tiene la forma esperada: {e}") from e

    return payload_bytes
