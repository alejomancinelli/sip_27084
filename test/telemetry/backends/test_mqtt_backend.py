"""Tests del backend MQTT: armado del cliente para las dos APIs de paho, puerto y
credenciales, publicación por tópico, estados de conexión y cierre ordenado.

El cliente de paho es un doble: no hace falta la librería instalada ni un broker
levantado para verificar el contrato del backend."""

import json
import logging
import os
import sys
import types

import pytest


import system.telemetry.backends.mqtt_backend as mb
from system.telemetry.backends.mqtt_backend import MqttBackend, _build_client

_HOST = "broker.local"
_TOPIC_BASE = "planta/linea_1"
_DEVICE_ID = "DEVICE_1"
_PASSWORD = "password-de-prueba"

_RECORD = {"measurement": "camera_1", "tags": {"slot": "camera_1"},
           "fields": {"temperature": 41.2}, "time": 1700000000.5}


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "telemetry.mqtt.enabled": True,
            "telemetry.mqtt.host": _HOST,
            "telemetry.mqtt.topic_base": _TOPIC_BASE,
            "system.device_id": _DEVICE_ID,
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakeResult:
    def __init__(self, rc: int):
        self.rc = rc


def _install(monkeypatch, *, callback_api: bool = True, publish_rc: int = 0,
             publish_error: bool = False, connect_error: bool = False) -> dict:
    """Deja el módulo con un paho falso y devuelve el espía de lo que pasó."""
    spy = {"published": [], "calls": [], "clients": []}

    class _FakeClient:
        def __init__(self, callback_api_version=None, client_id=""):
            self.callback_api_version = callback_api_version
            self.client_id = client_id
            self.creds = None
            self.tls = False
            self.target = None
            spy["clients"].append(self)

        def username_pw_set(self, user, password):
            self.creds = (user, password)

        def tls_set(self):
            self.tls = True

        def connect_async(self, host, port, keepalive):
            if connect_error:
                raise RuntimeError("host inválido")
            self.target = (host, port, keepalive)

        def loop_start(self):
            spy["calls"].append("loop_start")

        def loop_stop(self):
            spy["calls"].append("loop_stop")

        def disconnect(self):
            spy["calls"].append("disconnect")

        def publish(self, topic, payload, qos=0):
            if publish_error:
                raise RuntimeError("socket cerrado")
            spy["published"].append((topic, payload, qos))
            return _FakeResult(publish_rc)

    fake = types.SimpleNamespace(Client=_FakeClient, MQTT_ERR_SUCCESS=0)
    if callback_api:
        fake.CallbackAPIVersion = types.SimpleNamespace(VERSION1=1)

    monkeypatch.setattr(mb, "_PAHO_AVAILABLE", True, raising=False)
    monkeypatch.setattr(mb, "mqtt", fake, raising=False)
    return spy


@pytest.fixture(autouse=True)
def password(monkeypatch):
    monkeypatch.setenv("MQTT_PASSWORD", _PASSWORD)


def _ready(monkeypatch, **overrides) -> MqttBackend:
    """Backend con el setup ya hecho contra el paho falso."""
    backend = MqttBackend(_MockConfig(**overrides))
    backend.setup()
    return backend


def _connected(monkeypatch, **overrides) -> MqttBackend:
    """Backend con la sesión ya establecida: el broker aceptó la conexión."""
    backend = _ready(monkeypatch, **overrides)
    backend._on_connect(None, None, None, 0)
    return backend


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> int:
    return sum(1 for record in caplog.records if text in record.getMessage())


# ── Armado del cliente ───────────────────────────────────────────────────────

class TestBuildClient:
    def test_paho_2_gets_an_explicit_callback_api_version(self, monkeypatch):
        """mqtt.Client() sin versión levanta excepción en paho 2.x."""
        _install(monkeypatch)
        assert _build_client("id").callback_api_version == 1

    def test_paho_1_is_built_without_it(self, monkeypatch):
        _install(monkeypatch, callback_api=False)
        assert _build_client("id").callback_api_version is None

    def test_the_client_id_is_the_device_id(self, monkeypatch):
        """Dos equipos con el mismo client_id se echan del broker."""
        _install(monkeypatch)
        assert _ready(monkeypatch)._client.client_id == _DEVICE_ID


# ── Arranque ─────────────────────────────────────────────────────────────────

