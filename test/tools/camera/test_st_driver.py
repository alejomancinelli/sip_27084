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

    def update_device_list(self):
        self._system.refreshes += 1

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
        self.refreshes = 0
        self.driver = None

    def get_interface(self, index: int):
        return _Interface(self)

    def update_interface_list(self):
        self.refreshes += 1


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


class TestDeviceListRefresh:
    """
    Buscar por dirección tiene que re-enumerar antes.

    La lista se arma al crear el system y no se actualiza sola: la cámara que volvió sigue
    figurando con su IP, así que la traducción a device_id da bien y `create_device_by_id`
    falla igual. Es el segundo motivo por el que sólo se recuperaba reiniciando.
    """

    def test_it_re_enumerates_before_looking_up_by_address(self, system):
        driver = st_driver.StDriver({"address": "10.8.1.130", "acquisition": {}})
        driver._create_configured_device(system)
        assert system.refreshes > 0

    def test_a_transport_without_refresh_is_not_an_error(self, system):
        """Se sigue con la lista que haya en vez de dejar la cámara sin abrir."""
        def _explota():
            raise RuntimeError("no soportado")
        system.update_interface_list = _explota
        driver = st_driver.StDriver({"address": "", "acquisition": {}})
        system.create_first_device = lambda: _Device("cam-1")
        assert driver._create_configured_device(system) is not None


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


class TestWhyItCouldNotOpen:
    """
    «No está» y «está y no abre» se arreglan en lugares opuestos, así que se dicen
    distinto: lo primero es cable, IP o subred; lo segundo es acceso excluyente.
    """

    def _driver_that_resolves(self, monkeypatch, system):
        driver = st_driver.StDriver({"address": "10.8.1.130", "acquisition": {}})
        monkeypatch.setattr(driver, "_find_device_id_by_ip",
                            lambda _interface, _ip: "D4:7C:44:30:14:82")
        system.driver = driver
        return driver

    def test_found_but_not_openable_says_so(self, monkeypatch, system, caplog):
        system.is_plugged = False          # el doble hace fallar create_device_by_id
        driver = self._driver_that_resolves(monkeypatch, system)

        with caplog.at_level("ERROR"):
            assert driver._create_configured_device(system) is None

        assert "no se pudo abrir" in caplog.text
        assert "device lost" in caplog.text, "el motivo real tiene que salir"
        assert "No hay ninguna cámara" not in caplog.text

    def test_not_found_still_says_not_found(self, monkeypatch, system, caplog):
        """Sin resolución de la dirección, la cámara de verdad no está."""
        driver = st_driver.StDriver({"address": "10.8.1.130", "acquisition": {}})
        monkeypatch.setattr(driver, "_find_device_id_by_ip", lambda _i, _ip: None)
        system.driver = driver

        with caplog.at_level("ERROR"):
            assert driver._create_configured_device(system) is None

        assert "No hay ninguna cámara" in caplog.text
        assert "no se pudo abrir" not in caplog.text


