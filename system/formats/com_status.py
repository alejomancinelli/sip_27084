"""
Palabra de comunicaciones — una sola palabra de 16 bits.

Un bit por canal: **1 = el canal está funcionando ahora mismo**.

Cada subsistema reporta internamente `disabled | starting | active | error`, y un
bit no alcanza para cuatro estados: prende sólo con "levantó y está andando"
(`active`, o `connected` en los backends de telemetría). Un canal apagado a
propósito se lee igual que uno roto.

Módulo puro: sin Qt, sin Modbus, sin ConfigManager.
"""

from __future__ import annotations

BIT_RTSP       = 0   # servidor RTSP (GStreamer)
BIT_HTTP_VIDEO = 1   # servidor HTTP de video (MJPEG)
BIT_INFLUXDB   = 2   # backend de InfluxDB comunicando
BIT_MQTT       = 3   # backend de MQTT comunicando
BIT_MODBUS_TCP = 4   # servidor Modbus TCP
BIT_MODBUS_RTU = 5   # servidor Modbus RTU sobre RS-485

#: Bits 0-5 en uso; 6-15 libres.
WORD_MASK = 0x003F

DESCRIPTIONS = {
    BIT_RTSP:       "RTSP",
    BIT_HTTP_VIDEO: "Servidor HTTP de video",
    BIT_INFLUXDB:   "InfluxDB",
    BIT_MQTT:       "MQTT",
    BIT_MODBUS_TCP: "Modbus TCP",
    BIT_MODBUS_RTU: "Modbus RTU",
}

ACTIVE_STATES = frozenset({"active", "connected"})


def is_active(status: str | None) -> bool:
    """True con `active` o `connected`."""
    return status in ACTIVE_STATES


def pack(*, rtsp_status: str | None = None, http_video_status: str | None = None,
         influxdb_status: str | None = None, mqtt_status: str | None = None,
         modbus_tcp_status: str | None = None,
         modbus_rtu_status: str | None = None) -> int:
    """Arma la palabra de comunicaciones. Keyword-only: son seis estados del mismo
    tipo y un orden posicional se equivoca en silencio."""
    word = 0
    for bit, status in (
        (BIT_RTSP,       rtsp_status),
        (BIT_HTTP_VIDEO, http_video_status),
        (BIT_INFLUXDB,   influxdb_status),
        (BIT_MQTT,       mqtt_status),
        (BIT_MODBUS_TCP, modbus_tcp_status),
        (BIT_MODBUS_RTU, modbus_rtu_status),
    ):
        if is_active(status):
            word |= 1 << bit
    return word


def describe(word: int) -> str:
    """Lista legible de los canales activos — para el log y la UI."""
    if not word:
        return "sin canales activos"
    active = [DESCRIPTIONS[b] for b in sorted(DESCRIPTIONS) if word & (1 << b)]
    return "; ".join(active) or f"desconocido (0x{word:04X})"