class TestSetup:
    def test_the_setup_leaves_the_client_connecting(self, monkeypatch):
        """connect_async no espera al broker: el arranque de la app no se traba."""
        spy = _install(monkeypatch)
        backend = _ready(monkeypatch)
        assert backend.status == "connecting"
        assert spy["calls"] == ["loop_start"]

    def test_disabled_by_config_does_not_touch_the_library(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _ready(monkeypatch, **{"telemetry.mqtt.enabled": False})
        assert backend.status == "disabled"
        assert spy["clients"] == []

    def test_disabled_when_the_library_is_missing(self, monkeypatch):
        """Sin paho-mqtt el backend es un no-op, no una excepción."""
        _install(monkeypatch)
        monkeypatch.setattr(mb, "_PAHO_AVAILABLE", False)
        backend = _ready(monkeypatch)
        assert backend.status == "disabled"
        backend.write([_RECORD])

    @pytest.mark.parametrize("missing", ["telemetry.mqtt.host", "telemetry.mqtt.topic_base"])
    def test_an_incomplete_config_is_an_error(self, monkeypatch, missing):
        """Sin topic_base se publicaba en 'None/<measurement>'."""
        spy = _install(monkeypatch)
        assert _ready(monkeypatch, **{missing: ""}).status == "error"
        assert spy["clients"] == []

    def test_a_client_that_cannot_start_is_an_error(self, monkeypatch):
        _install(monkeypatch, connect_error=True)
        assert _ready(monkeypatch).status == "error"

    def test_the_broker_address_and_keepalive_reach_the_client(self, monkeypatch):
        _install(monkeypatch)
        assert _ready(monkeypatch)._client.target == (_HOST, 1883, 60)

    def test_the_default_port_depends_on_tls(self, monkeypatch):
        _install(monkeypatch)
        plain = _ready(monkeypatch)
        assert plain._client.target[1] == 1883
        assert plain._client.tls is False

        secure = _ready(monkeypatch, **{"telemetry.mqtt.tls": True})
        assert secure._client.target[1] == 8883
        assert secure._client.tls is True

    def test_a_configured_port_wins(self, monkeypatch):
        _install(monkeypatch)
        backend = _ready(monkeypatch, **{"telemetry.mqtt.port": 1884})
        assert backend._client.target[1] == 1884

    def test_a_zero_port_falls_back_to_the_default(self, monkeypatch):
        """`port: 0` es lo que dice el config.yaml de plantilla."""
        _install(monkeypatch)
        assert _ready(monkeypatch, **{"telemetry.mqtt.port": 0})._client.target[1] == 1883


# ── Credenciales ─────────────────────────────────────────────────────────────

class TestCredentials:
    def test_no_user_means_no_authentication(self, monkeypatch):
        _install(monkeypatch)
        assert _ready(monkeypatch)._client.creds is None

    def test_the_password_comes_from_the_environment(self, monkeypatch):
        """El config.yaml se versiona y se edita desde la UI: no lleva credenciales."""
        _install(monkeypatch)
        backend = _ready(monkeypatch, **{"telemetry.mqtt.user": "planta"})
        assert backend._client.creds == ("planta", _PASSWORD)

    def test_a_user_without_password_is_warned(self, monkeypatch, caplog):
        _install(monkeypatch)
        monkeypatch.delenv("MQTT_PASSWORD")
        with caplog.at_level(logging.WARNING, logger="app"):
            _ready(monkeypatch, **{"telemetry.mqtt.user": "planta"})
        assert _logged(caplog, "MQTT_PASSWORD") == 1


# ── Publicación ──────────────────────────────────────────────────────────────

class TestPublish:
    def test_nothing_is_published_before_the_session_exists(self, monkeypatch):
        """Con QoS 0 y sin sesión el publish se pierde: no vale la pena armarlo."""
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([_RECORD])
        assert spy["published"] == []

    def test_the_topic_is_the_base_plus_the_measurement(self, monkeypatch):
        spy = _install(monkeypatch)
        _connected(monkeypatch).write([_RECORD])
        assert spy["published"][0][0] == f"{_TOPIC_BASE}/camera_1"

    def test_the_topic_base_is_normalized(self, monkeypatch):
        """Las barras de sobra dejarían un tópico con '//' en el medio."""
        spy = _install(monkeypatch)
        _connected(monkeypatch, **{"telemetry.mqtt.topic_base": "/planta/"}).write([_RECORD])
        assert spy["published"][0][0] == "planta/camera_1"

    def test_the_payload_merges_tags_fields_and_time(self, monkeypatch):
        spy = _install(monkeypatch)
        _connected(monkeypatch).write([_RECORD])
        assert json.loads(spy["published"][0][1]) == {
            "slot": "camera_1", "temperature": 41.2, "time": 1700000000.5}

    def test_a_record_without_time_publishes_without_it(self, monkeypatch):
        spy = _install(monkeypatch)
        _connected(monkeypatch).write([{"measurement": "m", "fields": {"x": 1}}])
        assert json.loads(spy["published"][0][1]) == {"x": 1}

    def test_the_publication_is_qos_zero(self, monkeypatch):
        spy = _install(monkeypatch)
        _connected(monkeypatch).write([_RECORD])
        assert spy["published"][0][2] == 0

    def test_one_message_per_record(self, monkeypatch):
        spy = _install(monkeypatch)
        _connected(monkeypatch).write([_RECORD, {**_RECORD, "measurement": "camera_2"}])
        assert [topic for topic, _, _ in spy["published"]] == [
            f"{_TOPIC_BASE}/camera_1", f"{_TOPIC_BASE}/camera_2"]

    @pytest.mark.parametrize("record", [
        {"measurement": "", "fields": {"x": 1}},        # sin serie
        {"fields": {"x": 1}},                           # sin measurement
        {"measurement": "m"},                           # sin nada que publicar
        {"measurement": "m", "tags": {}, "fields": {}},
    ])
    def test_a_record_without_measurement_or_payload_is_skipped(self, monkeypatch, record):
        spy = _install(monkeypatch)
        _connected(monkeypatch).write([record])
        assert spy["published"] == []

    def test_an_empty_batch_publishes_nothing(self, monkeypatch):
        spy = _install(monkeypatch)
        _connected(monkeypatch).write([])
        assert spy["published"] == []

    def test_a_rejected_publication_is_warned_once(self, monkeypatch, caplog):
        """La telemetría es periódica: sin latch el log se llena del mismo error."""
        _install(monkeypatch, publish_rc=4)
        backend = _connected(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="app"):
            for _ in range(5):
                backend.write([_RECORD])
        assert _logged(caplog, "Publicación rechazada") == 1

    def test_a_publish_exception_is_reported_but_not_raised(self, monkeypatch, caplog):
        _install(monkeypatch, publish_error=True)
        backend = _connected(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="app"):
            backend.write([_RECORD])
        assert backend.status == "error"
        assert _logged(caplog, "Error publicando") == 1


# ── Estados de la conexión ───────────────────────────────────────────────────

class TestConnectionState:
    def test_an_accepted_connection_is_connected(self, monkeypatch):
        _install(monkeypatch)
        assert _connected(monkeypatch).status == "connected"

    def test_a_rejected_connection_is_an_error(self, monkeypatch, caplog):
        _install(monkeypatch)
        backend = _ready(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="app"):
            backend._on_connect(None, None, None, 5)
        assert backend.status == "error"
        assert _logged(caplog, "rechazó la conexión") == 1

    def test_a_rejected_connection_publishes_nothing(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _ready(monkeypatch)
        backend._on_connect(None, None, None, 5)
        backend.write([_RECORD])
        assert spy["published"] == []

    def test_a_drop_goes_back_to_connecting(self, monkeypatch):
        """El hilo de paho reconecta solo: el estado no es 'error'."""
        _install(monkeypatch)
        backend = _connected(monkeypatch)
        backend._on_disconnect(None, None, 7)
        assert backend.status == "connecting"

    def test_nothing_is_published_after_a_drop(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _connected(monkeypatch)
        backend._on_disconnect(None, None, 7)
        backend.write([_RECORD])
        assert spy["published"] == []

    def test_a_reconnection_publishes_again(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _connected(monkeypatch)
        backend._on_disconnect(None, None, 7)
        backend._on_connect(None, None, None, 0)
        backend.write([_RECORD])
        assert len(spy["published"]) == 1


# ── Cierre ───────────────────────────────────────────────────────────────────

class TestClose:
    def test_the_session_is_cut_before_stopping_the_network_thread(self, monkeypatch):
        """Parado el loop ya no hay quien escriba el paquete DISCONNECT."""
        spy = _install(monkeypatch)
        _connected(monkeypatch).close()
        assert spy["calls"] == ["loop_start", "disconnect", "loop_stop"]

    def test_close_leaves_the_backend_disabled(self, monkeypatch):
        _install(monkeypatch)
        backend = _connected(monkeypatch)
        backend.close()
        assert backend.status == "disabled"

    def test_close_is_idempotent(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _connected(monkeypatch)
        backend.close()
        backend.close()
        assert spy["calls"].count("disconnect") == 1

    def test_close_without_setup_does_not_fail(self, monkeypatch):
        _install(monkeypatch)
        MqttBackend(_MockConfig()).close()

    def test_publishing_after_close_is_a_no_op(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _connected(monkeypatch)
        backend.close()
        backend.write([_RECORD])
        assert spy["published"] == []


class TestIdentity:
    def test_the_backend_names_its_service(self):
        """El nombre identifica al backend en logs y estados sin usar isinstance."""
        assert MqttBackend.service_name == "mqtt"
