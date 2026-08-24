# Backends de telemetría. El contrato está en abstract_backend.py; cada
# implementación lo cumple y lee su propia sección `telemetry.<service_name>`.
from .abstract_backend import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISABLED,
    STATUS_ERROR,
    AbstractTelemetryBackend,
)
from .influxdb_backend import InfluxDBBackend
from .mqtt_backend import MqttBackend

__all__ = [
    "AbstractTelemetryBackend",
    "InfluxDBBackend",
    "MqttBackend",
    "STATUS_CONNECTED",
    "STATUS_CONNECTING",
    "STATUS_DISABLED",
    "STATUS_ERROR",
]
