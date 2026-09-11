"""Tests del servidor Modbus: lectura de config, direccionamiento base-1, el
contexto de sólo lectura y el ciclo de vida de los dos transportes.

Nada de acá abre un socket ni un puerto serie: los `StartAsync*Server` de pymodbus
se reemplazan por dobles. El datastore sí es el real —es lo que se está probando—
y vive en RAM, así que no necesita nada de afuera.
"""

import asyncio

import pytest

from system.modbus import server as modbus_server
from system.modbus.server import (
    DEFAULT_REGISTER_COUNT,
    DEFAULT_SLAVE_ID,
    STATUS_ACTIVE,
    STATUS_DISABLED,
    STATUS_ERROR,
    STATUS_STARTING,
    SharedModbusServer,
)

_FC_READ_HOLDING = 3
_FC_READ_COILS = 1
_FC_READ_DISCRETE = 2
_FC_READ_INPUT = 4
_FC_WRITE_SINGLE = 6
_FC_WRITE_MULTIPLE = 16


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "modbus.slave_id": 1,
            "modbus.register_count": DEFAULT_REGISTER_COUNT,
            "modbus.tcp": {"enabled": False},
            "modbus.rtu": {"enabled": False},
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


def _server(**overrides) -> SharedModbusServer:
    return SharedModbusServer(_MockConfig(**overrides))


def _never_returns(*args, **kwargs):
    """Doble de un `StartAsync*Server` que levantó bien: se queda esperando."""
    async def _serve():
        await asyncio.Event().wait()
    return _serve()


class TestSlaveId:
    def test_default_when_the_key_is_missing(self):
        config = _MockConfig()
        config._values.pop("modbus.slave_id")
        assert SharedModbusServer(config).slave_id == DEFAULT_SLAVE_ID

    def test_reads_a_valid_id(self):
        assert _server(**{"modbus.slave_id": 17}).slave_id == 17

    def test_accepts_the_range_edges(self):
        assert _server(**{"modbus.slave_id": 1}).slave_id == 1
        assert _server(**{"modbus.slave_id": 247}).slave_id == 247

    def test_accepts_a_numeric_string(self):
        """El config lo puede traer citado sin que sea un error del usuario."""
        assert _server(**{"modbus.slave_id": "17"}).slave_id == 17

    @pytest.mark.parametrize("value", [0, 248, -1, 1000])
    def test_out_of_range_falls_back(self, value):
        assert _server(**{"modbus.slave_id": value}).slave_id == DEFAULT_SLAVE_ID

    @pytest.mark.parametrize("value", ["abc", None, [1], {}])
    def test_garbage_falls_back(self, value):
        assert _server(**{"modbus.slave_id": value}).slave_id == DEFAULT_SLAVE_ID

    def test_only_its_own_id_is_registered(self):
        """
        Con `single=True` el servidor contestaba a los 247 ids, que en un RS-485
        compartido pisa a los demás dispositivos del bus.
        """
        server = _server(**{"modbus.slave_id": 17})
        assert server._context.device_ids() == [17]


class TestRegisterCount:
    def test_default_when_the_key_is_missing(self):
        config = _MockConfig()
        config._values.pop("modbus.register_count")
        assert SharedModbusServer(config).register_count == DEFAULT_REGISTER_COUNT

    def test_reads_a_valid_count(self):
        assert _server(**{"modbus.register_count": 50}).register_count == 50

    @pytest.mark.parametrize("value", [0, -1, "abc", None])
    def test_invalid_count_falls_back(self, value):
        server = _server(**{"modbus.register_count": value})
        assert server.register_count == DEFAULT_REGISTER_COUNT

    def test_the_datastore_matches_the_count(self):
        server = _server(**{"modbus.register_count": 20})
        assert server.get_register(20) == 0
        server.update_register(20, 7)
        assert server.get_register(20) == 7
        # 21 ya está afuera del mapa.
        server.update_register(21, 7)
        assert server.get_register(21) == 0


