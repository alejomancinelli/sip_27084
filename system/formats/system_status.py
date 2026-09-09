"""
Palabra de estado del sistema — una sola palabra de 16 bits.

Responde UNA sola pregunta: **¿se puede confiar en las mediciones de proceso?**
Con cualquiera de sus bits en 1, las mediciones no son aptas para control.

Lo que reporta otra palabra no se repite acá: el estado de los canales está en
`com_status` y la salud de cada cámara —sin frames, mal configurada— en
`camera_health`. Lo que queda es lo que **ninguna otra palabra puede expresar**.
Todos los bits tienen en común que el sistema sigue publicando números
verosímiles mientras pasan.

El de licencia entra por esa misma puerta y no por una comercial: con la licencia
caída el equipo puede seguir midiendo y publicando —depende de la política— y los
números que salen no se distinguen de los buenos. Que el integrador lo vea acá es
lo que evita que el PLC los tome por válidos.

Módulo puro: sin Qt, sin Modbus, sin ConfigManager.
"""

from __future__ import annotations

BIT_MODEL_NOT_LOADED = 0   # corriendo sin modelo: los resultados son 0 estructural
BIT_INFERENCE_ERROR  = 1   # el ciclo terminó sin medición usable: lo publicado quedó viejo
BIT_FALLBACK_CONFIG  = 2   # config.yaml ilegible: se está corriendo con la config de rescate
BIT_DEAD_THREAD      = 3   # murió un hilo de trabajo: sus valores quedan congelados
BIT_LICENSE_INVALID  = 4   # licencia ausente, vencida, de otra máquina o con el reloj movido

#: Bits 0-4 en uso; 5-15 libres.
WORD_MASK = 0x001F

DESCRIPTIONS = {
    BIT_MODEL_NOT_LOADED: "Modelo de inferencia no cargado",
    BIT_INFERENCE_ERROR:  "Inferencia sin resultado válido",
    BIT_FALLBACK_CONFIG:  "Corriendo con config de rescate",
    BIT_DEAD_THREAD:      "Murió un hilo de trabajo",
    BIT_LICENSE_INVALID:  "Licencia no válida",
}


def pack(*, model_loaded: bool, inference_error: bool, fallback_config: bool,
         dead_thread: bool, license_invalid: bool) -> int:
    """Arma la palabra de estado. Keyword-only: son banderas del mismo tipo y un orden
    posicional se equivoca en silencio."""
    word = 0

    # El bit dice "modelo NO cargado", así que esa entrada va negada.
    if not model_loaded:
        word |= 1 << BIT_MODEL_NOT_LOADED
    if inference_error:
        word |= 1 << BIT_INFERENCE_ERROR
    if fallback_config:
        word |= 1 << BIT_FALLBACK_CONFIG
    if dead_thread:
        word |= 1 << BIT_DEAD_THREAD
    if license_invalid:
        word |= 1 << BIT_LICENSE_INVALID

    return word


def describe(word: int) -> str:
    """Lista legible de los bits prendidos — para el log y la UI."""
    if not word:
        return "OK"
    active = [DESCRIPTIONS[b] for b in sorted(DESCRIPTIONS) if word & (1 << b)]
    return "; ".join(active) or f"desconocido (0x{word:04X})"
