"""Tests del backend de InfluxDB: validación de la configuración y la credencial,
armado de los puntos, tolerancia a un servidor caído y ciclo de vida.

El cliente de InfluxDB es un doble: no hace falta la librería instalada ni un
servidor levantado para verificar el contrato del backend."""

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import system.backends.influxdb_backend as ib
from system.backends.influxdb_backend import InfluxDBBackend

_URL = "http://influx:8086"
_ORG = "ORG"
_BUCKET = "BUCKET"
_TOKEN = "token-de-prueba"

_RECORD = {"measurement": "camera_1", "tags": {"slot": "camera_1", "index": 1},
           "fields": {"temperature": 41.2, "fps_estimated": 14.9},
           "time": 1700000000.5}


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "telemetry.influxdb.enabled": True,
            "telemetry.influxdb.url": _URL,
            "telemetry.influxdb.org": _ORG,
            "telemetry.influxdb.bucket": _BUCKET,
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakePoint:
    """Point de la librería: solo tiene que encadenar y guardar lo que le pasan."""

    def __init__(self, measurement: str):
        self.measurement = measurement
        self.tags = {}
        self.fields = {}
        self.timestamp = None

    def tag(self, key, value):
        self.tags[key] = value
        return self

    def field(self, key, value):
        self.fields[key] = value
        return self

    def time(self, timestamp, precision):
        self.timestamp = (timestamp, precision)
        return self


class _FakePrecision:
    NS = "ns"


def _install(monkeypatch, *, ping: bool = True, write_error: bool = False,
             client_error: bool = False) -> dict:
    """Deja el módulo con una librería falsa y devuelve el espía de lo que pasó."""
    spy = {"clients": 0, "writes": [], "closes": 0}

    class _FakeWriteApi:
        def write(self, bucket, org, record):
            if write_error:
                raise RuntimeError("servidor caído")
            spy["writes"].append((bucket, org, record))

    class _FakeClient:
        def __init__(self, url, token, org):
            if client_error:
                raise RuntimeError("url inválida")
            spy["clients"] += 1
            spy["url"] = url
            spy["token"] = token
            spy["org"] = org

        def write_api(self, write_options=None):
            spy["write_options"] = write_options
            return _FakeWriteApi()

        def ping(self):
            return ping

        def close(self):
            spy["closes"] += 1

    monkeypatch.setattr(ib, "_INFLUXDB_AVAILABLE", True, raising=False)
    monkeypatch.setattr(ib, "InfluxDBClient", _FakeClient, raising=False)
    monkeypatch.setattr(ib, "Point", _FakePoint, raising=False)
    monkeypatch.setattr(ib, "WritePrecision", _FakePrecision, raising=False)
    monkeypatch.setattr(ib, "SYNCHRONOUS", "sync", raising=False)
    return spy


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("INFLUXDB_TOKEN", _TOKEN)


def _ready(monkeypatch, **overrides) -> InfluxDBBackend:
    """Backend con el setup ya hecho contra la librería falsa."""
    backend = InfluxDBBackend(_MockConfig(**overrides))
    backend.setup()
    return backend


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> int:
    return sum(1 for record in caplog.records if text in record.getMessage())


# ── Arranque ─────────────────────────────────────────────────────────────────

