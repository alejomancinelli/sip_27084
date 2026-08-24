"""
Backend de telemetría por MQTT (paho-mqtt).

Implementa AbstractTelemetryBackend, que es donde está el contrato: ciclo de vida,
forma del registro y vocabulario de `status`. Su sección de config es
`telemetry.mqtt`.

Cada registro se publica en `<topic_base>/<measurement>`, con un JSON de tags y
fields fusionados más `time` si el registro lo trae.

QoS 0 y sin sesión persistente: lo que no se pudo publicar no se recupera, que es
lo que corresponde para telemetría periódica — el próximo valor llega enseguida y
es más útil que el viejo.

La password no sale de config.yaml: se lee de la variable de entorno
MQTT_PASSWORD, porque es una credencial y el config se versiona y se edita desde
la UI. El usuario sí va en el config: identifica, no autentica.

La conexión la sostiene el hilo interno de paho: `setup()` no bloquea aunque el
broker no esté, y la reconexión es automática. Mientras no haya sesión, `write()`
descarta.

Sin la librería `paho-mqtt` instalada el backend degrada a no-op y lo avisa por
`status`; el llamador no tiene que preguntar si está disponible.

    backend = MqttBackend(ConfigManager())
    backend.setup()
    backend.write([{"measurement": "camera_1", "tags": {}, "fields": {"temperature": 41.2}}])
    backend.close()
"""

import json
import os

from system.config_manager import ConfigManager
from system.logger import logger

from .abstract_backend import (
    STATUS_CONNECTED,
    STATUS_CONNECTING,
    STATUS_DISABLED,
    STATUS_ERROR,
    AbstractTelemetryBackend,
)

try:
    import paho.mqtt.client as mqtt
    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False

# La credencial viaja por entorno, nunca por config.yaml.
_PASSWORD_ENV_VAR = "MQTT_PASSWORD"

_DEFAULT_PORT = 1883
_DEFAULT_TLS_PORT = 8883
_KEEPALIVE_S = 60
_CONNECT_RC_OK = 0
_QOS = 0   # telemetría periódica: el valor que se pierde lo reemplaza el siguiente


def _build_client(client_id: str) -> "mqtt.Client":
    """
    Crea el cliente sirviendo a las dos generaciones de paho.

    paho 2.x exige declarar la versión de la API de callbacks; se pide la 1 para
    que las firmas de `_on_connect` y `_on_disconnect` sean las mismas con 1.x y
    con 2.x.
    """
    if hasattr(mqtt, "CallbackAPIVersion"):
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=client_id)
    return mqtt.Client(client_id=client_id)