class TestRegisterAccess:
    def setup_method(self):
        self.server = _server()

    def test_registers_start_at_zero(self):
        assert self.server.get_register(1) == 0

    def test_addressing_is_base_1(self):
        """El registro 1 es el primero del datastore, el 40001 del PLC."""
        self.server.update_register(1, 111)
        self.server.update_register(2, 222)
        assert self.server.get_register(1) == 111
        assert self.server.get_register(2) == 222
        assert self.server._simulator.registers[0].value == 111

    def test_the_last_register_is_reachable(self):
        """El off-by-one clásico: reg 100 con un mapa de 100 tiene que entrar."""
        self.server.update_register(DEFAULT_REGISTER_COUNT, 42)
        assert self.server.get_register(DEFAULT_REGISTER_COUNT) == 42

    @pytest.mark.parametrize("address", [0, -1, DEFAULT_REGISTER_COUNT + 1, 10_000])
    def test_out_of_map_writes_are_dropped(self, address, caplog):
        self.server.update_register(address, 42)
        assert self.server.get_register(address) == 0

    def test_out_of_map_write_is_logged(self, caplog):
        """Es un error del mapa, no del PLC: se avisa pero no se tira el ciclo."""
        with caplog.at_level("WARNING"):
            self.server.update_register(DEFAULT_REGISTER_COUNT + 1, 42)
        assert "fuera del mapa" in caplog.text

    def test_out_of_map_reads_return_zero(self):
        assert self.server.get_register(0) == 0
        assert self.server.get_register(DEFAULT_REGISTER_COUNT + 1) == 0

    def test_values_are_masked_to_16_bits(self):
        self.server.update_register(1, 0x1FFFF)
        assert self.server.get_register(1) == 0xFFFF

    def test_update_block_writes_every_item(self):
        self.server.update_block({1: 10, 5: 50, 9: 90})
        assert [self.server.get_register(a) for a in (1, 5, 9)] == [10, 50, 90]

    def test_update_block_skips_what_falls_outside(self):
        """Una dirección mala no puede impedir que se escriban las buenas."""
        self.server.update_block({1: 10, 10_000: 1, 5: 50})
        assert self.server.get_register(1) == 10
        assert self.server.get_register(5) == 50

    def test_update_block_of_nothing_is_harmless(self):
        self.server.update_block({})


class TestReadOnlyContext:
    def setup_method(self):
        self.simulator = _server()._simulator

    def test_reading_holding_registers_inside_the_map_is_allowed(self):
        assert self.simulator.validate(_FC_READ_HOLDING, 0, DEFAULT_REGISTER_COUNT)

    def test_reading_a_single_register_is_allowed(self):
        assert self.simulator.validate(_FC_READ_HOLDING, 0, 1)

    def test_the_last_register_validates(self):
        assert self.simulator.validate(_FC_READ_HOLDING, DEFAULT_REGISTER_COUNT - 1, 1)

    def test_a_read_that_runs_past_the_end_is_rejected(self):
        """
        `validate()` de pymodbus chequea sólo la dirección de arranque, y con `>` en
        vez de `>=`: una lectura que empieza adentro y se pasa reventaba con
        IndexError → 0x04 más un traceback por consulta.
        """
        assert not self.simulator.validate(_FC_READ_HOLDING, 50, DEFAULT_REGISTER_COUNT)
        assert not self.simulator.validate(_FC_READ_HOLDING, DEFAULT_REGISTER_COUNT, 1)

    def test_a_negative_address_is_rejected(self):
        assert not self.simulator.validate(_FC_READ_HOLDING, -1, 1)

    def test_a_zero_count_is_rejected(self):
        assert not self.simulator.validate(_FC_READ_HOLDING, 0, 0)

    @pytest.mark.parametrize("func_code", [_FC_READ_COILS, _FC_READ_DISCRETE,
                                           _FC_READ_INPUT])
    def test_other_read_functions_are_rejected(self, func_code):
        assert not self.simulator.validate(func_code, 0, 1)

    @pytest.mark.parametrize("func_code", [_FC_WRITE_SINGLE, _FC_WRITE_MULTIPLE])
    def test_writes_are_rejected(self, func_code):
        """El mapa es de sólo lectura: la app escribe desde adentro, el PLC no."""
        assert not self.simulator.validate(func_code, 0, 1)


class TestDisabledTransports:
    def test_status_starts_disabled(self):
        server = _server()
        assert server.tcp_status == STATUS_DISABLED
        assert server.rtu_status == STATUS_DISABLED

    def test_disabled_tcp_stays_disabled(self, caplog):
        server = _server(**{"modbus.tcp": {"enabled": False}})
        with caplog.at_level("INFO"):
            asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_DISABLED
        assert "TCP deshabilitado" in caplog.text

    def test_disabled_rtu_stays_disabled(self, caplog):
        server = _server(**{"modbus.rtu": {"enabled": False}})
        with caplog.at_level("INFO"):
            asyncio.run(server.run_rtu_server())
        assert server.rtu_status == STATUS_DISABLED
        assert "RTU deshabilitado" in caplog.text

    def test_tcp_is_enabled_by_default(self, monkeypatch):
        """Publicar por TCP es el caso normal; el RTU hay que pedirlo."""
        config = _MockConfig()
        config._values.pop("modbus.tcp")
        server = SharedModbusServer(config)
        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer",
                            lambda *a, **k: _raise(OSError("sin red")))
        asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_ERROR

    def test_rtu_is_disabled_by_default(self):
        config = _MockConfig()
        config._values.pop("modbus.rtu")
        server = SharedModbusServer(config)
        asyncio.run(server.run_rtu_server())
        assert server.rtu_status == STATUS_DISABLED


