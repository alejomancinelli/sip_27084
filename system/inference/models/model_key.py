"""
De dónde sale la clave con la que se abren los pesos cifrados.

No conoce el formato del archivo —eso es `encrypted_weights`— ni lee ningún archivo:
devuelve 32 bytes, o None cuando este build no tiene con qué abrir nada.

**Una clave por fork.** Cada instalación ya se compila para su cliente, así que una clave
por fork sale casi gratis, y la propiedad que da es la que importa: una clave sacada del
binario de un cliente expone el modelo de ese cliente y de ningún otro. Con una sola
clave para todas las entregas, un solo binario comprometido las expone todas. La custodia
es del repositorio de firma, que ya guarda la clave privada de las licencias y lleva el
registro por cliente; no hace falta inventar otro lugar con esa disciplina.

**La clave no está escrita en ningún lado como 32 bytes seguidos**, porque un blob así se
encuentra con un volcado de strings incluso en código compilado. Se deriva en tiempo de
ejecución con HKDF-SHA256 sobre fragmentos separados más el salt del fork, de modo que no
haya un único valor que buscar.

Ser honestos con lo que eso es: **sube el costo, no cierra la puerta.** Alguien con
oficio de ingeniería inversa la recupera igual. Lo que evita es el caso fácil, que es el
que va a pasar.

**El módulo con los fragmentos lo genera el build** en `_model_key.py`, está en el
`.gitignore` y nunca se commitea, igual que la clave privada de la firma. Su forma es:

    KEY_LABEL = "acme-linea3-1"     # sólo para el log; no es un secreto
    SALT = b"...16 bytes..."
    FRAGMENTS = (b"...", b"...", b"...")

**Sin ese módulo el desarrollo no cambia**: los pesos en claro cargan igual y los
cifrados fallan con el motivo, que es exactamente lo que corresponde en un equipo de
desarrollo que no tiene por qué manejar claves de nadie.
"""

from system.logger import logger

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:  # pragma: no cover — depende del entorno, no de la lógica
    HKDF = None
    hashes = None

try:
    from system.inference.models import _model_key as _fork_key
except ImportError:
    _fork_key = None

from .encrypted_weights import KEY_SIZE

# Etiqueta del propósito: entra en la derivación para que la misma semilla nunca produzca
# la clave de otra cosa. Cambiarla invalida todos los archivos ya cifrados.
_HKDF_INFO = b"cv-template/model-weights/v1"

_NO_KEY_LABEL = "sin clave"


def has_key() -> bool:
    """True si este build puede abrir pesos cifrados."""
    return get_model_key() is not None


def get_key_label() -> str:
    """
    Nombre de la clave que trae el build, para el log y el soporte.

    Es una etiqueta y no un secreto: sirve para saber por teléfono si el equipo tiene la
    clave que le corresponde, sin que nadie tenga que leer bytes.
    """
    if _fork_key is None:
        return _NO_KEY_LABEL
    return str(getattr(_fork_key, "KEY_LABEL", "") or _NO_KEY_LABEL)


def get_model_key() -> bytes | None:
    """
    Deriva la clave de los pesos de este fork, o None si este build no trae ninguna.

    No se cachea a propósito: se llama una vez por carga de modelo, derivar cuesta
    microsegundos, y dejar la clave viviendo en un global del proceso sería regalar el
    único valor que el esquema trata de no tener escrito en ninguna parte.
    """
    if _fork_key is None or HKDF is None:
        return None

    material = _read_material()
    if material is None:
        return None

    salt, fragments = material
    return HKDF(algorithm=hashes.SHA256(), length=KEY_SIZE, salt=salt,
                info=_HKDF_INFO).derive(fragments)


def _read_material() -> tuple | None:
    """
    Salt y fragmentos del módulo generado, o None si no tienen la forma esperada.

    Un módulo generado a medias se trata como ausente: derivar igual daría una clave que
    no abre nada, y el motivo que se leería sería «clave equivocada», que manda a buscar
    el problema al lado equivocado.
    """
    salt = getattr(_fork_key, "SALT", None)
    fragments = getattr(_fork_key, "FRAGMENTS", None)

    if not isinstance(salt, (bytes, bytearray)) or not salt:
        logger.error("[Inference] El módulo de clave de modelo no declara un SALT válido.")
        return None
    if not isinstance(fragments, (tuple, list)) or not fragments:
        logger.error("[Inference] El módulo de clave de modelo no declara FRAGMENTS.")
        return None
    if not all(isinstance(fragment, (bytes, bytearray)) and fragment for fragment in fragments):
        logger.error("[Inference] Los FRAGMENTS de la clave de modelo no son bytes.")
        return None

    return bytes(salt), b"".join(bytes(fragment) for fragment in fragments)
