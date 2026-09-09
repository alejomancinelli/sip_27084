"""Tests del estado de reloj: qué pasa cuando el archivo falta, cuando lo editan y
cuando alguien atrasa la fecha del equipo."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from system.license import clock

MACHINE_KEY = b"clave-de-maquina-de-prueba-32byt"
OTHER_KEY = b"otra-clave-de-maquina-de-prueba!!"


@pytest.fixture
def guard(tmp_path):
    return clock.ClockGuard(str(tmp_path / "data" / "license_state.bin"), MACHINE_KEY)


def at(*, hours: float = 0) -> datetime:
    # Con microsegundos a propósito: es lo que devuelve `datetime.now()`, y su punto
    # decimal vive adentro del payload que se firma. Un instante redondo no ejercita eso.
    return datetime(2026, 9, 7, 12, 0, 0, 854644, tzinfo=timezone.utc) + timedelta(hours=hours)


class TestRoundTrip:
    def test_saving_creates_the_directory(self, guard, tmp_path):
        assert guard.save_last_seen_utc(at())
        assert os.path.isfile(tmp_path / "data" / "license_state.bin")

    def test_what_is_saved_comes_back(self, guard):
        guard.save_last_seen_utc(at())
        assert guard.read_last_seen_utc() == at()

    def test_the_microseconds_of_a_real_clock_survive(self, guard):
        # El instante ISO trae su propio punto decimal y el estado se separa por punto.
        # Partir por el primero rompía el HMAC y dejaba la detección de retroceso muerta
        # en producción, donde el reloj nunca cae en un segundo redondo.
        now = datetime.now(timezone.utc)
        guard.save_last_seen_utc(now)
        assert guard.read_last_seen_utc() == now

    def test_a_missing_file_is_not_an_error(self, guard):
        # Es la primera corrida del equipo, no un ataque.
        assert guard.read_last_seen_utc() is None

    def test_an_unwritable_path_does_not_raise(self, tmp_path):
        blocked = tmp_path / "archivo"
        blocked.write_text("soy un archivo, no una carpeta")
        guard = clock.ClockGuard(str(blocked / "estado.bin"), MACHINE_KEY)
        assert not guard.save_last_seen_utc(at())


class TestTampering:
    def test_an_edited_timestamp_is_rejected(self, guard, tmp_path):
        guard.save_last_seen_utc(at())
        state_path = tmp_path / "data" / "license_state.bin"
        state_path.write_text(state_path.read_text().replace("2026", "2020"))
        assert guard.read_last_seen_utc() is None

    def test_the_state_of_another_machine_is_rejected(self, guard, tmp_path):
        # Copiar el archivo de un equipo a otro no sirve: la clave sale de la huella.
        guard.save_last_seen_utc(at())
        foreign = clock.ClockGuard(str(tmp_path / "data" / "license_state.bin"), OTHER_KEY)
        assert foreign.read_last_seen_utc() is None

    def test_a_truncated_file_is_rejected(self, guard, tmp_path):
        state_path = tmp_path / "data" / "license_state.bin"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("solo-payload-sin-firma")
        assert guard.read_last_seen_utc() is None


class TestRollback:
    def test_no_previous_state_is_not_a_rollback(self):
        assert not clock.has_rolled_back(at(), None)

    def test_the_clock_moving_forward_is_normal(self):
        assert not clock.has_rolled_back(at(hours=1), at())

    def test_a_clock_moved_back_a_year_is_a_rollback(self):
        assert clock.has_rolled_back(at(), at(hours=24 * 365))

    def test_an_ntp_correction_of_seconds_is_not_a_rollback(self):
        # Un cliente NTP corrige unos segundos hacia atrás cada tanto y eso no es un ataque.
        nudge = clock.ROLLBACK_TOLERANCE_S / 2
        assert not clock.has_rolled_back(at(), at() + timedelta(seconds=nudge))
