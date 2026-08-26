"""Tests de la maquinaria genérica del esquema de registros: carga del YAML,
codificación con escala, espejo de configuración y detección de escrituras del PLC.

Nada de acá abre el mapa que se despacha con el repo —eso es de test_registers.py—
ni levanta un servidor Modbus: el servidor se reemplaza por un doble que sólo
recuerda valores.
"""

import pytest

from system.modbus.schema import (
    PRODUCER_CONFIG,
    PRODUCER_HEALTH,
    PRODUCER_INFERENCE,
    PRODUCERS,
    UINT16_MAX,
    Reg,
    RegisterSchema,
    default_map_path,
    load_registers,
    to_uint16,
)


class _FakeServer:
    """Sólo lo que `detect_plc_writes` le pide a un servidor: leer un registro."""

    def __init__(self, values: dict[int, int] | None = None):
        self._values = dict(values or {})

    def get_register(self, address: int) -> int:
        return self._values.get(address, 0)

    def set_register(self, address: int, value: int):
        """Simula al PLC escribiendo el registro."""
        self._values[address] = value


class _FakeConfig:
    """ConfigManager mínimo: `get` y `set` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **values):
        self._values = dict(values)
        self.saved = False

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object) -> bool:
        self._values[key] = value
        return True


def _write_map(tmp_path, text: str) -> str:
    path = tmp_path / "register_map.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestToUint16:
    def test_none_is_zero(self):
        assert to_uint16(None) == 0

    def test_plain_value_passes_through(self):
        assert to_uint16(37) == 37

    def test_scale_multiplies(self):
        assert to_uint16(41.5, 10) == 415

    def test_rounds_to_nearest(self):
        assert to_uint16(41.46, 10) == 415
        assert to_uint16(41.44, 10) == 414

    def test_saturates_at_the_top(self):
        assert to_uint16(10 ** 9) == UINT16_MAX
        assert to_uint16(6553.6, 10) == UINT16_MAX

    def test_clamps_negatives_to_zero(self):
        """Un holding register no tiene signo: una temperatura bajo cero da 0."""
        assert to_uint16(-15.0, 10) == 0

    def test_accepts_numeric_strings(self):
        assert to_uint16("41.5", 10) == 415


class TestRegDefaults:
    def test_minimal_row_only_needs_the_four_required_fields(self):
        reg = Reg(1, "heartbeat", "Heartbeat", PRODUCER_HEALTH)
        assert reg.scale == 1
        assert reg.is_bool is False
        assert reg.unit == ""
        assert reg.config_key is None
        assert reg.mirror_from_config is True

    def test_is_frozen(self):
        """El mapa se arma al importar y se comparte entre hilos sin lock."""
        reg = Reg(1, "heartbeat", "Heartbeat", PRODUCER_HEALTH)
        with pytest.raises(Exception):
            reg.addr = 2

    def test_producers_vocabulary(self):
        assert PRODUCERS == (PRODUCER_INFERENCE, PRODUCER_HEALTH, PRODUCER_CONFIG)


class TestLoadRegisters:
    def test_reads_a_valid_map(self, tmp_path):
        path = _write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: health
- addr: 2
  name: cpu_temp_c
  desc: Temperatura de CPU
  producer: health
  unit: °C
  scale: 10
""")
        registers = load_registers(path)
        assert [r.addr for r in registers] == [1, 2]
        assert registers[1].scale == 10
        assert registers[1].unit == "°C"

    def test_empty_file_is_a_valid_empty_map(self, tmp_path):
        """Hay proyectos que todavía no publican nada."""
        assert load_registers(_write_map(tmp_path, "")) == []

    def test_comments_only_file_is_an_empty_map(self, tmp_path):
        assert load_registers(_write_map(tmp_path, "# nada todavía\n")) == []

    def test_a_mapping_at_the_root_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="lista de registros"):
            load_registers(_write_map(tmp_path, "addr: 1\nname: x\n"))

    def test_a_non_mapping_entry_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="mapa de claves"):
            load_registers(_write_map(tmp_path, "- 1\n- 2\n"))

    def test_unknown_key_is_rejected(self, tmp_path):
        """Una clave mal escrita se ignoraría en silencio y el registro saldría mal."""
        with pytest.raises(ValueError, match="desconocida"):
            load_registers(_write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: health
  scala: 10
"""))

    def test_missing_required_field_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="falta"):
            load_registers(_write_map(tmp_path, "- addr: 1\n  name: heartbeat\n"))

    def test_empty_required_field_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="falta"):
            load_registers(_write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: ''
  producer: health
"""))

    def test_unknown_producer_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="producer"):
            load_registers(_write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: telemetry
"""))

    def test_address_zero_is_rejected(self, tmp_path):
        """El mapa es base-1: el registro 1 es el 40001 del PLC."""
        with pytest.raises(ValueError, match="base-1"):
            load_registers(_write_map(tmp_path, """
- addr: 0
  name: heartbeat
  desc: Heartbeat
  producer: health
"""))

    def test_error_message_names_the_file_and_the_row(self, tmp_path):
        path = _write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: health
- addr: 2
  name: broken
  desc: Roto
  producer: nope
""")
        with pytest.raises(ValueError) as excinfo:
            load_registers(path)
        assert "entrada 2" in str(excinfo.value)
        assert path in str(excinfo.value)

    def test_default_map_path_points_into_the_package(self):
        assert default_map_path().endswith("register_map.yaml")