def _raise(exc):
    """Coroutine que falla igual que un `StartAsync*Server` que no pudo levantar."""
    async def _fail():
        raise exc
    return _fail()


class TestStartupFailures:
    def test_a_denied_bind_leaves_tcp_in_error(self, monkeypatch, caplog):
        server = _server(**{"modbus.tcp": {"enabled": True, "port": 502}})
        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer",
                            lambda *a, **k: _raise(PermissionError()))
        with caplog.at_level("ERROR"):
            asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_ERROR
        assert "Sin permiso" in caplog.text

    def test_a_taken_port_leaves_tcp_in_error(self, monkeypatch, caplog):
        server = _server(**{"modbus.tcp": {"enabled": True}})
        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer",
                            lambda *a, **k: _raise(OSError("address in use")))
        with caplog.at_level("ERROR"):
            asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_ERROR
        assert "address in use" in caplog.text

    def test_a_missing_serial_port_leaves_rtu_in_error(self, monkeypatch, caplog):
        """El RTU falla solo: el TCP tiene que poder seguir."""
        server = _server(**{"modbus.rtu": {"enabled": True, "port": "COM99"}})
        monkeypatch.setattr(modbus_server, "StartAsyncSerialServer",
                            lambda *a, **k: _raise(Exception("no existe COM99")))
        with caplog.at_level("ERROR"):
            asyncio.run(server.run_rtu_server())
        assert server.rtu_status == STATUS_ERROR
        assert "Continúa sólo el TCP" in caplog.text

    def test_a_failed_transport_does_not_touch_the_other(self, monkeypatch):
        server = _server(**{"modbus.tcp": {"enabled": True},
                            "modbus.rtu": {"enabled": False}})
        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer",
                            lambda *a, **k: _raise(OSError("boom")))
        asyncio.run(server.run_tcp_server())
        asyncio.run(server.run_rtu_server())
        assert server.tcp_status == STATUS_ERROR
        assert server.rtu_status == STATUS_DISABLED


class TestBlockedByAMissingMap:
    """
    Sin mapa de registros no se sirve, y se dice por qué.

    No arrancar es mejor que servir el datastore vacío: un PLC leyendo ceros no puede
    distinguir «no hay medición» de «falta el mapa». Sin conexión y sin latido, sí.
    """

    _REASON = "No se pudo cargar register_map.yaml: [Errno 2] No such file or directory"

    def test_tcp_does_not_serve(self, caplog):
        server = SharedModbusServer(_MockConfig(), blocked_reason=self._REASON)
        with caplog.at_level("ERROR"):
            asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_ERROR
        assert self._REASON in caplog.text

    def test_rtu_does_not_serve(self, caplog):
        """Aunque el config lo pida explícitamente."""
        config = _MockConfig(**{"modbus.rtu": {"enabled": True}})
        server = SharedModbusServer(config, blocked_reason=self._REASON)
        with caplog.at_level("ERROR"):
            asyncio.run(server.run_rtu_server())
        assert server.rtu_status == STATUS_ERROR
        assert self._REASON in caplog.text

    def test_it_is_an_error_and_not_a_disabled(self, caplog):
        """
        `disabled` es una decisión de la instalación y manda a mirar el config; esto no
        es eso, y confundirlos hace buscar el problema en el archivo equivocado.
        """
        config = _MockConfig(**{"modbus.tcp": {"enabled": False}})
        server = SharedModbusServer(config, blocked_reason=self._REASON)
        with caplog.at_level("INFO"):
            asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_ERROR
        assert "deshabilitado" not in caplog.text

    def test_without_a_reason_nothing_changes(self):
        """El caso normal: el mapa cargó y el servidor se comporta como siempre."""
        server = SharedModbusServer(_MockConfig(**{"modbus.rtu": {"enabled": False}}))
        asyncio.run(server.run_rtu_server())
        assert server.rtu_status == STATUS_DISABLED


class TestBecomingActive:
    def test_a_transport_that_survives_the_grace_period_turns_active(self, monkeypatch):
        monkeypatch.setattr(modbus_server, "_STARTUP_GRACE_S", 0.01)
        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer", _never_returns)
        server = _server(**{"modbus.tcp": {"enabled": True}})

        async def _drive():
            task = asyncio.ensure_future(server.run_tcp_server())
            await asyncio.sleep(0.1)
            status = server.tcp_status
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return status

        assert asyncio.run(_drive()) == STATUS_ACTIVE

    def test_a_transport_that_fails_never_turns_active(self, monkeypatch):
        """La gracia no puede pisar un error que ya se reportó."""
        monkeypatch.setattr(modbus_server, "_STARTUP_GRACE_S", 0.01)
        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer",
                            lambda *a, **k: _raise(OSError("boom")))
        server = _server(**{"modbus.tcp": {"enabled": True}})

        async def _drive():
            await server.run_tcp_server()
            await asyncio.sleep(0.05)      # más que la gracia
            return server.tcp_status

        assert asyncio.run(_drive()) == STATUS_ERROR

    def test_status_is_starting_before_the_grace_period_ends(self, monkeypatch):
        monkeypatch.setattr(modbus_server, "_STARTUP_GRACE_S", 5.0)
        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer", _never_returns)
        server = _server(**{"modbus.tcp": {"enabled": True}})

        async def _drive():
            task = asyncio.ensure_future(server.run_tcp_server())
            await asyncio.sleep(0.05)
            status = server.tcp_status
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return status

        assert asyncio.run(_drive()) == STATUS_STARTING


