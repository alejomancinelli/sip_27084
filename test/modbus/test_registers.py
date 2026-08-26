"""Tests del mapa de registros que se despacha con el repo.

No prueban la maquinaria —eso es de test_schema.py— sino que el archivo concreto
sea coherente: que cargue, que quepa en el datastore, y que respete las
convenciones que el integrador y el código dan por ciertas. Un fork que edite
`register_map.yaml` corre estos tests y se entera acá de lo que rompió.
"""

import os

from system.modbus.registers import MAP_PATH, REGISTERS, SCHEMA
from system.modbus.schema import PRODUCERS, UINT16_MAX
from system.modbus.server import DEFAULT_REGISTER_COUNT

# Geometría del bloque de cámaras, tal como la documenta el mapa.
_CAMERA_BLOCK_START = 51
_CAMERA_BLOCK_STRIDE = 4


class TestMapLoads:
    def test_the_shipped_map_exists(self):
        assert os.path.isfile(MAP_PATH)

    def test_the_map_is_not_empty(self):
        """Un mapa vacío es válido para el esquema, pero no para este repo."""
        assert REGISTERS

    def test_schema_and_registers_agree(self):
        assert SCHEMA.registers == REGISTERS


class TestAddressSpace:
    def test_every_address_fits_in_the_default_datastore(self):
        """
        El mapa tiene que caber en `modbus.register_count`. Con una dirección más
        alta, `update_register` la descarta con un warning y el PLC lee 0 para
        siempre.
        """
        assert SCHEMA.max_addr() <= DEFAULT_REGISTER_COUNT

    def test_addresses_are_base_1(self):
        assert all(r.addr >= 1 for r in REGISTERS)

    def test_addresses_are_unique(self):
        """Lo valida `RegisterSchema`, pero acá falla nombrando el mapa que se rompió."""
        addrs = [r.addr for r in REGISTERS]
        assert len(set(addrs)) == len(addrs)

    def test_names_are_unique(self):
        names = [r.name for r in REGISTERS]
        assert len(set(names)) == len(names)


class TestConventions:
    def test_every_producer_is_in_the_vocabulary(self):
        for reg in REGISTERS:
            assert reg.producer in PRODUCERS, f"{reg.name}: producer {reg.producer!r}"

    def test_names_are_snake_case_and_english(self):
        for reg in REGISTERS:
            assert reg.name.replace("_", "").isalnum(), reg.name
            assert reg.name == reg.name.lower(), reg.name
            assert not reg.name.startswith("_"), reg.name

    def test_every_row_has_a_human_description(self):
        """De acá sale la tabla de la UI y la doc que lee el integrador."""
        for reg in REGISTERS:
            assert reg.desc.strip(), reg.name

    def test_scales_are_positive_integers(self):
        for reg in REGISTERS:
            assert isinstance(reg.scale, int) and reg.scale >= 1, reg.name

    def test_scaled_rows_declare_their_unit(self):
        """Una escala sin unidad no se puede documentar ni interpretar."""
        for reg in REGISTERS:
            if reg.scale != 1:
                assert reg.unit, f"{reg.name} escala x{reg.scale} sin unidad"

    def test_booleans_are_not_scaled(self):
        for reg in REGISTERS:
            if reg.is_bool:
                assert reg.scale == 1, reg.name

    def test_the_map_declares_no_config_block(self):
        """
        En el template la configuración se edita en el equipo, no desde el PLC. Un
        fork que agregue filas `config` tiene que cablear también el ciclo de
        `detect_plc_writes` / `apply_plc_writes`, así que este test es el recordatorio.
        """
        assert SCHEMA.config_regs() == []


class TestScaledRangesAreUsable:
    def test_every_scaled_row_can_carry_a_plausible_value(self):
        """
        Una escala demasiado grande satura antes de llegar al valor real y el PLC lee
        el techo. Se chequea contra un valor plausible por unidad.
        """
        plausible_by_unit = {"°C": 150, "%": 100, "fps": 240, "W": 1000,
                             "GB": 4000, "MB": 65535, "ms": 60000}
        for reg in REGISTERS:
            if reg.unit not in plausible_by_unit:
                continue
            top = plausible_by_unit[reg.unit] * reg.scale
            assert top <= UINT16_MAX, (
                f"{reg.name}: {plausible_by_unit[reg.unit]} {reg.unit} x{reg.scale} "
                f"= {top} no entra en un uint16"
            )


class TestLookupsUsedByTheApp:
    def test_status_words_are_present(self):
        """Las dos palabras de estado son la base del mapa en cualquier proyecto."""
        for name in ("system_status_bitfield", "com_status_bitfield"):
            assert SCHEMA.addr(name) >= 1

    def test_per_camera_block_keeps_a_fixed_stride(self):
        """
        Las cámaras van en bloques de paso fijo: `camera_N` arranca en
        51 + 4*(N-1). El fork copia el bloque y corre las direcciones, y el
        integrador calcula la dirección de una cámara sin abrir el mapa.
        """
        by_camera: dict[str, list[int]] = {}
        for reg in REGISTERS:
            if not reg.name.startswith("camera_"):
                continue
            slot = "_".join(reg.name.split("_")[:2])
            by_camera.setdefault(slot, []).append(reg.addr)

        for slot, addrs in by_camera.items():
            index = int(slot.split("_")[1])
            start = _CAMERA_BLOCK_START + _CAMERA_BLOCK_STRIDE * (index - 1)
            assert min(addrs) == start, f"{slot} tendría que arrancar en {start}"
            assert len(addrs) <= _CAMERA_BLOCK_STRIDE, f"{slot} se pasa de su bloque"
            assert max(addrs) < start + _CAMERA_BLOCK_STRIDE, f"{slot} pisa el bloque siguiente"

    def test_readback_addrs_are_a_subset_of_the_map(self):
        assert set(SCHEMA.readback_addrs()) <= {r.addr for r in REGISTERS}