class TestRegisterSchemaValidation:
    def test_duplicate_address_is_rejected(self):
        with pytest.raises(ValueError, match="Dirección Modbus duplicada"):
            RegisterSchema([
                Reg(1, "a", "A", PRODUCER_HEALTH),
                Reg(1, "b", "B", PRODUCER_HEALTH),
            ])

    def test_duplicate_name_is_rejected(self):
        with pytest.raises(ValueError, match="Nombre de registro duplicado"):
            RegisterSchema([
                Reg(1, "a", "A", PRODUCER_HEALTH),
                Reg(2, "a", "B", PRODUCER_HEALTH),
            ])

    def test_empty_map_is_valid(self):
        schema = RegisterSchema([])
        assert schema.registers == []
        assert schema.max_addr() == 0
        assert schema.descriptions() == {}


class TestIndexes:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "status", "Estado", PRODUCER_HEALTH),
            Reg(5, "coverage_pct", "Cobertura", PRODUCER_INFERENCE, unit="%", scale=10),
            Reg(9, "confidence_pct", "Confianza", PRODUCER_INFERENCE),
            Reg(20, "interval_s", "Intervalo", PRODUCER_CONFIG,
                config_key="collector.interval_s", default=60),
        ])

    def test_addr_by_name(self):
        assert self.schema.addr("coverage_pct") == 5

    def test_unknown_name_raises(self):
        """Pedir una dirección que no existe se cae acá, no en el PLC."""
        with pytest.raises(KeyError):
            self.schema.addr("no_existe")

    def test_max_addr(self):
        assert self.schema.max_addr() == 20

    def test_descriptions(self):
        assert self.schema.descriptions() == {1: "Estado", 5: "Cobertura",
                                              9: "Confianza", 20: "Intervalo"}

    def test_readback_addrs_are_only_the_inference_rows(self):
        assert self.schema.readback_addrs() == [5, 9]

    def test_config_regs_are_only_the_config_rows(self):
        assert [r.addr for r in self.schema.config_regs()] == [20]


class TestEncode:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "cpu_usage_pct", "CPU", PRODUCER_HEALTH, unit="%"),
            Reg(2, "cpu_temp_c", "Temp", PRODUCER_HEALTH, unit="°C", scale=10),
            Reg(3, "collecting", "Recolectando", PRODUCER_HEALTH, is_bool=True),
        ])

    def test_unscaled_value(self):
        assert self.schema.encode("cpu_usage_pct", 37) == 37

    def test_scaled_value(self):
        """El productor entrega el valor físico; la escala es del esquema."""
        assert self.schema.encode("cpu_temp_c", 41.5) == 415

    def test_boolean_ignores_the_scale(self):
        assert self.schema.encode("collecting", True) == 1
        assert self.schema.encode("collecting", False) == 0

    def test_boolean_takes_truthiness(self):
        assert self.schema.encode("collecting", "algo") == 1
        assert self.schema.encode("collecting", None) == 0

    def test_none_becomes_zero(self):
        assert self.schema.encode("cpu_temp_c", None) == 0

    def test_saturates_instead_of_wrapping(self):
        """Un valor absurdo tiene que quedar en el techo, no dar la vuelta a 0."""
        assert self.schema.encode("cpu_temp_c", 10 ** 6) == UINT16_MAX

    def test_encode_batch_maps_names_to_addresses(self):
        assert self.schema.encode_batch({"cpu_usage_pct": 37, "cpu_temp_c": 41.5}) == {
            1: 37, 2: 415,
        }

    def test_encode_batch_of_nothing_is_empty(self):
        assert self.schema.encode_batch({}) == {}

    def test_encode_batch_rejects_an_unknown_name(self):
        with pytest.raises(KeyError):
            self.schema.encode_batch({"no_existe": 1})


