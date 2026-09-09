"""
Formato del archivo de licencia: qué campos tiene y cómo se serializa.

Módulo puro: sin Qt, sin I/O, sin ConfigManager. No verifica firmas —eso es `verify.py`—
ni decide qué habilita cada campo —eso es `manager.py`—. Acá sólo se define la forma, y
es el único archivo que la conoce.

El token son dos partes separadas por un punto, igual que un JWT y por la misma razón: se
lee con un `cat`, viaja por mail sin que un cliente de correo lo rompa, y el payload se
inspecciona sin tener la clave privada.

    {payload_b64url}.{signature_b64url}

**La firma se verifica sobre los bytes del payload tal como viajan**, no sobre el dict
reserializado de este lado. Así el verificador no depende de que su `json.dumps` produzca
exactamente lo mismo que el del firmante, que es la forma clásica de que una licencia
correcta deje de validar al cambiar de versión de Python. El JSON canónico —claves
ordenadas, sin espacios, ASCII— es una convención del firmante para que dos emisiones
consecutivas se puedan comparar con un diff, no un requisito de corrección.

Los campos, y quién los mira:

    schema          versión del formato; una desconocida se rechaza en vez de adivinar
    license_id      identificador de la emisión, para soporte y para el registro
    key_id          con qué clave pública se verifica. Sin esto, rotar la clave privada
                    obliga a reflashear cada planta
    product         informativo, para el registro del emisor
    client          nombre del cliente, tal como está en `project.client`
    project_id      identificador de la instalación, tal como está en `project.project_id`
    issued_at       cuándo se emitió
    expires_at      instante de vencimiento; `null` = perpetua
    policy          qué hace el programa si la licencia no vale (ver `policy.py`)
    fingerprint     `components` con un hash por fuente y `min_matches`, el N-de-M
    entitlements    `max_cameras`, `features` y `model_hashes`

**`client` y `project_id` no son un control de seguridad.** El que ata el equipo es el
fingerprint: los dos nombres salen de `config.yaml`, que el cliente edita. Están para que
mandar el archivo equivocado se note el primer día, que es el error que de verdad pasa.

Una fecha sin hora —`2027-03-31`— vence al final de ese día en UTC: una licencia anual se
vende por día y no por instante, y hacerla vencer a la medianoche del día anterior
sorprende a todo el mundo.
"""

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timezone

from system.license import policy

#: Versión del formato. Se sube cuando cambia el significado de un campo existente.
SCHEMA_VERSION = 1

_TOKEN_SEPARATOR = "."
_B64URL_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


class LicenseFormatError(Exception):
    """El archivo no tiene la forma de una licencia: no es un problema de firma."""


@dataclass(frozen=True)
class License:
    """Una licencia ya parseada. No dice si es válida: dice qué declara."""

    license_id: str
    key_id: str
    product: str
    client: str
    project_id: str
    issued_at: datetime
    expires_at: datetime | None
    policy: str
    fingerprint: dict = field(default_factory=dict)
    min_matches: int = 0
    max_cameras: int | None = None
    features: tuple = ()
    model_hashes: dict = field(default_factory=dict)

    @property
    def is_perpetual(self) -> bool:
        return self.expires_at is None


def split_token(token: str) -> tuple:
    """
    Parte el token en `(payload_bytes, signature_bytes)`.

    Los bytes del payload son los que se firmaron: se devuelven tal como venían.
    """
    text = str(token or "").strip()
    if not text:
        raise LicenseFormatError("El archivo de licencia está vacío.")

    parts = text.split(_TOKEN_SEPARATOR)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise LicenseFormatError(
            "El archivo no tiene la forma '{payload}.{firma}'. ¿Se copió completo?"
        )

    return decode_b64url(parts[0]), decode_b64url(parts[1])


def build_token(payload_bytes: bytes, signature_bytes: bytes) -> str:
    """Arma el token a partir del payload firmado y su firma."""
    return f"{encode_b64url(payload_bytes)}{_TOKEN_SEPARATOR}{encode_b64url(signature_bytes)}"


def read_key_id(payload_bytes: bytes) -> str:
    """
    Devuelve el `key_id` del payload SIN verificar nada.

    Leerlo antes de verificar es seguro porque lo único que decide es con qué clave
    pública se prueba la firma: un `key_id` falsificado elige una clave con la que la
    firma no cierra, o ninguna.
    """
    payload = _load_payload(payload_bytes)
    return str(payload.get("key_id") or "")


