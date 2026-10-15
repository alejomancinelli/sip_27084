"""
Palabra de estado de un pipeline — una palabra de 16 bits por slot de
`inference.pipelines`.

Responde: **¿los números de este pipeline son una medición?** Con todos sus bits en 0
el pipeline corre, con todas sus etapas cargadas, y su último resultado en cada cámara
fue confiable. Existe porque desde afuera «el pipeline no corre» y «el pipeline corre y
no encontró nada» se ven igual —los dos dejan sus registros quietos o en 0— y para uno
de seguridad, como la detección de personas, esa es la peor ambigüedad posible.

Un pipeline que no arrancó —sin licencia o deshabilitado— lleva además los bits de no
cargado y sin hilo, porque es lo que pasa: el bit de la causa dice por qué.

El bit de compuerta no es una falla: es la planta diciendo que ese proceso no está
ocurriendo, y mientras dura los registros del pipeline quedan con su último valor. Va en
la palabra porque la señal no siempre es del PLC —una entrada cableada desde un sensor
no la ve nadie más— y sin el bit un pipeline en pausa se lee igual que uno sano. La palabra
del sistema sigue resumiendo a todos en un solo bit; el detalle por pipeline es éste.

Módulo puro: sin Qt, sin Modbus, sin ConfigManager.
"""

from __future__ import annotations

BIT_NOT_LICENSED   = 0   # la licencia no habilita su `feature`: no se le levantó hilo
BIT_DISABLED       = 1   # `enabled: false` en el config
BIT_NOT_LOADED     = 2   # alguna etapa sin modelo, incluido un `type` no registrado
BIT_NOT_RUNNING    = 3   # su hilo no está corriendo
BIT_SYNTHETIC      = 4   # alguna etapa es un modelo sintético: no mide el proceso
BIT_INVALID_RESULT = 5   # el último resultado de alguna de sus cámaras no fue confiable
BIT_GATED          = 6   # una señal de la planta le corta los frames de alguna cámara

#: Bits 0-6 en uso; 7-15 libres.
WORD_MASK = 0x007F

DESCRIPTIONS = {
    BIT_NOT_LICENSED:   "Pipeline no habilitado por la licencia",
    BIT_DISABLED:       "Pipeline deshabilitado en el config",
    BIT_NOT_LOADED:     "Pipeline sin todos sus modelos cargados",
    BIT_NOT_RUNNING:    "Hilo del pipeline detenido",
    BIT_SYNTHETIC:      "Pipeline con modelo sintético",
    BIT_INVALID_RESULT: "Último resultado no confiable en alguna cámara",
    BIT_GATED:          "Frames cortados por una señal en alguna cámara",
}


def pack(*, licensed: bool, enabled: bool, loaded: bool, running: bool,
         synthetic: bool, last_result_valid: bool, gated: bool) -> int:
    """Arma la palabra de un pipeline. Keyword-only: son banderas del mismo tipo y un orden
    posicional se equivoca en silencio."""
    word = 0

    # Los bits dicen lo que falla, así que las entradas afirmativas van negadas.
    if not licensed:
        word |= 1 << BIT_NOT_LICENSED
    if not enabled:
        word |= 1 << BIT_DISABLED
    if not loaded:
        word |= 1 << BIT_NOT_LOADED
    if not running:
        word |= 1 << BIT_NOT_RUNNING
    if synthetic:
        word |= 1 << BIT_SYNTHETIC
    if not last_result_valid:
        word |= 1 << BIT_INVALID_RESULT
    if gated:
        word |= 1 << BIT_GATED

    return word


def describe(word: int) -> str:
    """Lista legible de los bits prendidos — para el log y la UI."""
    if not word:
        return "OK"
    active = [DESCRIPTIONS[b] for b in sorted(DESCRIPTIONS) if word & (1 << b)]
    return "; ".join(active) or f"desconocido (0x{word:04X})"