class MqttBackend(AbstractTelemetryBackend):
    """
    Publica la telemetría en un broker MQTT.

    `write()` no bloquea: entrega al hilo interno de paho, que es el único que
    habla con el broker.
    """

    service_name = "mqtt"

    def __init__(self, config_manager: ConfigManager):
        super().__init__(config_manager)
        self._client = None
        self._topic_base = ""
        self._is_connected = False
        # Anti-spam del warning de publicación (ver _publish).
        self._publish_warned = False

    # ── API pública ──────────────────────────────────────────────────────────

    def setup(self):
        """
        Arranca el cliente y su hilo de red.

        No bloquea esperando al broker ni propaga errores: quedan en `status`.
        """
        if not self._config.get("telemetry.mqtt.enabled", False):
            logger.info("[MQTT] Deshabilitado en configuración.")
            self._status = STATUS_DISABLED
            return
        if not _PAHO_AVAILABLE:
            logger.warning("[MQTT] paho-mqtt no está instalado. No se publica nada.")
            self._status = STATUS_DISABLED
            return

        host = self._config.get("telemetry.mqtt.host", "")
        self._topic_base = str(self._config.get("telemetry.mqtt.topic_base", "")).strip("/")
        if not host or not self._topic_base:
            logger.error(
                "[MQTT] Configuración incompleta: faltan host o topic_base. "
                "No se publica nada."
            )
            self._status = STATUS_ERROR
            return

        use_tls = bool(self._config.get("telemetry.mqtt.tls", False))
        port = (self._config.get("telemetry.mqtt.port")
                or (_DEFAULT_TLS_PORT if use_tls else _DEFAULT_PORT))
        user = self._config.get("telemetry.mqtt.user", "")
        password = os.environ.get(_PASSWORD_ENV_VAR, "")
        if user and not password:
            logger.warning(
                f"[MQTT] Hay usuario configurado pero la variable de entorno "
                f"{_PASSWORD_ENV_VAR} está vacía. El broker va a rechazar la conexión."
            )

        try:
            # El client_id identifica la sesión ante el broker: dos equipos con el
            # mismo device_id se echarían mutuamente.
            self._client = _build_client(str(self._config.get("system.device_id", "")))
            if user:
                self._client.username_pw_set(user, password)
            if use_tls:
                self._client.tls_set()

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect

            # connect_async + loop_start: el broker puede no estar todavía y el
            # arranque de la app no se puede quedar esperándolo.
            self._client.connect_async(host, port, keepalive=_KEEPALIVE_S)
            self._client.loop_start()
        except Exception as e:
            logger.error(f"[MQTT] No se pudo iniciar el cliente para {host}:{port}: {e}")
            self._status = STATUS_ERROR
            self._client = None
            return

        self._status = STATUS_CONNECTING
        logger.info(f"[MQTT] Conectando a {host}:{port}, publicando bajo '{self._topic_base}/'.")

    def write(self, batch: list):
        """
        Publica un mensaje por registro. Sin sesión con el broker, descarta.

        Los errores no se propagan: quedan en el log y en `status`.
        """
        if self._client is None or not self._is_connected or not batch:
            return

        for rec in batch:
            measurement = rec.get("measurement")
            payload = {**(rec.get("tags") or {}), **(rec.get("fields") or {})}
            if not measurement or not payload:
                continue
            if rec.get("time") is not None:
                payload["time"] = rec["time"]
            self._publish(f"{self._topic_base}/{measurement}", payload)

    def close(self):
        """Corta la sesión y para el hilo de red. Idempotente."""
        if self._client is not None:
            try:
                # disconnect() antes de loop_stop(): el paquete DISCONNECT lo manda
                # el hilo de red, y parado ya no queda quien lo escriba.
                self._client.disconnect()
                self._client.loop_stop()
            except Exception as e:
                logger.debug(f"[MQTT] Error cerrando el cliente: {e}")
        self._client = None
        self._is_connected = False
        self._status = STATUS_DISABLED

    # ── Callbacks de paho ────────────────────────────────────────────────────

    def _on_connect(self, client, userdata, flags, rc):
        if rc == _CONNECT_RC_OK:
            self._is_connected = True
            self._status = STATUS_CONNECTED
            self._publish_warned = False
            logger.info("[MQTT] Conectado al broker.")
        else:
            self._is_connected = False
            self._status = STATUS_ERROR
            logger.warning(f"[MQTT] El broker rechazó la conexión: rc={rc}")

    def _on_disconnect(self, client, userdata, rc, properties=None):
        # No hay nada que rearmar acá: el hilo de paho reintenta solo.
        self._is_connected = False
        self._status = STATUS_CONNECTING
        logger.warning(f"[MQTT] Desconectado del broker (rc={rc}). Reintentando.")

    # ── Internos ─────────────────────────────────────────────────────────────

    def _publish(self, topic: str, payload: dict):
        try:
            result = self._client.publish(topic, json.dumps(payload), qos=_QOS)
        except Exception as e:
            self._status = STATUS_ERROR
            logger.warning(f"[MQTT] Error publicando en {topic}: {e}")
            return

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            # Sin el latch esto inunda el log: la telemetría es periódica.
            if not self._publish_warned:
                logger.warning(f"[MQTT] Publicación rechazada en {topic}: rc={result.rc}")
                self._publish_warned = True
            return
        self._publish_warned = False
