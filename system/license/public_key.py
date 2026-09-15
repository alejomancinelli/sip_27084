"""
Claves públicas con las que se verifican las licencias: resuelve `key_id -> clave`.

Módulo puro: sin Qt, sin I/O, sin ConfigManager. Es una tabla y la puerta para leerla.

**Acá va sólo la mitad pública.** La privada vive en el repositorio de firma, que no se
forkea ni se entrega; si alguna vez aparece una clave privada en este archivo, el esquema
completo dejó de servir en ese commit y hay que rotar.

**La clave de un build entregado no se escribe acá: la genera el repositorio de firma**
en `_public_key.py`, que está en el `.gitignore` y nunca se commitea, igual que el
`_model_key.py` de los pesos. Ese módulo guarda cada clave partida en fragmentos
enmascarados y la rearma recién al llamarla, así en el binario no queda ningún base64 de
43 caracteres que se encuentre con un volcado de strings y se reemplace por otro del
mismo largo —que es el ataque de un rato, no de una semana—. El build lo escribe antes de
compilar y lo borra después:

    python -m licensing keymodule --out <fork>/system/license/_public_key.py

**La tabla de este archivo queda vacía y es lo correcto.** Un checkout de fuentes no
verifica ninguna licencia y no tiene por qué —desde fuentes el estado es
`unlicensed_build`—, y los tests inyectan acá su clave sintética mientras duran. Un build
entregado que no encuentre ninguna clave tiene el problema del otro lado —una licencia
legítima que no valida— y por eso el motivo lo dice con todas las letras en vez de hablar
de firmas.

**Es un mapa `key_id -> clave` y no una clave fija, aunque casi siempre tenga una sola
entrada.** Cada licencia declara con cuál se verifica, y esa indirección es lo único que
hace posible rotar: con la clave escrita como un valor único, cambiarla dejaría afuera el
mismo día a todas las plantas que todavía tienen su licencia vieja.

**Esa entrada única es la operación normal.** No se emite una clave por proyecto ni una
por año: lo que ata la licencia a una instalación son el fingerprint y el `project_id`,
que van firmados adentro y sí se verifican, no el nombre de la clave. Una clave por
proyecto multiplicaría los secretos a custodiar sin aislar nada, porque todos vivirían en
el mismo repositorio de firma. La segunda clave aparece por un incidente, o porque empieza
a firmar otra organización.

Cómo se rota —que es de dos etapas, y la segunda es la que cierra el agujero— está en
`docs/licensing.md`. No está acá a propósito: un procedimiento tiene público propio, y lo
que se compila adentro del binario no tiene por qué explicarle el esquema a quien lo abra
con un editor hexadecimal.

Formato de cada valor de la tabla: los 32 bytes de la clave pública Ed25519 en base64
URL-safe sin relleno.
"""

from system.license import schema

# La tabla del build entregado. Que falte es el caso normal de un checkout de fuentes, y
# por eso se importa así y no con un `os.path.exists`: es el mismo recurso que usa
# `model_key` con su `_model_key`.
try:
    from system.license import _public_key as _generated_keys
except ImportError:
    _generated_keys = None

#: `key_id` -> clave pública Ed25519 en base64url. El id es un contador y nada más: no
#: lleva año, país ni proyecto, porque el verificador sólo lo usa para elegir con qué
#: clave probar la firma. Un `iea-2026-ar` daría por válida una licencia de cualquier otro
#: año y de cualquier otro país, así que sería un nombre que miente. Lo único que importa
#: es que no se repita nunca: queda escrito en cada licencia emitida bajo él.
PUBLIC_KEYS: dict = {}

_EXPECTED_KEY_LENGTH = 32   # bytes de una clave pública Ed25519


def get_public_key(key_id: str) -> bytes | None:
    """
    Devuelve los bytes de la clave pública, o None si el `key_id` no está en ninguna tabla.

    Primero la tabla que generó el build y después la de este archivo: el build entregado
    trae la suya, y un checkout de fuentes sigue pudiendo poner una de prueba sin que
    nadie edite el archivo generado.

    Una clave con largo equivocado se trata como ausente: es un error de tipeo al pegarla,
    y dejarla pasar terminaría en un mensaje de firma inválida que manda a buscar el
    problema al lado equivocado.
    """
    name = str(key_id or "")
    raw = _read_generated_key(name)
    if raw is None:
        raw = _decode_key(PUBLIC_KEYS.get(name))

    return raw if raw is not None and len(raw) == _EXPECTED_KEY_LENGTH else None


def has_any_key() -> bool:
    """True si este build trae al menos una clave utilizable."""
    if _generated_keys is not None and _generated_keys.has_any_key():
        return True
    return any(get_public_key(key_id) for key_id in PUBLIC_KEYS)


# ── Internos ─────────────────────────────────────────────────────────────────

def _read_generated_key(key_id: str) -> bytes | None:
    """Clave de la tabla del build, ya rearmada, o None si no hay módulo o no la tiene."""
    if _generated_keys is None:
        return None
    return _generated_keys.get_public_key(key_id)


def _decode_key(encoded: object) -> bytes | None:
    if not encoded:
        return None
    try:
        return schema.decode_b64url(str(encoded))
    except schema.LicenseFormatError:
        return None
