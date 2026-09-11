import threading
import time
from queue import Queue, Empty

import cv2
import numpy as np

from system.logger import logger

from .abstract_driver import AbstractCameraDriver

try:
    import stapipy as st
    _STAPI_AVAILABLE = True
    _IMPORT_ERROR = ""
except ImportError as error:
    _STAPI_AVAILABLE = False
    # Se guarda el motivo porque son dos problemas distintos que se arreglan en lugares
    # distintos: que **falte el módulo** y que el módulo esté pero **no carguen sus DLL
    # nativas**. Windows levanta `ImportError` en los dos casos, así que sin el texto el
    # aviso manda a instalar algo que ya está.
    _IMPORT_ERROR = str(error)


def _import_hint() -> str:
    """Qué mirar, según por qué no se pudo importar stapipy."""
    if "DLL load failed" in _IMPORT_ERROR:
        return ("El módulo está pero sus DLL nativas no cargan: en este equipo falta el "
                "SentechSDK, o el que está instalado no es el que corresponde a esta "
                "versión de stapipy —los nombres de sus DLL llevan la versión adentro—.")
    return "Falta el módulo stapipy, que se instala desde el SentechSDK."

# Serializa lo que en StApi es global al proceso y no es thread-safe: el arranque de la
# librería y el alta del hilo de captura. **No alcanza con tomarlo en `connect()`**: el
# trabajo con StApi lo hace el hilo que `connect()` levanta, y el lock ya está soltado
# cuando ese hilo arranca. Por eso `_grab_loop` lo toma otra vez para inicializar.
_stapi_lock = threading.Lock()

# Nodos GenICam del evento DeviceLost.
_EVENT_SELECTOR        = "EventSelector"
_EVENT_NOTIFICATION    = "EventNotification"
_EVENT_NOTIFICATION_ON = "On"
_TARGET_EVENT          = "DeviceLost"
_CALLBACK_NODE         = "EventDeviceLost"

# Sensor de temperatura que se lee en get_status().
_TEMPERATURE_SOURCE = "Mainboard"

# Nodos del nodemap de la interface: publican la IP de cada cámara sin abrirla.
_DEVICE_SELECTOR_NODE = "DeviceSelector"
_DEVICE_IP_NODE = "GevDeviceIPAddress"

_DEFAULT_EXPOSURE_TIME_US = 9000.0
_DEFAULT_GAIN = 0.0
_DEFAULT_FPS_LIMIT = 15.0

_PAUSE_POLL_S = 0.2     # cada cuánto revisa el thread si volvió la captura
_QUEUE_MAXSIZE = 2      # cola corta: semántica always-fresh
_CONNECT_TIMEOUT_S = 5  # espera máxima a que el thread confirme la adquisición
_CONNECT_POLL_S = 0.1
_JOIN_TIMEOUT_S = 5.0
_RETRY_DELAY_S = 5      # entre reintentos de conexión del thread de captura

# Timeout holgado para cubrir la renegociación GigE y el arranque de firmware
# después de una reconexión. Si retrieve_buffer expira, la cámara se quedó muda
# de verdad y la excepción sube a _grab_loop, que reintenta la conexión.
_RETRIEVE_TIMEOUT_MS = 30000
# En tajadas de esto, para que el hilo mire si tiene que cerrar. Ver `_retrieve_buffer`:
# esperar los 30 s de una vez deja la cámara reservada cuando se cierra la app.
_RETRIEVE_SLICE_MS = 500


def _refresh_device_list(st_system):
    """
    Vuelve a enumerar interfaces y cámaras.

    Cada llamada es un descubrimiento por broadcast, así que sólo se hace al buscar por
    dirección: mientras la cámara responde, el loop de captura reusa su device_id y no
    pasa por acá. Un transporte que no soporte alguna de las dos no es un error: se sigue
    con la lista que haya.
    """
    try:
        st_system.update_interface_list()
    except Exception as e:
        logger.debug(f"[StDriver] No se pudo actualizar la lista de interfaces: {e}")
    for index in range(st_system.interface_count):
        try:
            st_system.get_interface(index).update_device_list()
        except Exception as e:
            logger.debug(f"[StDriver] No se pudo actualizar la lista de cámaras: {e}")


