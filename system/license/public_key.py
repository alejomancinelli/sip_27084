"""
Claves públicas con las que se verifican las licencias.

Módulo puro: sin Qt, sin I/O, sin ConfigManager. Es una tabla y nada más.

**Acá va sólo la mitad pública.** La privada vive en el repositorio de firma, que no se
forkea ni se entrega; si alguna vez aparece una clave privada en este archivo, el esquema
completo dejó de servir en ese commit y hay que rotar.

**Es un mapa `key_id -> clave` y no una clave fija, aunque casi siempre tenga una sola
entrada.** Cada licencia declara con cuál se verifica, y esa indirección es lo único que
hace posible rotar: con la clave escrita como un valor único, cambiarla dejaría afuera el
mismo día a todas las plantas que todavía tienen su licencia vieja.

**Esa entrada única es la operación normal.** No se emite una clave por proyecto ni una
por año: lo que ata la licencia a una instalación son el fingerprint y el `project_id`,
que van firmados adentro y sí se verifican, no el nombre de la clave. Una clave por proyecto
multiplicaría los secretos a custodiar sin aislar nada, porque todos vivirían en el mismo
repositorio de firma. La segunda clave aparece por un incidente, o porque empieza a
firmar otra organización.

**Rotar es de dos etapas, y la segunda es la que cierra el agujero.** Agregar la clave
nueva no revoca nada: mientras la comprometida siga en esta tabla, el que la robó puede
seguir firmando licencias que este binario acepta.

    1. Se publica un build con las DOS claves y se reemite a todas las plantas con el
       `key_id` nuevo. Nadie se queda afuera, porque las licencias viejas todavía validan.
    2. Cuando no queda ninguna licencia viva con la clave vieja, se publica un build con
       la vieja BORRADA de esta tabla. Recién ahí la clave robada deja de servir.

Entre las dos etapas el esquema está comprometido y hay poco que hacer al respecto: por
eso la etapa 2 se planifica junto con la 1 y no «cuando haya tiempo».

Formato de cada valor: los 32 bytes de la clave pública Ed25519 en base64 URL-safe sin
relleno, tal como los imprime el generador del repo de firma.

**La tabla vacía es un estado válido y significa que nada verifica**: todas las licencias
quedan inválidas, con el motivo en el log. Es lo que corresponde en el template, donde
todavía no hay par de claves; un build entregado con esta tabla vacía tiene el problema
del otro lado —una licencia legítima que no valida—, y por eso el motivo lo dice con
todas las letras en vez de hablar de firmas.
"""

from system.license import schema

# `key_id` -> clave pública Ed25519 en base64url. El id es un contador y nada más: no
# lleva año, país ni proyecto, porque el verificador sólo lo usa para elegir con qué clave
# probar la firma. Un `iea-2026-ar` daría por válida una licencia de cualquier otro año y
# de cualquier otro país, así que sería un nombre que miente. Lo único que importa es que
# no se repita nunca: queda escrito en cada licencia emitida bajo él.
PUBLIC_KEYS: dict = {
    # "iea-1": "reemplazar-por-la-clave-publica-del-repo-de-firma",
}

_EXPECTED_KEY_LENGTH = 32   # bytes de una clave pública Ed25519


def get_public_key(key_id: str) -> bytes | None:
    """
    Devuelve los bytes de la clave pública, o None si el `key_id` no está en la tabla.

    Una clave con largo equivocado se trata como ausente: es un error de tipeo al pegarla,
    y dejarla pasar terminaría en un mensaje de firma inválida que manda a buscar el
    problema al lado equivocado.
    """
    encoded = PUBLIC_KEYS.get(str(key_id or ""))
    if not encoded:
        return None

    try:
        raw = schema.decode_b64url(str(encoded))
    except schema.LicenseFormatError:
        return None

    return raw if len(raw) == _EXPECTED_KEY_LENGTH else None


def has_any_key() -> bool:
    """True si el binario trae al menos una clave utilizable."""
    return any(get_public_key(key_id) for key_id in PUBLIC_KEYS)
