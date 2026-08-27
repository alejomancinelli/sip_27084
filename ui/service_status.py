"""
Traducción del estado de un servicio al texto y el color de su chip.

Único lugar de la UI donde se decide cómo se ve un servicio caído: antes cada vista
repetía la misma tabla de `status -> etiqueta` por cada servicio, y agregar uno
significaba editar dos archivos.

Los servicios son los seis canales de salida del equipo, los mismos que `com_status`
publica al PLC: esa es la lista canónica y de ahí sale también qué estados cuentan
como "andando" (`com_status.is_active`). El vocabulario de estados lo definen los
dueños de cada subsistema —`system/video/abstract_video_server.py`,
`system/telemetry/backends/abstract_backend.py` y el servidor de Modbus—, todos con
las mismas palabras: `disabled | starting | connecting | active | connected | error`.
Acá sólo se elige con qué texto y con qué color se muestra cada una.

Módulo de presentación: sin Qt, sin I/O. Devuelve strings, así que se testea sin
levantar la GUI.
"""

from system.formats import com_status

from ui.strings import tr
from ui.widgets.status_chip import CHIP_ERROR, CHIP_OK, CHIP_WARNING

# Servicios que la UI muestra. El valor es la clave estable con la que las vistas
# los piden; el texto que se ve sale de la tabla de idiomas.
SERVICE_MODBUS_TCP = "modbus_tcp"
SERVICE_MODBUS_RTU = "modbus_rtu"
SERVICE_VIDEO_HTTP = "video_http"
SERVICE_VIDEO_RTSP = "video_rtsp"
SERVICE_INFLUXDB = "influxdb"
SERVICE_MQTT = "mqtt"

SERVICES = (
    SERVICE_MODBUS_TCP,
    SERVICE_MODBUS_RTU,
    SERVICE_VIDEO_HTTP,
    SERVICE_VIDEO_RTSP,
    SERVICE_INFLUXDB,
    SERVICE_MQTT,
)

# Estado -> (clave del texto, estado del chip). Un servicio apagado a propósito no es
# una falla: va en ámbar y no en rojo, para que el rojo signifique siempre "quiso
# levantar y no pudo".
_CHIP_BY_STATUS = {
    "disabled":   ("status_disabled",   CHIP_WARNING),
    "starting":   ("status_starting",   CHIP_WARNING),
    "connecting": ("status_connecting", CHIP_WARNING),
    "error":      ("status_error",      CHIP_ERROR),
}


def get_label(service: str) -> str:
    """Nombre del servicio en el idioma activo."""
    return tr(f"service_{service}")


def describe(service: str, status: str) -> tuple[str, str]:
    """
    (texto del chip, estado del chip) para ese servicio en ese estado.

    Los estados que cuentan como andando los define `com_status`, así que el chip
    verde de la UI y el bit que lee el PLC no pueden discrepar. Un estado que no
    está en la tabla se muestra tal cual y en ámbar: es información, no una falla
    confirmada.
    """
    label = get_label(service)
    if com_status.is_active(status):
        return f"{label}: {tr('status_active')}", CHIP_OK
    text_key, chip_state = _CHIP_BY_STATUS.get(status, ("", CHIP_WARNING))
    if not text_key:
        return f"{label}: {status or tr('status_unknown')}", chip_state
    return f"{label}: {tr(text_key)}", chip_state
