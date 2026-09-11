"""
Formato del contenedor de pesos cifrados: qué bytes tiene el archivo y cómo se abre.

Módulo puro salvo por la librería de criptografía: sin Qt, sin ConfigManager, sin I/O.
No lee archivos —eso es de `AbstractModel`— ni sabe de dónde sale la clave —eso es
`model_key`—: recibe bytes y devuelve bytes. Es el único archivo del repo que conoce
este formato, y el que lo escribe en el repositorio de firma tiene que producir
exactamente estos bytes.

**Cifrado autenticado (AES-256-GCM) y no hash + cifrado por separado.** El tag de GCM da
confidencialidad e integridad en una sola pasada: si descifra, el archivo es auténtico y
está íntegro; si le cambiaron un bit, no descifra. No hay ningún hash que guardar, ni
manifiesto que mantener, ni nada que comparar.

Qué protege y qué no: **los pesos en claro existen en la memoria del equipo mientras el
modelo infiere**, porque para inferir hay que descifrarlos. Quien enganche un debugger y
vuelque el buffer los obtiene. Lo que esto termina por completo es la extracción casual
—ya no hay un `.pt` para arrastrar a un pendrive—, que es la amenaza realista.

Disposición del archivo, en este orden:

    offset  bytes  campo
    0       8      magic       identifica el formato; es lo que distingue un archivo
                               cifrado de unos pesos en claro
    8       1      version     para poder cambiar el formato sin romper lo entregado
    9       12     nonce       único por archivo, al azar
    21      resto  ciphertext  el texto cifrado, con el tag de 16 bytes pegado al final

Los 21 bytes del encabezado entran como datos autenticados (AAD) del propio GCM, así que
tampoco se pueden editar: cambiar la versión declarada rompe el tag.

Adentro del texto cifrado va **la metadata junto con los pesos**, y por eso también queda
protegida:

    4 bytes big-endian  largo del JSON de metadata
    ese JSON en UTF-8   los nombres de clase, el umbral, la tarea
    resto               los pesos, tal cual salieron del framework

Que la metadata viaje adentro es lo que evita que el `config.yaml` —un archivo de texto
al lado del ejecutable— cuente qué clases detecta el modelo y con qué umbral.

**El nonce no se reusa nunca con la misma clave**: se genera al azar en cada `pack()`.
Reusarlo con GCM rompe la confidencialidad de los dos archivos, y es el error clásico de
estos esquemas.

**Sin `cryptography` instalada no se abre nada.** Misma degradación que el verificador de
licencias y por el mismo motivo: para un candado, «no-op» sería quedar abierto.
"""

import json
import os
from dataclasses import dataclass, field

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover — depende del entorno, no de la lógica
    AESGCM = None
    InvalidTag = Exception

#: Identifica el formato. Ocho bytes ASCII: son los que se ven con un `head -c 8`.
MAGIC = b"IEAWGTS\x00"

#: Versión del formato. Se sube cuando cambia el significado de un campo existente.
FORMAT_VERSION = 1

KEY_SIZE = 32       # AES-256
NONCE_SIZE = 12     # el tamaño que recomienda GCM; otro obliga a rehashear el nonce
TAG_SIZE = 16       # va pegado al final del texto cifrado
HEADER_SIZE = len(MAGIC) + 1 + NONCE_SIZE

#: Sufijo con el que se nombra un archivo protegido (`best.engine` -> `best.engine.enc`),
#: para que se vea de un vistazo qué está cifrado. El formato NO se detecta por el
#: nombre: se detecta por el magic, así que renombrarlo no engaña a nadie.
ENCRYPTED_SUFFIX = ".enc"

_META_LENGTH_SIZE = 4
_MAX_METADATA_SIZE = 1 << 20    # 1 MB: la metadata son nombres y umbrales, no un modelo


class WeightsError(Exception):
    """Los pesos no se pudieron abrir. Las dos subclases dicen de qué lado está el problema."""


class WeightsFormatError(WeightsError):
    """El archivo no tiene la forma del contenedor: no es un problema de clave."""


class WeightsDecryptError(WeightsError):
    """El contenedor está bien formado pero no abre: clave equivocada o archivo alterado."""


@dataclass(frozen=True)
class WeightsBundle:
    """
    Lo que sale de un archivo de pesos: los bytes para el framework y su metadata.

    `metadata` viene vacía cuando los pesos están en claro —un archivo suelto no declara
    nada— y ése es el caso de desarrollo.
    """

    weights: bytes
    metadata: dict = field(default_factory=dict)
    is_protected: bool = False      # si salió de un contenedor cifrado o de un archivo suelto


def is_available() -> bool:
    """True si el entorno tiene la librería de criptografía."""
    return AESGCM is not None


def is_encrypted(raw: bytes) -> bool:
    """True si estos bytes son un contenedor de este formato, mirando sólo el magic."""
    return len(raw) >= len(MAGIC) and raw[:len(MAGIC)] == MAGIC


