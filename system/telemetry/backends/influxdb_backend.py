"""
Backend de telemetría contra InfluxDB 2.x.

Implementa AbstractTelemetryBackend, que es donde está el contrato: ciclo de vida,
forma del registro y vocabulario de `status`. Su sección de config es
`telemetry.influxdb`; los tags viajan como texto y los fields con su tipo.

El token no sale de config.yaml: se lee de la variable de entorno INFLUXDB_TOKEN.

**Un corte del servidor no pierde los puntos, hasta un tope.** Lo que no se pudo escribir
queda en memoria y sale con el próximo intento: cada punto lleva el instante en que se
midió, así que llega tarde pero cae en su lugar de la serie. Mientras el servidor está
caído no se lo vuelve a llamar hasta `_RETRY_INTERVAL_S`: cada intento bloquea el hilo de
telemetría hasta el timeout HTTP, y hacerlo en cada batch llenaba la cola de
`persistence.py` de puntos descartados. Pasado `_PENDING_MAX_POINTS` se descartan los más
viejos, y al cerrar se pierde lo pendiente: no hay buffer en disco.

Un batch que el servidor **rechaza** —un 4xx como el conflicto de tipo de un campo— no se
reintenta: fallaría siempre y arrastraría a todos los que vengan detrás.

Sin la librería `influxdb-client` instalada el backend degrada a no-op y lo avisa
por `status`; el llamador no tiene que preguntar si está disponible.

    backend = InfluxDBBackend(ConfigManager())
    backend.setup()
    backend.write([{"measurement": "camera_1", "tags": {}, "fields": {"temperature": 41.2}}])
    backend.close()
"""

import os
import time
from collections import deque

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

_TIMEOUT_MS = 5000             # timeout HTTP de cada escritura; el de la librería es 10 s
_RETRY_INTERVAL_S = 30.0       # espera entre intentos mientras el servidor no contesta
_PENDING_MAX_POINTS = 6000     # puntos guardados durante un corte: ~10 min a 10 puntos/s


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
        # Lo que todavía no se pudo escribir. Lo toca sólo el hilo de telemetría.
        self._pending: deque = deque(maxlen=_PENDING_MAX_POINTS)
        self._retry_at_s = 0.0
        self._outage_lost_points = 0
        self._in_outage = False

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
            self._client = InfluxDBClient(url=url, token=token, org=self._org,
                                          timeout=_TIMEOUT_MS)
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
        Escribe un batch de registros, junto con lo que haya quedado pendiente de antes.
        Los registros sin measurement o sin fields se saltean.

        Con el servidor caído sólo acumula, hasta que vence `_RETRY_INTERVAL_S`. Los errores
        no se propagan: quedan en el log —una vez por corte— y en `status`.
        """
        if self._write_api is None:
            return
        self._keep([point for point in (self._build_point(rec) for rec in batch or [])
                    if point is not None])
        if not self._pending or time.monotonic() < self._retry_at_s:
            return

        points = list(self._pending)
        try:
            self._write_api.write(bucket=self._bucket, org=self._org, record=points)
        except Exception as e:
            self._status = STATUS_ERROR
            if not _is_retryable(e):
                self._pending.clear()
                logger.warning(f"[InfluxDB] El servidor rechazó {len(points)} puntos y se "
                               f"descartan, porque reintentarlos fallaría igual: {e}")
                return
            self._retry_at_s = time.monotonic() + _RETRY_INTERVAL_S
            if not self._in_outage:
                self._in_outage = True
                logger.warning(
                    f"[InfluxDB] Error escribiendo {len(points)} puntos: {e}. Se guardan "
                    f"hasta {_PENDING_MAX_POINTS} y se reintenta cada {_RETRY_INTERVAL_S:.0f} s.")
            return

        self._pending.clear()
        self._status = STATUS_CONNECTED
        if self._in_outage:
            lost = (f", {self._outage_lost_points} perdidos por falta de lugar"
                    if self._outage_lost_points else "")
            logger.info(f"[InfluxDB] El servidor volvió: {len(points)} puntos escritos{lost}.")
            self._in_outage = False
            self._outage_lost_points = 0
        else:
            logger.debug(f"[InfluxDB] {len(points)} puntos escritos.")

    def close(self):
        """Cierra el cliente y su write_api. Idempotente. Lo pendiente se pierde, con aviso."""
        if self._pending:
            logger.warning(f"[InfluxDB] Se cierra con {len(self._pending)} puntos sin escribir.")
            self._pending.clear()
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(f"[InfluxDB] Error cerrando el cliente: {e}")
        self._client = None
        self._write_api = None
        self._status = STATUS_DISABLED

    # ── Internos ─────────────────────────────────────────────────────────────

    def _keep(self, points: list):
        """Suma puntos a lo pendiente; lleno, se van los más viejos y se cuentan."""
        overflow = len(self._pending) + len(points) - _PENDING_MAX_POINTS
        if overflow > 0:
            if not self._outage_lost_points:
                logger.warning(f"[InfluxDB] {_PENDING_MAX_POINTS} puntos pendientes: se "
                               f"descartan los más viejos hasta que el servidor vuelva.")
            self._outage_lost_points += overflow
        self._pending.extend(points)

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


def _is_retryable(error: Exception) -> bool:
    """
    Si vale la pena reintentar lo que falló: todo salvo un 4xx del servidor.

    Un 4xx dice que el batch está mal —tipo de campo, bucket, permisos— y fallaría igual
    la próxima vez. El 429 es la excepción: es el servidor pidiendo que se espere.
    """
    status = getattr(error, "status", None)
    return not (isinstance(status, int) and 400 <= status < 500 and status != 429)