class TestSetup:
    def test_a_healthy_server_leaves_the_backend_connected(self, monkeypatch):
        spy = _install(monkeypatch)
        assert _ready(monkeypatch).status == "connected"
        assert (spy["url"], spy["org"], spy["token"]) == (_URL, _ORG, _TOKEN)

    def test_the_write_api_is_created_once_in_the_setup(self, monkeypatch):
        """Crearla por escritura era rearmar el cliente HTTP en cada batch."""
        spy = _install(monkeypatch)
        backend = _ready(monkeypatch)
        backend.write([_RECORD])
        backend.write([_RECORD])
        assert spy["clients"] == 1
        assert spy["write_options"] == "sync"

    def test_disabled_by_config_does_not_touch_the_library(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _ready(monkeypatch, **{"telemetry.influxdb.enabled": False})
        assert backend.status == "disabled"
        assert spy["clients"] == 0

    def test_disabled_when_the_library_is_missing(self, monkeypatch):
        """Sin influxdb-client el backend es un no-op, no una excepción."""
        _install(monkeypatch)
        monkeypatch.setattr(ib, "_INFLUXDB_AVAILABLE", False)
        backend = _ready(monkeypatch)
        assert backend.status == "disabled"
        backend.write([_RECORD])

    @pytest.mark.parametrize("missing", [
        "telemetry.influxdb.url",
        "telemetry.influxdb.org",
        "telemetry.influxdb.bucket",
    ])
    def test_an_incomplete_config_is_an_error(self, monkeypatch, missing):
        spy = _install(monkeypatch)
        assert _ready(monkeypatch, **{missing: ""}).status == "error"
        assert spy["clients"] == 0

    def test_a_missing_token_is_an_error(self, monkeypatch, caplog):
        """La credencial va por entorno: sin ella no se intenta ni conectar."""
        spy = _install(monkeypatch)
        monkeypatch.delenv("INFLUXDB_TOKEN")
        with caplog.at_level(logging.ERROR, logger="app"):
            assert _ready(monkeypatch).status == "error"
        assert _logged(caplog, "INFLUXDB_TOKEN") == 1
        assert spy["clients"] == 0

    def test_a_client_that_cannot_be_built_is_an_error(self, monkeypatch):
        _install(monkeypatch, client_error=True)
        assert _ready(monkeypatch).status == "error"

    def test_a_server_that_does_not_answer_keeps_the_client_armed(self, monkeypatch):
        """El servidor puede levantarse después: no hay que rehacer el setup."""
        spy = _install(monkeypatch, ping=False)
        backend = _ready(monkeypatch)
        assert backend.status == "error"
        backend.write([_RECORD])
        assert backend.status == "connected"
        assert len(spy["writes"]) == 1


# ── Escritura ────────────────────────────────────────────────────────────────

class TestWrite:
    def test_the_batch_goes_to_the_configured_bucket(self, monkeypatch):
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([_RECORD])
        bucket, org, points = spy["writes"][0]
        assert (bucket, org) == (_BUCKET, _ORG)
        assert len(points) == 1

    def test_tags_travel_as_text(self, monkeypatch):
        """InfluxDB indexa los tags como strings; un int rompería el punto."""
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([_RECORD])
        assert spy["writes"][0][2][0].tags == {"slot": "camera_1", "index": "1"}

    def test_fields_keep_their_type(self, monkeypatch):
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([_RECORD])
        assert spy["writes"][0][2][0].fields == {"temperature": 41.2, "fps_estimated": 14.9}

    def test_the_record_timestamp_is_the_one_that_travels(self, monkeypatch):
        """Sin esto todos los puntos quedaban sellados con la hora del servidor."""
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([_RECORD])
        assert spy["writes"][0][2][0].timestamp == (1700000000500000000, "ns")

    def test_each_record_keeps_its_own_timestamp(self, monkeypatch):
        """
        Sin el sello propio, InfluxDB pone la hora de la escritura en todo el batch:
        los puntos de una misma serie colapsan en uno y solo sobrevive el último.
        """
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([
            {"measurement": "camera_1", "fields": {"temperature": 41.0}, "time": 1700000000.0},
            {"measurement": "camera_1", "fields": {"temperature": 42.0}, "time": 1700000002.0},
            {"measurement": "camera_1", "fields": {"temperature": 43.0}, "time": 1700000004.0},
        ])
        assert [point.timestamp for point in spy["writes"][0][2]] == [
            (1700000000000000000, "ns"),
            (1700000002000000000, "ns"),
            (1700000004000000000, "ns"),
        ]

    def test_a_record_without_time_is_stamped_by_the_server(self, monkeypatch):
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([{"measurement": "m", "fields": {"x": 1}}])
        assert spy["writes"][0][2][0].timestamp is None

    @pytest.mark.parametrize("record", [
        {"measurement": "m", "fields": {}},                     # sin valores
        {"measurement": "", "fields": {"x": 1}},                # sin serie
        {"measurement": "m", "fields": {"x": None}},            # el único valor es None
        {"fields": {"x": 1}},                                   # sin measurement
        {"measurement": "m"},                                   # sin fields
    ])
    def test_a_record_without_measurement_or_fields_is_skipped(self, monkeypatch, record):
        """InfluxDB rechaza el punto sin valores, y uno solo voltea el batch entero."""
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([record])
        assert spy["writes"] == []

    def test_a_none_field_does_not_drag_the_rest(self, monkeypatch):
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([{"measurement": "m", "fields": {"x": 1, "error": None}}])
        assert spy["writes"][0][2][0].fields == {"x": 1}

    def test_valid_records_survive_an_invalid_neighbour(self, monkeypatch):
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([{"measurement": "m", "fields": {}}, _RECORD])
        assert len(spy["writes"][0][2]) == 1

    def test_an_empty_batch_does_not_reach_the_server(self, monkeypatch):
        spy = _install(monkeypatch)
        _ready(monkeypatch).write([])
        assert spy["writes"] == []

    def test_writing_without_setup_is_a_no_op(self, monkeypatch):
        _install(monkeypatch)
        backend = InfluxDBBackend(_MockConfig())
        backend.write([_RECORD])
        assert backend.status == "disabled"

    def test_a_failed_write_is_reported_but_not_raised(self, monkeypatch, caplog):
        """Un error de red no puede voltear al hilo que publica la telemetría."""
        _install(monkeypatch, write_error=True)
        backend = _ready(monkeypatch)
        with caplog.at_level(logging.WARNING, logger="app"):
            backend.write([_RECORD])
        assert backend.status == "error"
        assert _logged(caplog, "Error escribiendo") == 1

    def test_the_backend_recovers_after_a_failed_write(self, monkeypatch):
        _install(monkeypatch, write_error=True)
        backend = _ready(monkeypatch)
        backend.write([_RECORD])
        assert backend.status == "error"

        _install(monkeypatch)          # el servidor vuelve
        backend = _ready(monkeypatch)
        backend.write([_RECORD])
        assert backend.status == "connected"


# ── Cierre ───────────────────────────────────────────────────────────────────

class TestClose:
    def test_close_releases_the_client(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _ready(monkeypatch)
        backend.close()
        assert spy["closes"] == 1
        assert backend.status == "disabled"

    def test_close_is_idempotent(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _ready(monkeypatch)
        backend.close()
        backend.close()
        assert spy["closes"] == 1

    def test_close_without_setup_does_not_fail(self, monkeypatch):
        _install(monkeypatch)
        InfluxDBBackend(_MockConfig()).close()

    def test_writing_after_close_is_a_no_op(self, monkeypatch):
        spy = _install(monkeypatch)
        backend = _ready(monkeypatch)
        backend.close()
        backend.write([_RECORD])
        assert spy["writes"] == []


class TestIdentity:
    def test_the_backend_names_its_service(self, monkeypatch):
        """El nombre identifica al backend en logs y estados sin usar isinstance."""
        assert InfluxDBBackend.service_name == "influxdb"
