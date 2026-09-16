"""
Palabras de GPIO — dos palabras de 16 bits, una para entradas y otra para salidas.

Bits 0-3: **1 = el canal está activo**. En las entradas eso es la señal leída del
hardware; en las salidas, lo que se comandó.

Bits 8-11 de las entradas: **1 = esa entrada no se pudo leer**. Un canal que no responde
no es un canal en cero, y sin este bit el PLC no puede distinguir una entrada apagada de
una placa muerta.

Bit 15 de las dos: **1 = no hay GPIO**. Sin la librería de sistema el controlador degrada
a modo simulado, y lo que se publique de ahí no es una lectura del equipo.

Las salidas **no tienen bits de falla**: lo que se publica es el eco de lo comandado y no
una relectura del hardware, así que no hay lectura que pueda fallar. Que la salida haya
llegado al borne es algo que esta palabra no afirma.

Módulo puro: sin Qt, sin Modbus, sin ConfigManager.
"""

from __future__ import annotations

#: Canales que entran en la palabra. Más de cuatro pediría reacomodar los bits de falla.
MAX_CHANNELS = 4

STATE_SHIFT = 0     # bits 0-3: estado de cada canal
ERROR_SHIFT = 8     # bits 8-11: sólo entradas, la lectura falló
BIT_UNAVAILABLE = 15

#: Lo que devuelve una lectura que no se pudo hacer.
READ_ERROR = -1

WORD_MASK = 0x8F0F

DESCRIPTIONS = {
    STATE_SHIFT: "Estado de los canales 1-4",
    ERROR_SHIFT: "Falla de lectura de los canales 1-4 (sólo entradas)",
    BIT_UNAVAILABLE: "GPIO no disponible: modo simulado",
}


def pack_inputs(states_by_channel: dict, *, hardware_available: bool = True) -> int:
    """
    Arma la palabra de entradas a partir de `{número de canal: 1 | 0 | READ_ERROR}`.

    Los canales se numeran desde 1, como en el borne. Un canal que no figura se publica
    como cero y sin bit de falla: no está cableado, que es distinto de no haberse podido
    leer.
    """
    word = 0 if hardware_available else 1 << BIT_UNAVAILABLE
    for channel, state in (states_by_channel or {}).items():
        bit = _channel_bit(channel)
        if bit is None:
            continue
        if int(state) == READ_ERROR:
            word |= 1 << (ERROR_SHIFT + bit)
        elif int(state):
            word |= 1 << (STATE_SHIFT + bit)
    return word


def pack_outputs(states_by_channel: dict, *, hardware_available: bool = True) -> int:
    """
    Arma la palabra de salidas a partir de `{número de canal: 1 | 0}`.

    Es el eco de lo que se comandó, no una relectura del hardware: por eso no hay bits de
    falla que armar.
    """
    word = 0 if hardware_available else 1 << BIT_UNAVAILABLE
    for channel, state in (states_by_channel or {}).items():
        bit = _channel_bit(channel)
        if bit is not None and int(state) > 0:
            word |= 1 << (STATE_SHIFT + bit)
    return word


def _channel_bit(channel: object) -> int | None:
    """Posición del canal dentro del grupo de bits, o None si está fuera de rango."""
    try:
        number = int(channel)
    except (TypeError, ValueError):
        return None
    if 1 <= number <= MAX_CHANNELS:
        return number - 1
    return None
