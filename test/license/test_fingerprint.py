"""Tests de la huella: qué se descarta, cómo se cuenta el N-de-M, y que leer el
hardware real no pueda tumbar el arranque."""

import subprocess

import pytest

from system.license import fingerprint


class TestHashValue:
    def test_same_value_and_source_gives_same_hash(self):
        assert fingerprint.hash_value("board_uuid", "ABC-123") == \
               fingerprint.hash_value("board_uuid", "ABC-123")

    def test_same_value_under_two_sources_gives_different_hashes(self):
        # El nombre de la fuente va adentro del hash: si no, un serial de disco igual a un
        # serial de placa contaría como dos coincidencias siendo un solo dato.
        assert fingerprint.hash_value("board_uuid", "ABC-123") != \
               fingerprint.hash_value("disk_serial", "ABC-123")

    def test_case_and_padding_do_not_change_the_hash(self):
        assert fingerprint.hash_value("board_uuid", "  abc-123 \n") == \
               fingerprint.hash_value("board_uuid", "ABC-123")

    def test_trailing_nul_of_the_device_tree_is_ignored(self):
        assert fingerprint.hash_value("module_serial", "1423121012345\x00") == \
               fingerprint.hash_value("module_serial", "1423121012345")

    @pytest.mark.parametrize("value", [
        "", "  ", None, "abc",
        "To be filled by O.E.M.", "Default string", "None", "System Serial Number",
        "00000000-0000-0000-0000-000000000000", "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
        "0000000000", "----------",
    ])
    def test_factory_filler_is_not_an_identity(self, value):
        # Estos valores son idénticos en miles de equipos: tomarlos por huella haría que
        # una licencia validara en cualquier máquina con el mismo BIOS.
        assert fingerprint.hash_value("board_serial", value) == ""


class TestMatching:
    def setup_method(self):
        self.expected = {
            "board_uuid": fingerprint.hash_value("board_uuid", "UUID-1"),
            "board_serial": fingerprint.hash_value("board_serial", "BOARD-1"),
            "disk_serial": fingerprint.hash_value("disk_serial", "DISK-1"),
        }

    def test_all_sources_match(self):
        assert fingerprint.count_matches(dict(self.expected), self.expected) == 3

    def test_a_replaced_disk_still_reaches_the_quorum(self):
        current = dict(self.expected)
        current["disk_serial"] = fingerprint.hash_value("disk_serial", "DISK-NUEVO")
        assert fingerprint.count_matches(current, self.expected) == 2
        assert fingerprint.is_match(current, self.expected, min_matches=2)

    def test_a_missing_source_does_not_count(self):
        current = dict(self.expected)
        del current["board_serial"]
        assert fingerprint.count_matches(current, self.expected) == 2

    def test_another_machine_does_not_match(self):
        other = {"board_uuid": fingerprint.hash_value("board_uuid", "UUID-2")}
        assert fingerprint.count_matches(other, self.expected) == 0
        assert not fingerprint.is_match(other, self.expected, min_matches=2)

    def test_quorum_is_exact_not_approximate(self):
        current = {"board_uuid": self.expected["board_uuid"]}
        assert fingerprint.is_match(current, self.expected, min_matches=1)
        assert not fingerprint.is_match(current, self.expected, min_matches=2)

    def test_a_license_without_machine_lock_corresponds_to_no_machine(self):
        # Una huella vacía que coincidiera con todo es una sola licencia forjada corriendo
        # en cualquier equipo, que es justo lo que la huella existe para impedir.
        assert not fingerprint.is_match({"board_uuid": "x"}, {}, min_matches=0)
        assert not fingerprint.is_match({}, {}, min_matches=0)

    def test_zero_quorum_with_components_does_not_match_anything(self):
        # Una licencia que declara huella pero pide cero coincidencias sería una puerta
        # abierta escrita como si estuviera cerrada.
        assert not fingerprint.is_match({}, self.expected, min_matches=0)


class TestMachineKey:
    def test_same_components_give_the_same_key(self):
        components = {"board_uuid": "aa", "disk_serial": "bb"}
        assert fingerprint.build_machine_key(components) == \
               fingerprint.build_machine_key(dict(reversed(list(components.items()))))

    def test_different_components_give_different_keys(self):
        assert fingerprint.build_machine_key({"board_uuid": "aa"}) != \
               fingerprint.build_machine_key({"board_uuid": "bb"})

    def test_empty_components_still_give_a_usable_key(self):
        assert len(fingerprint.build_machine_key({})) == 32


class TestRunProbe:
    """
    Cómo se lanza la consulta al sistema, que es lo que decide si anda compilada.

    El compilado es el único que importa acá: desde fuentes el estado es
    `unlicensed_build` y la huella no se usa para nada.
    """

    def test_the_probe_does_not_inherit_stdin(self, monkeypatch):
        """
        Sin esto, subprocess hereda el STD_INPUT_HANDLE del proceso.

        Con `--windows-console-mode=attach` ese handle queda atado a la consola que lanzó
        el ejecutable, y el lanzador del entregable hace `start` y cierra la suya: queda
        un handle inválido y `subprocess` muere con WinError 6 al duplicarlo, antes de
        llegar a correr la consulta. La sonda no lee nada de stdin, así que no hay motivo
        para heredarlo.
        """
        seen = {}

        def _fake_run(argv, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        monkeypatch.setattr(fingerprint.subprocess, "run", _fake_run)
        fingerprint._run_probe(["wmic", "csproduct", "get", "uuid"])
        assert seen["stdin"] == subprocess.DEVNULL

    def test_a_probe_that_cannot_run_is_a_warning_and_not_an_exception(self, monkeypatch):
        """El contrato del módulo: una fuente ilegible es un dato, no una excepción."""
        def _fake_run(argv, **kwargs):
            raise OSError(6, "El identificador no es valido")

        monkeypatch.setattr(fingerprint.subprocess, "run", _fake_run)
        assert fingerprint._run_probe(["wmic"]) == ""


class TestReadComponents:
    def test_reading_the_real_machine_never_raises(self):
        # El contrato del módulo: una fuente ilegible es un dato, no una excepción. Este
        # test corre en Windows, en la Jetson y en un contenedor sin DMI.
        components = fingerprint.read_components(refresh=True)
        assert isinstance(components, dict)
        assert all(len(digest) == 64 for digest in components.values())

    def test_the_result_is_cached_between_calls(self):
        first = fingerprint.read_components()
        assert fingerprint.read_components() == first

    def test_the_caller_cannot_mutate_the_cache(self):
        components = fingerprint.read_components()
        components["board_uuid"] = "envenenado"
        assert fingerprint.read_components().get("board_uuid") != "envenenado"