def pack(weights: bytes, key: bytes, metadata: dict | None = None) -> bytes:
    """
    Arma el contenedor cifrado con estos pesos y esta metadata.

    La clave son 32 bytes; el nonce se genera acá y no se recibe, justamente para que no
    se pueda reusar por error.
    """
    _require_library()
    _verify_key(key)

    meta_bytes = _encode_metadata(metadata)
    header = MAGIC + bytes([FORMAT_VERSION]) + os.urandom(NONCE_SIZE)
    plaintext = len(meta_bytes).to_bytes(_META_LENGTH_SIZE, "big") + meta_bytes + weights
    nonce = header[len(MAGIC) + 1:]

    return header + AESGCM(key).encrypt(nonce, plaintext, header)


def unpack(raw: bytes, key: bytes | None = None) -> WeightsBundle:
    """
    Devuelve los pesos y su metadata; unos pesos en claro salen tal cual, sin clave.

    Que el archivo sin cifrar pase de largo es lo que deja andar el desarrollo sin clave
    ni configuración: la protección aparece en el build entregado, no estorba antes.

    Levanta `WeightsFormatError` si el contenedor está cortado o declara una versión que
    este build no conoce, y `WeightsDecryptError` si no hay clave con qué abrirlo o si no
    abre con la que hay.
    """
    if not is_encrypted(raw):
        return WeightsBundle(weights=raw)

    _require_library()
    version = _read_version(raw)
    if version != FORMAT_VERSION:
        raise WeightsFormatError(
            f"Los pesos están en un formato de versión {version} y este programa "
            f"entiende la {FORMAT_VERSION}. Hace falta una versión más nueva del software."
        )
    if len(raw) < HEADER_SIZE + TAG_SIZE:
        raise WeightsFormatError("El archivo de pesos está cortado: no llega ni al tag.")

    if key is None:
        raise WeightsDecryptError(
            "Los pesos están cifrados y este build no trae la clave para abrirlos."
        )
    _verify_key(key)

    header = raw[:HEADER_SIZE]
    nonce = header[len(MAGIC) + 1:]
    try:
        plaintext = AESGCM(key).decrypt(nonce, raw[HEADER_SIZE:], header)
    except InvalidTag as e:
        raise WeightsDecryptError(
            "Los pesos no abren con la clave de este programa: o están cifrados con otra "
            "clave, o el archivo fue alterado."
        ) from e

    return _split_plaintext(plaintext)


# ── Internos ─────────────────────────────────────────────────────────────────

def _require_library():
    if not is_available():
        raise WeightsDecryptError(
            "Falta la librería 'cryptography': este build no puede abrir pesos cifrados."
        )


def _verify_key(key: bytes):
    if not isinstance(key, (bytes, bytearray)) or len(key) != KEY_SIZE:
        raise WeightsDecryptError(
            f"La clave de los pesos tiene que ser de {KEY_SIZE} bytes."
        )


def _read_version(raw: bytes) -> int:
    if len(raw) < len(MAGIC) + 1:
        raise WeightsFormatError("El archivo de pesos está cortado: no trae ni la versión.")
    return raw[len(MAGIC)]


def _encode_metadata(metadata: dict | None) -> bytes:
    """Serializa la metadata a JSON canónico, para que dos cifrados se puedan comparar."""
    if not metadata:
        return b""
    try:
        meta_bytes = json.dumps(dict(metadata), sort_keys=True,
                                separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    except (TypeError, ValueError) as e:
        raise WeightsFormatError(f"La metadata no es serializable a JSON: {e}") from e
    if len(meta_bytes) > _MAX_METADATA_SIZE:
        raise WeightsFormatError(
            f"La metadata pesa {len(meta_bytes)} bytes y el máximo es {_MAX_METADATA_SIZE}."
        )
    return meta_bytes


def _split_plaintext(plaintext: bytes) -> WeightsBundle:
    """Separa metadata y pesos del texto ya descifrado y autenticado."""
    if len(plaintext) < _META_LENGTH_SIZE:
        raise WeightsFormatError(
            "El contenido descifrado está cortado: falta el largo de la metadata."
        )

    meta_length = int.from_bytes(plaintext[:_META_LENGTH_SIZE], "big")
    meta_end = _META_LENGTH_SIZE + meta_length
    if meta_length > _MAX_METADATA_SIZE or meta_end > len(plaintext):
        raise WeightsFormatError("El largo de metadata declarado no entra en el archivo.")

    return WeightsBundle(
        weights=plaintext[meta_end:],
        metadata=_decode_metadata(plaintext[_META_LENGTH_SIZE:meta_end]),
        is_protected=True,
    )


def _decode_metadata(meta_bytes: bytes) -> dict:
    if not meta_bytes:
        return {}
    try:
        metadata = json.loads(meta_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise WeightsFormatError(f"La metadata de los pesos no es JSON válido: {e}") from e
    if not isinstance(metadata, dict):
        raise WeightsFormatError("La metadata de los pesos tiene que ser un objeto JSON.")
    return metadata
