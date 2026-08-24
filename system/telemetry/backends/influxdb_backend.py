"""
Backend de telemetría contra InfluxDB 2.x.

Implementa AbstractTelemetryBackend, que es donde está el contrato: ciclo de vida,
forma del registro y vocabulario de `status`. Su sección de config es
`telemetry.influxdb`; los tags viajan como texto y los fields con su tipo.

El token no sale de config.yaml: se lee de la variable de entorno INFLUXDB_TOKEN.

No reintenta ni acumula: un batch que falla se pierde, con el motivo en el log y
`status` en "error". El buffering, si el proyecto lo necesita, es de quien llama.

Sin la librería `influxdb-client` instalada el backend degrada a no-op y lo avisa
por `status`; el llamador no tiene que preguntar si está disponible.

    backend = InfluxDBBackend(ConfigManager())
    backend.setup()
    backend.write([{"measurement": "camera_1", "tags": {}, "fields": {"temperature": 41.2}}])
    backend.close()
"""

import os

from system.config_manager import ConfigManager
from system.logger import logger

from .abstract_backend import (
    STATUS_CONNECTED,
    STATUS_DISABLED,
    STATUS_ERROR,
    AbstractTelemetryBackend,
)

try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
    _INFLUXDB_AVAILABLE = True
except ImportError:
    _INFLUXDB_AVAILABLE = False

# La credencial viaja por entorno, nunca por config.yaml.
_TOKEN_ENV_VAR = "INFLUXDB_TOKEN"

_NS_PER_S = 1_000_000_000


class InfluxDBBackend(AbstractTelemetryBackend):
    """
    Escribe la telemetría en un bucket de InfluxDB 2.x.

    `write()` es sincrónico: vuelve recién cuando el servidor confirmó o falló, así
    que no se llama desde un hilo que también entregue frames.
    """

    service_name = "influxdb"

    def __init__(self, config_manager: ConfigManager):
        super().__init__(config_manager)
        self._client = None
        self._write_api = None
        self._org = ""
        self._bucket = ""

    # ── API pública ──────────────────────────────────────────────────────────

    def setup(self):
        """
        Abre el cliente y verifica que el servidor conteste.

        No propaga errores: los deja en `status`. Un servidor que no responde no
        cancela el backend —el cliente queda armado y cada `write()` reintenta—,
        pero una configuración incompleta sí, porque no se arregla sola.
        """
        if not self._config.get("telemetry.influxdb.enabled", False):
            logger.info("[InfluxDB] Deshabilitado en configuración.")
            self._status = STATUS_DISABLED
            return
        if not _INFLUXDB_AVAILABLE:
            logger.warning("[InfluxDB] influxdb-client no está instalado. No se escribe nada.")
            self._status = STATUS_DISABLED
            return

        url = self._config.get("telemetry.influxdb.url", "")
        self._org = self._config.get("telemetry.influxdb.org", "")
        self._bucket = self._config.get("telemetry.influxdb.bucket", "")
        token = os.environ.get(_TOKEN_ENV_VAR, "")

        if not url or not self._org or not self._bucket:
            logger.error(
                "[InfluxDB] Configuración incompleta: faltan url, org o bucket. "
                "No se escribe nada."
            )
            self._status = STATUS_ERROR
            return
        if not token:
            logger.error(
                f"[InfluxDB] La variable de entorno {_TOKEN_ENV_VAR} está vacía. "
                f"No se escribe nada."
            )
            self._status = STATUS_ERROR
            return

        try:
            self._client = InfluxDBClient(url=url, token=token, org=self._org)
            self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        except Exception as e:
            logger.error(f"[InfluxDB] No se pudo crear el cliente para {url}: {e}")
            self._status = STATUS_ERROR
            self._client = None
            self._write_api = None
            return

        if self._ping():
            self._status = STATUS_CONNECTED
            logger.info(f"[InfluxDB] Conectado a {url}, bucket '{self._bucket}'.")
        else:
            # El cliente queda armado igual: el servidor puede levantarse después y
            # el próximo write() lo encuentra sin rehacer el setup.
            self._status = STATUS_ERROR
            logger.warning(f"[InfluxDB] {url} no responde. Se reintenta en cada escritura.")

    def write(self, batch: list):
        """
        Escribe un batch de registros. Los que no tengan measurement o fields se saltean.

        Los errores no se propagan: quedan en el log y en `status`.
        """
        if self._write_api is None or not batch:
            return

        points = [point for point in (self._build_point(rec) for rec in batch)
                  if point is not None]
        if not points:
            return

        try:
            self._write_api.write(bucket=self._bucket, org=self._org, record=points)
            self._status = STATUS_CONNECTED
            logger.debug(f"[InfluxDB] {len(points)} puntos escritos.")
        except Exception as e:
            self._status = STATUS_ERROR
            logger.warning(f"[InfluxDB] Error escribiendo {len(points)} puntos: {e}")

    def close(self):
        """Cierra el cliente y su write_api. Idempotente."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"[InfluxDB] Error cerrando el cliente: {e}")
        self._client = None
        self._write_api = None
        self._status = STATUS_DISABLED

    # ── Internos ─────────────────────────────────────────────────────────────

    def _ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception as e:
            logger.debug(f"[InfluxDB] Ping fallido: {e}")
            return False

    def _build_point(self, rec: dict) -> "Point | None":
        """
        Arma el punto de un registro.

        Devuelve None si el registro no tiene measurement o se queda sin fields:
        InfluxDB rechaza el punto sin ningún valor, y un solo punto inválido
        voltea el batch entero.
        """
        measurement = rec.get("measurement")
        fields = {key: value for key, value in (rec.get("fields") or {}).items()
                  if value is not None}
        if not measurement or not fields:
            return None

        point = Point(measurement)
        for key, value in (rec.get("tags") or {}).items():
            point = point.tag(key, str(value))
        for key, value in fields.items():
            point = point.field(key, value)

        timestamp_s = rec.get("time")
        if timestamp_s is not None:
            point = point.time(int(timestamp_s * _NS_PER_S), WritePrecision.NS)
        return point
