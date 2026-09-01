"""
Tests del driver Sentech: la reconexión del thread de captura.

No hay cámara ni StApi de verdad: se reemplaza el módulo `st` por un doble y se corre
`_grab_loop` directamente. Lo que se verifica es la máquina de reintentos, que es donde
estaba el bug — el resto del driver es la SDK y no se puede afirmar sin hardware.
"""

import pytest

from tools.camera import st_driver

_MAX_ID_LOOKUPS = 5   # tope del doble, no del driver: ver create_device_by_id


class _Interface:
    """Interfaz de StApi: entrega la cámara por device_id, o falla si ya no está."""

    def __init__(self, system):
        self._system = system

    def create_device_by_id(self, device_id: str):
        self._system.by_id_calls.append(device_id)
        # Corta el loop si se insiste demasiado con el mismo id. Sin esto, un driver que
        # no limpia el id cacheado deja el test colgado en vez de hacerlo fallar, y un
        # test que cuelga no avisa nada.
        if len(self._system.by_id_calls) > _MAX_ID_LOOKUPS:
            self._system.driver._grab_active = False
        if not self._system.is_plugged:
            raise RuntimeError("device lost")
        return _Device(device_id)


class _Device:
    def __init__(self, device_id: str):
        self.info = type("_Info", (), {"device_id": device_id,
                                       "display_name": "doble"})()


class _System:
    """Sistema de StApi con una sola interfaz y la cámara enchufable a voluntad."""

    interface_count = 1

    def __init__(self):
        self.is_plugged = True
        self.by_id_calls: list = []
        self.by_address_calls = 0
        self.driver = None

    def get_interface(self, index: int):
        return _Interface(self)


@pytest.fixture
def system(monkeypatch):
    """Deja el driver corriendo contra el doble, sin esperas y sin StApi real."""
    stub = _System()
    monkeypatch.setattr(st_driver, "st",
                        type("_St", (), {"initialize": staticmethod(lambda: None),
                                         "create_system": staticmethod(lambda: stub)}))
    monkeypatch.setattr(st_driver, "_RETRY_DELAY_S", 0)
    return stub


def _driver(system, *, grabs_before_stop: int):
    """
    Un driver cuyo `_do_grabbing` cuenta y corta el loop después de N adquisiciones.

    `_do_grabbing` es la SDK entera; acá sólo interesa cuántas veces se llegó a ella y con
    qué cámara, así que se reemplaza.
    """
    driver = st_driver.StDriver({"address": "10.8.1.130", "acquisition": {}})
    driver.grabbed: list = []

    def _fake_grabbing(st_device):
        driver.grabbed.append(st_device.info.device_id)
        if len(driver.grabbed) >= grabs_before_stop:
            driver._grab_active = False

    def _fake_create(_system):
        system.by_address_calls += 1
        return _Device("cam-1") if system.is_plugged else None

    driver._do_grabbing = _fake_grabbing
    driver._create_configured_device = _fake_create
    driver._grab_active = True
    system.driver = driver
    return driver


class TestGrabLoopReconnect:
    def test_the_first_pass_enumerates_by_address(self, system):
        driver = _driver(system, grabs_before_stop=1)
        driver._grab_loop()
        assert system.by_address_calls == 1
        assert driver.grabbed == ["cam-1"]

    def test_while_the_camera_answers_it_reuses_the_cached_id(self, system):
        """Enumerar por dirección en cada vuelta sería recorrer todas las interfaces."""
        driver = _driver(system, grabs_before_stop=3)
        driver._grab_loop()
        assert system.by_address_calls == 1
        assert system.by_id_calls == ["cam-1", "cam-1"]

    def test_it_reconnects_after_the_camera_comes_back(self, system):
        """
        El bug: una cámara que se desenchufa y vuelve dejaba al driver preguntando para
        siempre por un id que ya no resuelve, y sólo se recuperaba reiniciando la app.

        Acá se desenchufa después de la primera adquisición y vuelve dos reintentos
        después. Sin limpiar el id cacheado, `grabbed` se queda en una sola entrada.
        """
        driver = _driver(system, grabs_before_stop=2)
        driver._do_grabbing = _unplug_after_first(driver, system)
        driver._create_configured_device = _replug_on_next_lookup(system)

        driver._grab_loop()

        assert driver.grabbed == ["cam-1", "cam-1"], "no se reconectó"
        assert system.by_address_calls == 2, "no volvió a enumerar por dirección"

    def test_a_camera_that_never_returns_keeps_looking_by_address(self, system):
        """Sigue reintentando, pero por dirección: es la única que la puede encontrar."""
        system.is_plugged = False
        driver = _driver(system, grabs_before_stop=1)
        driver._create_configured_device = _give_up_after(system, attempts=4, driver=driver)

        driver._grab_loop()

        assert driver.grabbed == []
        assert system.by_address_calls == 4


def _unplug_after_first(driver, system):
    """La cámara se cae apenas empieza a capturar, como un desenchufe."""
    def _grabbing(st_device):
        driver.grabbed.append(st_device.info.device_id)
        if len(driver.grabbed) >= 2:
            driver._grab_active = False
        system.is_plugged = False
    return _grabbing


def _replug_on_next_lookup(system):
    """La cámara vuelve a estar en la red justo cuando se la busca por dirección."""
    def _create(_system=None):
        system.by_address_calls += 1
        system.is_plugged = True
        return _Device("cam-1")
    return _create


def _give_up_after(system, *, attempts: int, driver):
    """Nunca vuelve; corta el loop para que el test termine."""
    def _create(_system=None):
        system.by_address_calls += 1
        if system.by_address_calls >= attempts:
            driver._grab_active = False
        return None
    return _create
