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

**Antes de verificar, la librería se controla a sí misma.** En el build acelerado de
`build.py` `cryptography` no se compila —Nuitka carga vacía su extensión de Rust— y su capa
de Python queda editable al lado del ejecutable. Por eso cada verificación prueba primero un
vector conocido de la RFC 8032: la firma tiene que cerrar sobre su mensaje y no cerrar sobre
otro. Un `verify()` vaciado acepta las dos, y entonces se rechaza la licencia. Encarece el
ataque de un rato; quien lea el binario lo esquiva igual, y no pretende otra cosa.

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

# Vector 1 de la RFC 8032: un par público y conocido, ajeno a las licencias.
_CANARY_PUBLIC_KEY = bytes.fromhex(
    "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
_CANARY_MESSAGE = b""
_CANARY_SIGNATURE = bytes.fromhex(
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065"
    "224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")


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
    if not _is_verifier_sound():
        raise LicenseSignatureError(
            "La verificación de firmas no pasa su autocontrol: la instalación fue modificada."
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


def _is_verifier_sound() -> bool:
    """
    True si la librería acepta la firma del vector y la rechaza sobre otro mensaje.

    Cualquier otra excepción también es un verificador que no responde como Ed25519: se
    contesta False y no se la deja salir, porque el manager sólo espera los errores de
    licencia.
    """
    try:
        key = Ed25519PublicKey.from_public_bytes(_CANARY_PUBLIC_KEY)
        key.verify(_CANARY_SIGNATURE, _CANARY_MESSAGE)
    except Exception:
        return False
    try:
        key.verify(_CANARY_SIGNATURE, _CANARY_MESSAGE + b"\x00")
    except InvalidSignature:
        return True
    except Exception:
        return False
    return False
