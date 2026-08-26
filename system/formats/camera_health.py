"""
Estado de una cámara — una sola palabra de 16 bits.

Dos grupos con reglas distintas:

  - **Bits 0-5, adquisición: exactamente uno prendido.** Son estados mutuamente
    excluyentes (una cámara no puede estar desconectada y entregando frames a la
    vez), ordenados del problema más grave al más leve; `pack()` reporta el primero
    que aplica. Como siempre hay uno, la palabra **nunca vale 0** una vez que el
    equipo publicó: un 0 significa "todavía no escribió nada".
  - **Bit 6, lente sucio: independiente.** Es ortogonal a la adquisición —el vidrio
    puede estar sucio con la cámara sana, con el driver simulado o con la cámara
    caída— así que conviven. No se limpia solo: quien mide la nitidez sostiene el
    último veredicto mientras el detector no pueda desmentirlo (sin frames la
    ventana de nitidez se vacía y el detector pasa a "no disponible", pero el vidrio
    sigue sucio).

Las entradas son las claves estables de `AbstractCameraDriver.get_status()` más el
veredicto de la óptica, que se mide aparte.

Módulo puro: sin Qt, sin Modbus, sin ConfigManager.
"""

from __future__ import annotations

# ── Adquisición (bits 0-5, excluyentes entre sí) ─────────────────────────────
BIT_OK           = 0   # conectada y entregando frames
BIT_MISCONFIGURED = 1  # el driver no pudo construirse: corregir config + reiniciar
BIT_DISABLED     = 2   # `enabled: false`: no se le piden frames a propósito
BIT_DISCONNECTED = 3   # el driver reintenta; se recupera sola
BIT_NO_FRAMES    = 4   # conectada y habilitada, pero la adquisición no entrega nada
BIT_MOCK         = 5   # driver sintético: los números no vienen de una cámara

# ── Condición independiente (bit 6) ──────────────────────────────────────────
BIT_DIRTY_LENS   = 6   # la cámara anda; lo que está sucio es el vidrio

#: Bits 0-5: siempre uno y sólo uno prendido.
ACQUISITION_MASK = 0b0111111
#: Bits 0-6 en uso; 7-15 libres.
WORD_MASK        = 0b1111111

DESCRIPTIONS = {
    BIT_OK:            "Adquisición OK",
    BIT_MISCONFIGURED: "Mal configurada — corregir config.yaml",
    BIT_DISABLED:      "Deshabilitada por config",
    BIT_DISCONNECTED:  "Desconectada (reintentando)",
    BIT_NO_FRAMES:     "Conectada sin frames",
    BIT_MOCK:          "Driver simulado (frames sintéticos)",
    BIT_DIRTY_LENS:    "Lente sucio — limpiar el vidrio",
}


def get_acquisition_bit(connected: bool, config_error: str | None, fps_estimated: float,
                        capture_enabled: bool = True, is_synthetic: bool = False) -> int:
    """
    Único bit de adquisición que aplica, del problema más grave al más leve.

    Los dos primeros son lo que dice el config, y van antes que cualquier síntoma:
    una cámara mal configurada o apagada a propósito explica todo lo que venga
    después, y reportar "sin frames" sobre ella sería una falsa alarma.
    """
    if config_error:
        return BIT_MISCONFIGURED
    if not capture_enabled:
        return BIT_DISABLED
    if not connected:
        return BIT_DISCONNECTED
    if not fps_estimated:                 # 0 o None → la ventana no vio ni un frame
        return BIT_NO_FRAMES
    if is_synthetic:
        return BIT_MOCK
    return BIT_OK


def pack(*, connected: bool, config_error: str | None, fps_estimated: float,
         capture_enabled: bool = True, is_synthetic: bool = False,
         dirty_lens: bool = False) -> int:
    """Arma la palabra de estado de la cámara. Keyword-only: son cinco banderas del
    mismo tipo y un orden posicional se equivoca en silencio."""
    word = 1 << get_acquisition_bit(connected, config_error, fps_estimated,
                                    capture_enabled, is_synthetic)
    return set_dirty_lens(word, dirty_lens)


def set_dirty_lens(word: int, dirty_lens: bool) -> int:
    """
    Prende el bit de lente sucio sobre una palabra ya armada, sin tocar los de
    adquisición.

    Existe porque el veredicto de la óptica se calcula aparte del estado del driver
    y llega después. Idempotente.
    """
    return word | (1 << BIT_DIRTY_LENS) if dirty_lens else word


def describe(word: int) -> str:
    """Lista legible de los bits prendidos — para el log y la UI."""
    if not word:
        return "sin datos"
    active = [DESCRIPTIONS[b] for b in sorted(DESCRIPTIONS) if word & (1 << b)]
    return "; ".join(active) or f"desconocido (0x{word:04X})"