class TestMirrorBatch:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "cpu_usage_pct", "CPU", PRODUCER_HEALTH),
            Reg(10, "interval_s", "Intervalo", PRODUCER_CONFIG,
                config_key="collector.interval_s", default=60),
            Reg(11, "enabled", "Habilitado", PRODUCER_CONFIG, is_bool=True,
                config_key="collector.enabled", default=False),
            Reg(12, "threshold_pct", "Umbral", PRODUCER_CONFIG, scale=10,
                config_key="inference.threshold_pct", default=0),
            Reg(13, "external", "Externo", PRODUCER_CONFIG,
                config_key="algo.externo", mirror_from_config=False),
            Reg(14, "no_backing", "Sin config", PRODUCER_CONFIG),
        ])

    def test_mirrors_only_the_config_rows(self):
        config = _FakeConfig(**{"collector.interval_s": 90})
        batch = self.schema.mirror_batch(config)
        assert 1 not in batch, "una fila health no se espeja desde config"
        assert batch[10] == 90

    def test_uses_the_default_when_the_key_is_missing(self):
        assert self.schema.mirror_batch(_FakeConfig())[10] == 60

    def test_applies_the_scale(self):
        config = _FakeConfig(**{"inference.threshold_pct": 7.5})
        assert self.schema.mirror_batch(config)[12] == 75

    def test_encodes_booleans(self):
        config = _FakeConfig(**{"collector.enabled": True})
        assert self.schema.mirror_batch(config)[11] == 1

    def test_skips_externally_sourced_rows(self):
        """Su sitio productor las rellena; el espejo las pisaría."""
        assert 13 not in self.schema.mirror_batch(_FakeConfig())

    def test_skips_rows_without_a_config_key(self):
        assert 14 not in self.schema.mirror_batch(_FakeConfig())


class TestDetectPlcWrites:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "cpu_usage_pct", "CPU", PRODUCER_HEALTH),
            Reg(10, "interval_s", "Intervalo", PRODUCER_CONFIG,
                config_key="collector.interval_s", default=60),
        ])

    def test_first_cycle_detects_nothing(self):
        """`last_pushed` vacío es el prime natural: el datastore arranca en 0."""
        server = _FakeServer({10: 90})
        assert self.schema.detect_plc_writes(server, {}) == {}

    def test_a_plc_write_is_detected(self):
        server = _FakeServer({10: 60})
        server.set_register(10, 120)
        assert self.schema.detect_plc_writes(server, {10: 60}) == {10: 120}

    def test_an_untouched_register_is_not_a_write(self):
        server = _FakeServer({10: 60})
        assert self.schema.detect_plc_writes(server, {10: 60}) == {}

    def test_a_local_config_change_is_not_a_plc_write(self):
        """
        El caso que motiva comparar contra `last_pushed` y no contra el espejo: la
        app cambió config, el servidor sigue con lo último que ella escribió. Contra
        el espejo nuevo esto daría una escritura del PLC falsa, y el valor viejo del
        registro terminaría pisando el valor nuevo de config.
        """
        server = _FakeServer({10: 60})           # el PLC no tocó nada
        mirror = self.schema.mirror_batch(_FakeConfig(**{"collector.interval_s": 120}))
        assert mirror[10] == 120                 # el espejo ya difiere
        assert self.schema.detect_plc_writes(server, {10: 60}) == {}

    def test_health_rows_are_never_checked(self):
        """Sólo el bloque de config es escribible; el resto no se compara."""
        server = _FakeServer({1: 99})
        assert self.schema.detect_plc_writes(server, {1: 37}) == {}


class TestApplyPlcWrites:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(10, "interval_s", "Intervalo", PRODUCER_CONFIG,
                config_key="collector.interval_s", default=60),
            Reg(11, "enabled", "Habilitado", PRODUCER_CONFIG, is_bool=True,
                config_key="collector.enabled", default=False),
            Reg(12, "threshold_pct", "Umbral", PRODUCER_CONFIG, scale=10,
                config_key="inference.threshold_pct", default=0),
            Reg(13, "active", "Activo", PRODUCER_CONFIG, is_bool=True,
                apply="set_active"),
        ])

    def test_writes_land_in_config(self):
        config = _FakeConfig()
        batch = {}
        assert self.schema.apply_plc_writes(config, {10: 120}, {}, batch) is True
        assert config.get("collector.interval_s") == 120

    def test_decodes_the_scale(self):
        config = _FakeConfig()
        self.schema.apply_plc_writes(config, {12: 75}, {}, {})
        assert config.get("inference.threshold_pct") == 7.5

    def test_decodes_booleans(self):
        config = _FakeConfig()
        self.schema.apply_plc_writes(config, {11: 1}, {}, {})
        assert config.get("collector.enabled") is True

    def test_the_raw_value_is_echoed_into_the_batch(self):
        """
        Sin el eco, el `update_block` posterior reescribiría el valor viejo y el
        ciclo siguiente lo volvería a leer como una escritura nueva del PLC.
        """
        batch = {10: 60}
        self.schema.apply_plc_writes(_FakeConfig(), {10: 120}, {}, batch)
        assert batch[10] == 120

    def test_a_row_with_a_hook_calls_it_with_the_decoded_value(self):
        seen = []
        hooks = {"set_active": seen.append}
        needs_save = self.schema.apply_plc_writes(_FakeConfig(), {13: 1}, hooks, {})
        assert seen == [True]
        assert needs_save is False, "la fila con hook persiste por su cuenta"

    def test_a_missing_hook_is_not_a_crash(self):
        """Un hook sin cablear no puede tirar abajo el ciclo del PLC."""
        batch = {}
        assert self.schema.apply_plc_writes(_FakeConfig(), {13: 1}, {}, batch) is False
        assert batch[13] == 1

    def test_nothing_to_apply_needs_no_save(self):
        assert self.schema.apply_plc_writes(_FakeConfig(), {}, {}, {}) is False