def parse_payload(payload_bytes: bytes) -> License:
    """Convierte el payload en una `License`, o levanta `LicenseFormatError`."""
    payload = _load_payload(payload_bytes)

    schema = payload.get("schema")
    if schema != SCHEMA_VERSION:
        raise LicenseFormatError(
            f"Formato de licencia versión {schema!r}; este programa entiende "
            f"la {SCHEMA_VERSION}. Hace falta una versión más nueva del software."
        )

    fingerprint = _require_dict(payload, "fingerprint")
    components = _require_dict(fingerprint, "components")
    entitlements = _require_dict(payload, "entitlements")

    for source, digest in components.items():
        if not isinstance(source, str) or not isinstance(digest, str) or not digest:
            raise LicenseFormatError("La huella tiene un componente que no es texto.")

    min_matches = _require_int(fingerprint, "min_matches", minimum=0)
    if min_matches > len(components):
        raise LicenseFormatError(
            f"La licencia exige {min_matches} coincidencias de huella pero declara "
            f"{len(components)} componentes: no puede validar en ninguna máquina."
        )

    max_cameras = entitlements.get("max_cameras")
    if max_cameras is not None:
        max_cameras = _require_int(entitlements, "max_cameras", minimum=1)

    features = entitlements.get("features") or []
    if not isinstance(features, list) or any(not isinstance(name, str) for name in features):
        raise LicenseFormatError("`features` tiene que ser una lista de nombres.")

    model_hashes = entitlements.get("model_hashes") or {}
    if not isinstance(model_hashes, dict):
        raise LicenseFormatError("`model_hashes` tiene que ser un mapa slot -> sha256.")

    return License(
        license_id=_require_str(payload, "license_id"),
        key_id=_require_str(payload, "key_id"),
        product=str(payload.get("product") or ""),
        client=str(payload.get("client") or ""),
        project_id=str(payload.get("project_id") or ""),
        issued_at=_parse_instant(payload.get("issued_at"), "issued_at", end_of_day=False),
        expires_at=_parse_instant(payload.get("expires_at"), "expires_at", end_of_day=True),
        policy=policy.normalize(payload.get("policy")),
        fingerprint=dict(components),
        min_matches=min_matches,
        max_cameras=max_cameras,
        features=tuple(features),
        model_hashes={str(slot): str(digest) for slot, digest in model_hashes.items()},
    )


def to_canonical_json(payload: dict) -> bytes:
    """
    Serializa el payload como lo hace el firmante: claves ordenadas, sin espacios, ASCII.

    Está acá y no en el repo de firma porque el formato tiene un solo dueño, y porque los
    tests firman licencias sintéticas con exactamente esta función.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def encode_b64url(raw: bytes) -> str:
    """Base64 URL-safe sin relleno: sobrevive a un mail y a un nombre de archivo."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_b64url(text: str) -> bytes:
    """
    Inversa de `encode_b64url`; levanta `LicenseFormatError` si no es base64url.

    Los espacios y saltos de línea se sacan antes de mirar: un cliente de correo que corta
    el token en líneas de 76 caracteres es lo más común que le puede pasar a un archivo
    que viaja por mail, y eso se arregla acá y no en soporte. Cualquier otro carácter sí
    es un error, y se avisa como tal: `base64` por default descarta en silencio lo que no
    entiende, así que sin esta validación un archivo pisado decodificaría a basura y el
    motivo que leería el cliente sería «la firma no cierra».
    """
    compact = re.sub(r"\s+", "", str(text or ""))
    if not compact or not _B64URL_PATTERN.fullmatch(compact):
        raise LicenseFormatError("El archivo tiene caracteres que no son base64url. "
                                 "¿Se copió completo y sin modificar?")

    padded = compact + "=" * (-len(compact) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as e:
        raise LicenseFormatError(f"El archivo no se pudo decodificar: {e}") from e


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_payload(payload_bytes: bytes) -> dict:
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise LicenseFormatError(f"El payload no es JSON válido: {e}") from e

    if not isinstance(payload, dict):
        raise LicenseFormatError("El payload tiene que ser un objeto JSON.")
    return payload


def _require_str(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LicenseFormatError(f"Falta el campo obligatorio '{key}'.")
    return value.strip()


def _require_dict(payload: dict, key: str) -> dict:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise LicenseFormatError(f"El campo '{key}' tiene que ser un objeto.")
    return value


def _require_int(payload: dict, key: str, *, minimum: int) -> int:
    value = payload.get(key)
    # Un bool es un int para Python y acá no lo es: `True` como cupo de cámaras sería 1.
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LicenseFormatError(f"El campo '{key}' tiene que ser un entero >= {minimum}.")
    return value


def _parse_instant(value: object, key: str, *, end_of_day: bool) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LicenseFormatError(f"El campo '{key}' tiene que ser una fecha ISO o null.")

    text = value.strip()
    # `fromisoformat` de Python 3.10 no acepta la Z de Zulu, que es como la escribe
    # cualquier emisor sensato.
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise LicenseFormatError(f"El campo '{key}' no es una fecha ISO: {value!r}") from e

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    # Una fecha sola vence al final de ese día: la licencia se vende por día.
    if end_of_day and len(text) == 10:
        parsed = datetime.combine(parsed.date(), time(23, 59, 59), tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)