class _Datastream:
    """Datastream que falla N veces y después entrega un buffer."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    def retrieve_buffer(self, timeout_ms: int):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(f"timeout tras {timeout_ms} ms")
        return "buffer"


class TestRetrieveBuffer:
    """
    Esperar la tolerancia entera de una vez deja al hilo sordo al cierre, y entonces la
    limpieza de `_do_grabbing` no corre y la cámara queda reservada.
    """

    def _driver(self, datastream) -> st_driver.StDriver:
        driver = st_driver.StDriver({"address": "10.8.1.130", "acquisition": {}})
        driver._st_datastream = datastream
        driver._grab_active = True
        return driver

    def test_a_slice_without_a_frame_is_not_a_failure(self):
        driver = self._driver(_Datastream(failures=3))
        assert driver._retrieve_buffer() == "buffer"

    def test_it_asks_in_slices_and_not_for_the_whole_tolerance(self):
        datastream = _Datastream(failures=1)
        self._driver(datastream)._retrieve_buffer()
        assert datastream.calls == 2, "insistió, no esperó una sola vez"

    def test_closing_wins_over_waiting(self, monkeypatch):
        """Lo que arregla el bug: el cierre se nota sin esperar la tolerancia entera."""
        driver = self._driver(_Datastream(failures=999))

        original = driver._st_datastream.retrieve_buffer

        def _stop_asking(timeout_ms):
            driver._grab_active = False    # como `disconnect()` desde otro hilo
            return original(timeout_ms)

        driver._st_datastream.retrieve_buffer = _stop_asking
        assert driver._retrieve_buffer() is None

    def test_it_gives_up_when_the_tolerance_runs_out(self, monkeypatch):
        """Una cámara muda de verdad sigue siendo una pérdida, y sube como antes."""
        monkeypatch.setattr(st_driver, "_RETRIEVE_TIMEOUT_MS", 0)
        driver = self._driver(_Datastream(failures=999))
        with pytest.raises(RuntimeError):
            driver._retrieve_buffer()


class _NodeHandle:
    def __init__(self, nodemap, name: str):
        self.nodemap, self.name = nodemap, name


class _Accessor:
    """PyIFloat / PyIEnumeration / PyIInteger: cualquiera de las tres anota lo escrito."""

    def __init__(self, handle: _NodeHandle):
        self._handle = handle

    def set_value(self, value):
        self._handle.nodemap.written[self._handle.name] = value

    set_symbolic_value = set_value


class _Nodemap:
    def __init__(self):
        self.written: dict = {}

    def get_node(self, name: str) -> _NodeHandle:
        return _NodeHandle(self, name)


class TestCameraParams:
    """
    Lo que el driver **declara** en la cámara, en vez de heredarlo de su memoria.

    Son los parámetros que deciden si llega alguna imagen, y viven en la cámara: una que
    viene de otro montaje llega con ellos puestos y el síntoma es un timeout de captura,
    que se lee como un problema de red.
    """

    @pytest.fixture
    def nodemap(self, monkeypatch) -> _Nodemap:
        monkeypatch.setattr(st_driver, "st",
                            type("_St", (), {"PyIFloat": _Accessor,
                                             "PyIEnumeration": _Accessor,
                                             "PyIInteger": _Accessor}))
        return _Nodemap()

    def _apply(self, nodemap, acquisition: dict):
        st_driver.StDriver({"address": "10.8.1.130", "acquisition": acquisition}) \
            ._apply_camera_params(nodemap)
        return nodemap.written

    def test_free_run_is_declared_and_not_inherited(self, nodemap):
        """Una cámara con TriggerMode en On abre, se configura y no entrega un frame."""
        assert self._apply(nodemap, {})["TriggerMode"] == "Off"

    def test_the_packet_size_is_left_alone_unless_it_is_configured(self, nodemap):
        """Sin la clave no se toca: lo que la cámara traiga puede ser lo correcto."""
        written = self._apply(nodemap, {})
        assert "GevSCPSPacketSize" not in written
        assert "GevSCPD" not in written

    def test_the_packet_size_is_written_when_configured(self, nodemap):
        """Es el único camino para corregir un MTU en un equipo sin visor del fabricante."""
        written = self._apply(nodemap, {"packet_size_bytes": 1500, "packet_delay_ns": 1000})
        assert written["GevSCPSPacketSize"] == 1500
        assert written["GevSCPD"] == 1000

    def test_a_zero_means_do_not_touch(self, nodemap):
        written = self._apply(nodemap, {"packet_size_bytes": 0, "packet_delay_ns": 0})
        assert "GevSCPSPacketSize" not in written
        assert "GevSCPD" not in written


class _Session:
    """Device de StApi con lo que `_do_grabbing` le pide a una sesión."""

    def __init__(self, datastream_fails: bool = False):
        self.info = type("_Info", (), {"device_id": "id", "display_name": "doble"})()
        self.remote_port = type("_Port", (), {"nodemap": _Nodemap()})()
        self.local_port = type("_Port", (), {"nodemap": _Nodemap()})()
        self.events_stopped = 0
        self._datastream_fails = datastream_fails

    def create_datastream(self):
        if self._datastream_fails:
            raise RuntimeError("no se pudo crear el datastream")
        return _Datastream(failures=0)

    def start_event_acquisition(self):
        pass

    def stop_event_acquisition(self):
        self.events_stopped += 1


class TestTheSessionIsAlwaysReleased:
    """
    El bug que dejaba la cámara tomada por el propio proceso.

    Un timeout de captura sale de `_do_grabbing` por excepción. Con la limpieza en línea
    después del `while` no corría: la adquisición quedaba abierta y `_st_device` seguía
    apuntando a la sesión muerta, así que el reintento encontraba la cámara en la red y no
    la podía abrir —acceso excluyente contra sí mismo— y no salía nunca. «SIN SEÑAL» en la
    vista y un log que repite que la encuentra y no conecta.
    """

    @pytest.fixture
    def driver(self, monkeypatch) -> st_driver.StDriver:
        monkeypatch.setattr(st_driver, "st",
                            type("_St", (), {"PyIFloat": _Accessor,
                                             "PyIEnumeration": _Accessor,
                                             "PyIInteger": _Accessor,
                                             "EGCCallbackType": None}))
        made = st_driver.StDriver({"address": "10.8.1.130", "acquisition": {}})
        made._grab_active = True
        return made

    def test_a_capture_error_still_releases_it(self, driver, monkeypatch):
        session = _Session()
        monkeypatch.setattr(driver, "_grab_frames",
                            lambda: (_ for _ in ()).throw(RuntimeError("grab timed out")))

        with pytest.raises(RuntimeError):
            driver._do_grabbing(session)

        assert driver._st_device is None, "la cámara quedó tomada por este proceso"
        assert driver._st_datastream is None
        assert driver.is_connected is False
        assert session.events_stopped == 1

    def test_a_clean_stop_releases_it_too(self, driver, monkeypatch):
        session = _Session()
        monkeypatch.setattr(driver, "_grab_frames", lambda: None)

        driver._do_grabbing(session)

        assert driver._st_device is None
        assert driver._st_datastream is None

    def test_a_datastream_that_fails_to_open_releases_it(self, driver):
        """`create_datastream()` también puede fallar, y ahí ya hay algo que soltar."""
        with pytest.raises(RuntimeError):
            driver._do_grabbing(_Session(datastream_fails=True))

        assert driver._st_device is None