class TestLifecycle:
    def test_stop_before_run_does_nothing(self):
        """Se puede llamar en un shutdown que nunca llegó a arrancar el servidor."""
        _server().stop()

    def test_stop_is_idempotent(self):
        server = _server()
        server.stop()
        server.stop()

    def test_run_returns_after_stop(self, monkeypatch):
        """
        El ciclo completo: `run()` toma el hilo y `stop()` lo suelta desde otro. Si
        el cierre se colgara, este test se queda esperando en vez de pasar.
        """
        import threading

        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer", _never_returns)
        server = _server(**{"modbus.tcp": {"enabled": True}})

        finished = threading.Event()

        def _run():
            server.run()
            finished.set()

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        # Esperar a que el loop exista antes de pedirle que pare.
        for _ in range(200):
            if server._loop is not None:
                break
            threading.Event().wait(0.01)

        server.stop()
        assert finished.wait(5.0), "run() no volvió después de stop()"
        assert server.tcp_status == STATUS_DISABLED

    def test_run_reports_disabled_transports_and_returns(self):
        """Con los dos transportes apagados, `run()` arranca y vuelve solo."""
        server = _server()
        server.run()
        assert server.tcp_status == STATUS_DISABLED
        assert server.rtu_status == STATUS_DISABLED

    def test_shutdown_keeps_the_error_of_a_transport_that_never_came_up(
            self, monkeypatch):
        """
        El cierre no puede pisar el diagnóstico: si el transporte falló, ese es el
        motivo de que la corrida no sirviera y se perdería para siempre. `error` y
        `disabled` apagan igual el bit de la palabra de comunicaciones, así que
        conservarlo no le miente a nadie.
        """
        monkeypatch.setattr(modbus_server, "StartAsyncSerialServer",
                            lambda *a, **k: _raise(Exception("no existe COM99")))
        server = _server(**{"modbus.rtu": {"enabled": True, "port": "COM99"}})
        server.run()
        assert server.rtu_status == STATUS_ERROR
        assert server.tcp_status == STATUS_DISABLED

    def test_shutdown_clears_an_active_transport(self, monkeypatch):
        """Un transporte que sí levantó no puede quedar en `active` después del cierre."""
        import threading

        monkeypatch.setattr(modbus_server, "_STARTUP_GRACE_S", 0.01)
        monkeypatch.setattr(modbus_server, "StartAsyncTcpServer", _never_returns)
        server = _server(**{"modbus.tcp": {"enabled": True}})

        worker = threading.Thread(target=server.run, daemon=True)
        worker.start()
        for _ in range(200):
            if server.tcp_status == STATUS_ACTIVE:
                break
            threading.Event().wait(0.01)
        assert server.tcp_status == STATUS_ACTIVE

        server.stop()
        worker.join(timeout=5.0)
        assert not worker.is_alive()
        assert server.tcp_status == STATUS_DISABLED


class TestSharedDatastore:
    def test_the_context_serves_the_registered_device(self):
        """
        El camino real de una consulta del PLC: el contexto del servidor, el unit id
        y FC03. Los dos transportes comparten este contexto, así que el PLC ve los
        mismos valores por el cable que sea.
        """
        server = _server(**{"modbus.slave_id": 17})
        server.update_register(1, 123)
        values = asyncio.run(
            server._context.async_getValues(17, _FC_READ_HOLDING, 0, 1)
        )
        assert list(values) == [123]

    def test_a_query_to_another_unit_id_finds_nothing(self):
        """
        Sólo el id configurado está registrado. Es lo que hace que el TCP pueda
        contestar 0x0B en vez de un timeout opaco, y que el RTU se calle en un bus
        compartido en vez de pisar al esclavo que sí era el destinatario.
        """
        from pymodbus.exceptions import NoSuchIdException

        server = _server(**{"modbus.slave_id": 17})
        with pytest.raises(NoSuchIdException):
            asyncio.run(server._context.async_getValues(18, _FC_READ_HOLDING, 0, 1))