def _looks_like_ip(text: str) -> bool:
    """True si el texto tiene forma de IPv4. No valida que la dirección exista."""
    parts = text.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) < 256 for p in parts)


def _format_ip(packed: int) -> str:
    """Pasa a texto la IP que GenICam publica empaquetada en un uint32."""
    return ".".join(str((packed >> shift) & 0xFF) for shift in (24, 16, 8, 0))


class StDriver(AbstractCameraDriver):
    """
    Implementación de AbstractCameraDriver para cámaras Sentech GigE vía StApi.

    Claves de config_cam que usa:
        address     : IP o device_id (la MAC); si no coincide, cae a la primera
        acquisition:
            exposure_time_us : float
            gain             : float
            fps_limit        : float
        rotation    : opcional — la aplica la base
    """

    def __init__(self, config_cam: dict):
        super().__init__(config_cam)
        self._address = config_cam.get("address", "unknown")
        self._acquisition = config_cam.get("acquisition", {})
        self.is_connected = False

        self._img_queue: Queue = Queue(maxsize=_QUEUE_MAXSIZE)
        self._grab_active = False
        self._force_disconnect = False
        self._grab_thread: threading.Thread | None = None
        self._nodemap = None
        # Device y datastream vivos: los necesita _apply_capture_enabled para cortar la
        # adquisición desde el thread del llamador.
        self._st_device = None
        self._st_datastream = None
        # Anti-spam del warning de temperatura (ver get_status).
        self._temp_warned = False

    # ── AbstractCameraDriver interface ────────────────────────────────────────

    def connect(self) -> bool:
        if not _STAPI_AVAILABLE:
            logger.error(f"[StDriver] No se pudo cargar stapipy ({_IMPORT_ERROR}). "
                         f"{_import_hint()} No se puede conectar.")
            return False

        with _stapi_lock:
            # _grab_loop maneja la reconexión por su cuenta: no levantar un segundo
            # thread si ya hay uno vivo (pasa cuando get_frame pone is_connected en
            # False por un timeout transitorio y el thread de captura reintenta).
            if self._grab_thread and self._grab_thread.is_alive():
                return self.is_connected

            self._grab_active = True
            self._grab_thread = threading.Thread(
                target=self._grab_loop, daemon=True, name=f"StDriver-{self._address}"
            )
            self._grab_thread.start()

        deadline = time.time() + _CONNECT_TIMEOUT_S
        while not self.is_connected and time.time() < deadline:
            time.sleep(_CONNECT_POLL_S)

        if not self.is_connected:
            logger.error(f"[StDriver] {self._address} no conectó en {_CONNECT_TIMEOUT_S} s.")
            self._grab_active = False
            return False

        return True

    def disconnect(self):
        self._grab_active = False
        if self._grab_thread and self._grab_thread.is_alive():
            self._grab_thread.join(timeout=_JOIN_TIMEOUT_S)
        self._grab_thread = None
        self._nodemap = None
        self.is_connected = False
        logger.info(f"[StDriver] {self._address} desconectada.")

    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        if not self._capture_enabled or not self.is_connected:
            return None
        try:
            frame = self._img_queue.get(timeout=timeout_ms / 1000.0)
            return self._deliver(frame)
        except Empty:
            return None
        except Exception as e:
            logger.warning(f"[StDriver {self._address}] Error en get_frame: {e}")
            return None

    def get_status(self) -> dict:
        """Estado para telemetría. Un 0.0 en `temperature` significa sin lectura."""
        status = {
            "connected": self.is_connected,
            "capture_enabled": self._capture_enabled,
            "temperature": 0.0,
            "fps_estimated": self._measured_fps(),
        }
        if self.is_connected and self._nodemap is not None:
            try:
                temp_node = self._nodemap.get_node("DeviceTemperature")
                status["temperature"] = float(st.PyIFloat(temp_node).value)
                self._temp_warned = False
            except Exception as e:
                # Sin el latch esto inunda el log.
                if not self._temp_warned:
                    logger.warning(
                        f"[StDriver {self._address}] No se pudo leer la temperatura: {e}. "
                        f"La temperatura queda en 0 (sin lectura)."
                    )
                    self._temp_warned = True
        return status

    def _apply_capture_enabled(self):
        # Se vacía la cola para que al rehabilitar no salga un frame viejo.
        while not self._img_queue.empty():
            try:
                self._img_queue.get_nowait()
            except Empty:
                break

        # La adquisición la corta el propio thread de captura, no este: llamar a
        # acquisition_stop() desde afuera se traba contra el retrieve_buffer() que el
        # thread tiene pendiente. Mientras la cámara transmite, ese retrieve vuelve una
        # vez por frame (~66 ms a 15 fps), así que el thread ve la bandera enseguida.
        logger.info(
            f"[StDriver] {self._address}: captura "
            f"{'habilitada' if self._capture_enabled else 'deshabilitada'}."
        )

    def _start_acquisition(self):
        self._st_datastream.start_acquisition()
        self._st_device.acquisition_start()

    def _stop_acquisition(self):
        """Corta la transmisión. Silenciosa: puede estar ya parada o sin device."""
        try:
            if self._st_device is not None:
                self._st_device.acquisition_stop()
        except Exception:
            pass
        try:
            if self._st_datastream is not None:
                self._st_datastream.stop_acquisition()
        except Exception:
            pass

    # ── Parámetros de cámara ─────────────────────────────────────────────────

    def _apply_camera_params(self, nodemap):
        def set_float(name: str, value: float):
            try:
                st.PyIFloat(nodemap.get_node(name)).set_value(float(value))
            except Exception as e:
                logger.warning(f"[StDriver] No se pudo escribir {name}: {e}")

        def set_enum(name: str, value: str):
            try:
                st.PyIEnumeration(nodemap.get_node(name)).set_symbolic_value(value)
            except Exception as e:
                logger.warning(f"[StDriver] No se pudo escribir {name}={value}: {e}")

        def set_int(name: str, value: int):
            try:
                st.PyIInteger(nodemap.get_node(name)).set_value(int(value))
            except Exception as e:
                logger.warning(f"[StDriver] No se pudo escribir {name}: {e}")

        # **Se declara free-run explícitamente.** El ritmo lo pone la cámara, y el modo de
        # disparo vive en su memoria no volátil: una que llega de otro montaje con
        # `TriggerMode` en On abre, acepta todos estos parámetros, arranca la adquisición y
        # no entrega un solo frame. El síntoma es un timeout de captura, que se lee como un
        # problema de red y manda a mirar el switch.
        set_enum("TriggerMode", "Off")
        set_enum("ExposureAuto", "Off")
        set_float(
            "ExposureTime",
            self._acquisition.get("exposure_time_us", _DEFAULT_EXPOSURE_TIME_US),
        )
        set_enum("GainAuto", "Off")
        set_float("Gain", self._acquisition.get("gain", _DEFAULT_GAIN))
        set_float("AcquisitionFrameRate", self._acquisition.get("fps_limit", _DEFAULT_FPS_LIMIT))

        # Tamaño de paquete y demora entre paquetes: los dos deciden si llega alguna
        # imagen, y los dos son de la cámara y no del equipo. Un paquete más grande que el
        # MTU de la placa se descarta entero —control perfecto, cero imágenes— y sin esta
        # clave la única forma de arreglarlo sería el visor del fabricante, que en la planta
        # no está. Vacío o 0 = no se toca lo que la cámara ya tenga.
        packet_size_bytes = int(self._acquisition.get("packet_size_bytes", 0) or 0)
        if packet_size_bytes > 0:
            set_int("GevSCPSPacketSize", packet_size_bytes)
        packet_delay_ns = int(self._acquisition.get("packet_delay_ns", 0) or 0)
        if packet_delay_ns > 0:
            set_int("GevSCPD", packet_delay_ns)

        # Se fija el selector una sola vez para que get_status() solo tenga que leer.
        set_enum("DeviceTemperatureSelector", _TEMPERATURE_SOURCE)

    # ── Loop de adquisición ──────────────────────────────────────────────────

    def _do_grabbing(self, st_device):
        """Bucle de captura. Bloquea hasta que _grab_active baja o se pierde el device."""
        self._nodemap = st_device.remote_port.nodemap
        self._apply_camera_params(self._nodemap)

        # El callback de DeviceLost no es crítico: si no está disponible, se sigue.
        try:
            local_map = st_device.local_port.nodemap
            local_map.get_node(_CALLBACK_NODE).register_callback(
                self._on_device_lost, st_device, st.EGCCallbackType.OutsideLock
            )
            event_selector = st.PyIEnumeration(local_map.get_node(_EVENT_SELECTOR))
            event_selector.set_symbolic_value(_TARGET_EVENT)
            event_notification = st.PyIEnumeration(local_map.get_node(_EVENT_NOTIFICATION))
            event_notification.set_symbolic_value(_EVENT_NOTIFICATION_ON)
            st_device.start_event_acquisition()
        except Exception as e:
            logger.debug(f"[StDriver] Evento DeviceLost no disponible: {e}")

        # Desde acá va todo en el `try`: en cuanto `_st_device` queda puesto hay algo que
        # soltar, y `create_datastream()` también puede fallar.
        self._st_device = st_device
        try:
            self._st_datastream = st_device.create_datastream()

            self._force_disconnect = False
            self.is_connected = True
            # Nueva conexión: puede ser otra cámara o el mismo nodo ya disponible, así que
            # se permite volver a avisar si la temperatura sigue sin leerse.
            self._temp_warned = False
            logger.info(
                f"[StDriver] {self._address} ({st_device.info.display_name}) capturando."
            )
            self._grab_frames()
        finally:
            self._release_session(st_device)

    def _release_session(self, st_device):
        """
        Cierra la sesión: para la adquisición y suelta el device y el datastream.

        **Va en un `finally`, y ahí estaba el bug.** Un timeout de captura sube desde
        `retrieve_buffer` y sale de `_do_grabbing` por excepción, así que esto —que era
        código en línea después del `while`— no corría. La adquisición quedaba abierta y
        `_st_device`/`_st_datastream` seguían apuntando a la sesión muerta, o sea que la
        cámara seguía abierta **por este mismo proceso**: el reintento la encontraba en la
        red y no la podía abrir por acceso excluyente, y de ahí no salía más. La vista
        quedaba en «SIN SEÑAL» y el log repetía que la encuentra y no conecta.

        Aparecía después de unos minutos —hace falta un solo hipo del stream para entrar— y
        antes en la máquina más lenta, que es la que más paquetes pierde.
        """
        self._stop_acquisition()
        try:
            st_device.stop_event_acquisition()
        except Exception:
            pass

        self._nodemap = None
        self._st_device = None
        self._st_datastream = None
        self.is_connected = False

    def _grab_frames(self):
        """Entrega frames hasta que baje `_grab_active` o se pierda la cámara."""
        is_streaming = False
        while self._grab_active and not self._force_disconnect:
            # Deshabilitada, la cámara se para de verdad: no es un filtro de frames,
            # el cable queda libre.
            if not self._capture_enabled:
                if is_streaming:
                    self._stop_acquisition()
                    is_streaming = False
                time.sleep(_PAUSE_POLL_S)
                continue

            if not is_streaming:
                self._start_acquisition()
                is_streaming = True

            pending_buffer = self._retrieve_buffer()
            if pending_buffer is None:
                break
            with pending_buffer as st_buffer:
                if self._force_disconnect:
                    break
                if not st_buffer.info.is_image_present:
                    continue

                frame = self._decode_buffer(st_buffer)
                if frame is None:
                    continue

                # Always-fresh: se descarta el más viejo si el consumidor va lento.
                if self._img_queue.full():
                    try:
                        self._img_queue.get_nowait()
                    except Empty:
                        pass
                self._img_queue.put_nowait(frame)

    def _retrieve_buffer(self):
        """
        Espera un buffer sin quedarse sordo al cierre. None si hay que salir.

        `retrieve_buffer` bloquea hasta su timeout, y pedirle la tolerancia entera de una
        vez deja al hilo sin mirar `_grab_active` durante todo ese rato. Ahí el cierre se
        cae solo: `disconnect()` lo espera `_JOIN_TIMEOUT_S`, se cansa, el proceso termina
        y **el bloque de limpieza de `_do_grabbing` no corre nunca**. La cámara se queda
        con su adquisición abierta y reservada hasta que expire su heartbeat, así que el
        arranque siguiente la ve en la red y no la puede abrir. Pasó en la planta: se cerró
        la app con el stream ya muerto y la reapertura falló.

        Cortar desde afuera no es una opción: `acquisition_stop()` llamado desde otro hilo
        se traba contra el `retrieve_buffer()` pendiente —ver `_apply_capture_enabled`—,
        así que la única salida es que este hilo pregunte seguido.

        De ahí las tajadas: la tolerancia total ante una cámara muda es la misma y el
        cierre se nota en medio segundo. Si se agota la tolerancia, propaga el error de la
        última tajada, que es lo que el bucle de arriba trata como pérdida de la cámara.
        """
        deadline_s = time.monotonic() + _RETRIEVE_TIMEOUT_MS / 1000.0
        while self._grab_active and not self._force_disconnect:
            try:
                return self._st_datastream.retrieve_buffer(_RETRIEVE_SLICE_MS)
            except Exception:
                # Una tajada sin frame no es una falla todavía: se insiste hasta la
                # tolerancia. Una pérdida real no depende de esto —el callback de
                # DeviceLost levanta `_force_disconnect` y el `while` corta.
                if time.monotonic() >= deadline_s:
                    raise
        return None

    def _find_device_id_by_ip(self, st_interface, wanted_ip: str) -> str | None:
        """
        Traduce una IP al device_id con el que StApi sí sabe abrir la cámara.

        StApi no puede abrir por IP: `create_device_by_id` espera el device_id, que en
        GigE es la MAC. El nodemap de la interface publica la IP de cada cámara sin
        abrirla, así que la traducción sale de ahí.
        """
        try:
            nodemap = st_interface.port.nodemap
            selector = st.PyIInteger(nodemap.get_node(_DEVICE_SELECTOR_NODE))
            ip_node = st.PyIInteger(nodemap.get_node(_DEVICE_IP_NODE))
        except Exception as e:
            logger.debug(f"[StDriver] La interface no publica IPs de cámara: {e}")
            return None

        for index in range(st_interface.device_count):
            try:
                selector.set_value(index)
                if _format_ip(ip_node.value) == wanted_ip:
                    return st_interface.get_device_info(index).device_id
            except Exception:
                continue
        return None

    def _create_configured_device(self, st_system):
        """
        Abre la cámara indicada por `address` recorriendo las interfaces.

        `address` puede traer la IP o el device_id: la IP se traduce a device_id antes
        de pedir la cámara. Devuelve None si `address` no coincide con ninguna: dar la
        primera disponible entregaría frames de otra cámara como si fueran los de la
        pedida. Solo cuando `address` viene vacío se toma la primera.
        """
        # La lista de cámaras se arma cuando se crea el system y no se actualiza sola: una
        # que se desenchufa y vuelve sigue figurando con su IP —la traducción a device_id
        # funciona— pero el handle que quedó cacheado ya no abre, y `create_device_by_id`
        # falla sin decir por qué. Re-enumerar es lo único que la vuelve a hacer visible.
        _refresh_device_list(st_system)

        wanted = (self._address or "").strip()
        if not wanted or wanted.lower() == "unknown":
            logger.warning("[StDriver] Sin `address` en config: se toma la primera cámara.")
            return st_system.create_first_device()

        is_ip = _looks_like_ip(wanted)
        # Se distingue «no está» de «está y no abre», porque se arreglan en lugares
        # opuestos: lo primero es cableado, IP o subred; lo segundo es casi siempre acceso
        # excluyente —otro proceso la tiene tomada, o quedó una conexión sin cerrar cuyo
        # heartbeat todavía no venció—. Decir «no hay ninguna cámara» cuando la enumeración
        # la encontró manda a revisar el cable durante media hora.
        open_error = None
        for i in range(st_system.interface_count):
            interface = st_system.get_interface(i)

            device_id = wanted
            if is_ip:
                device_id = self._find_device_id_by_ip(interface, wanted)
                if not device_id:
                    continue
                logger.info(f"[StDriver] {wanted} resuelta al device_id {device_id}")

            try:
                return interface.create_device_by_id(device_id)
            except Exception as e:
                open_error = e
                continue

        if open_error is not None:
            logger.error(
                f"[StDriver] {wanted} está en la red pero no se pudo abrir: {open_error}. "
                f"Suele ser que la tenga tomada otro programa, o que una conexión anterior "
                f"no se haya cerrado y la cámara siga reservada hasta que expire su "
                f"heartbeat.")
        else:
            logger.error(f"[StDriver] No hay ninguna cámara en '{wanted}'.")
        return None

    def _grab_loop(self):
        """Thread de fondo: inicializa StApi, conecta y reconecta ante pérdida."""
        try:
            # Bajo el lock porque esto es global al proceso: con dos cámaras Sentech
            # los dos hilos llegan acá a la vez y uno de los dos se lleva
            # `-1004 = GCInitLib()`, al azar. Secuenciales no molestan —`initialize()`
            # es idempotente— así que serializarlos alcanza y no cuesta nada: pasa una
            # vez por cámara, en el arranque.
            with _stapi_lock:
                st.initialize()
                st_system = st.create_system()
        except Exception as e:
            logger.error(f"[StDriver] Falló la inicialización de StApi: {e}")
            return

        device_id = ""
        while self._grab_active:
            st_device = None
            try:
                if not device_id:
                    st_device = self._create_configured_device(st_system)
                    if st_device is not None:
                        device_id = st_device.info.device_id
                else:
                    for i in range(st_system.interface_count):
                        try:
                            interface = st_system.get_interface(i)
                            st_device = interface.create_device_by_id(device_id)
                            break
                        except Exception:
                            continue

                if st_device:
                    self._do_grabbing(st_device)
                else:
                    # Se olvida el id cacheado para volver a enumerar por dirección. Una
                    # cámara que se desenchufa y vuelve puede no responder más a ese id, y
                    # reintentarlo con el mismo no sale nunca: la cámara está de vuelta en
                    # la red y el driver sigue preguntando por algo que ya no existe.
                    device_id = ""
                    logger.warning(
                        f"[StDriver] {self._address} no encontrada. "
                        f"Reintento en {_RETRY_DELAY_S} s."
                    )
                    time.sleep(_RETRY_DELAY_S)

            except Exception as e:
                logger.warning(
                    f"[StDriver] {self._address} error de captura: {e}. "
                    f"Reintento en {_RETRY_DELAY_S} s."
                )
                self.is_connected = False
                time.sleep(_RETRY_DELAY_S)

    # ── Decodificación ───────────────────────────────────────────────────────

    def _decode_buffer(self, st_buffer) -> np.ndarray | None:
        """Convierte un buffer de StApi en una imagen BGR de numpy."""
        st_image = st_buffer.get_image()
        format_info = st.get_pixel_format_info(st_image.pixel_format)

        if not (format_info.is_mono or format_info.is_bayer):
            return None

        image_data = st_image.get_image_data()
        if format_info.each_component_total_bit_count > 8:
            # `astype` aloca → memoria propia.
            img = np.frombuffer(image_data, np.uint16)
            divisor = pow(2, format_info.each_component_valid_bit_count - 8)
            img = (img / divisor).astype(np.uint8)
            is_buffer_view = False
        else:
            # Vista cruda sobre el buffer de StApi. Todavía no es propia.
            img = np.frombuffer(image_data, np.uint8)
            is_buffer_view = True

        img = img.reshape(st_image.height, st_image.width, 1)

        if format_info.is_bayer:
            # No puede ser constante de módulo: las claves necesitan stapipy importado.
            bayer_map = {
                st.EStPixelColorFilter.BayerRG: cv2.COLOR_BAYER_BG2BGR,
                st.EStPixelColorFilter.BayerGR: cv2.COLOR_BAYER_GB2BGR,
                st.EStPixelColorFilter.BayerGB: cv2.COLOR_BAYER_GR2BGR,
                st.EStPixelColorFilter.BayerBG: cv2.COLOR_BAYER_RG2BGR,
            }
            bayer_code = bayer_map.get(format_info.get_pixel_color_filter())
            if bayer_code is not None:
                img = cv2.cvtColor(img, bayer_code)
                is_buffer_view = False

        # StApi recicla el buffer al salir del `with retrieve_buffer(...)`: devolver
        # una vista sobre él entregaría memoria liberada al resto de la app.
        if is_buffer_view:
            img = img.copy()

        return img

    def _on_device_lost(self, node=None, st_device=None):
        if node and node.is_available and st_device and st_device.is_device_lost:
            logger.warning(f"[StDriver] Evento DeviceLost para {self._address}")
            self._force_disconnect = True
