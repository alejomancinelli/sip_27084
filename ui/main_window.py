"""
Ventana principal: header con navegación, las tres vistas y la barra de estado.

Es la puerta de la UI para el cableado: `main.py` construye esta ventana y le habla
sólo a ella —`set_service_status()`, `log_event()`, los slots de actualización—, sin
saber qué vista dibuja cada cosa. Así agregar una vista no cambia el cableado.

Dos cosas que no son obvias:

  - **ConfigView se arma la primera vez que se navega a ella.** Son ocho pestañas con
    cientos de campos y el arranque de la aplicación no las necesita; hasta entonces su
    lugar en el stack lo ocupa un cartel.
  - **El botón de GPIO aparece sólo si hay controlador.** El template no trae el módulo
    de GPIO, así que `set_gpio()` es lo que lo habilita: sin eso el botón no está, en
    vez de estar y no hacer nada.

La ventana no toca hardware ni protocolos: reparte lo que recibe y persiste config a
través del ConfigView.
"""

import os

from PySide6.QtCore import QTime, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout,
    QWidget,
)

from system.config_manager import ConfigManager
from system.logger import logger
from system.version import APP_VERSION

from ui import strings, theme
from ui.strings import tr
from ui.views.config_view import ConfigView
from ui.views.diagnostics_view import DiagnosticsView
from ui.views.monitor_view import MonitorView

# Índices de las vistas en el QStackedWidget.
VIEW_MONITOR = 0
VIEW_CONFIG = 1
VIEW_DIAGNOSTICS = 2

_HEADER_HEIGHT_PX = 56
_FOOTER_HEIGHT_PX = 28
_LOGO_HEIGHT_PX = 44
_LOGO_TITLE_SPACING_PX = 28   # aire entre el logo y el nombre del proyecto
_NAV_BUTTON_WIDTH_PX = 140
_HEADER_MARGIN_PX = 12        # aire del logo contra el borde izquierdo
_NAV_SPACING_PX = 6           # aire entre los botones de navegación
_GROUP_SPACING_PX = 16        # aire de cada hueco del lado derecho del header
_SEPARATOR_WIDTH_PX = 1
_SEPARATOR_INSET_PX = 14      # cuánto se acorta el divisor arriba y abajo del header
_WINDOW_WIDTH_PX = 1920
_WINDOW_HEIGHT_PX = 1080
_CLOCK_INTERVAL_MS = 1000

_ICONS_DIRNAME = "icons"
_WINDOW_ICON_FILENAME = "iea_100x100.ico"
_LOGO_FILENAME = "logo-iea-texto-debajo-blanco.png"

# Flash del footer: se pinta del color de aviso y se apaga hasta el fondo del tema, para
# que un mensaje nuevo se note sin tener que leerlo. Es el único estilo animado de la UI
# y por eso el único que se aplica inline: el QSS no interpola colores.
_FLASH_STEPS = 55
_FLASH_INTERVAL_MS = 50

# Propiedad Qt que marca el botón de navegación de la vista activa; la pinta el QSS.
_ACTIVE_PROPERTY = "active"


class MainWindow(QMainWindow):
    """Ventana principal. Ver el contrato en el docstring del módulo."""

    config_saved = Signal()      # se persistió el config.yaml; reenvía el del ConfigView

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._gpio_controller = None
        self._gpio_thread = None
        self._gpio_dialog = None
        self._config_view: ConfigView | None = None
        self._nav_buttons: list = []
        self._is_dark = True
        self._flash_step = 0

        self.setWindowTitle(str(self._config.get("system.app_name", "")))
        self._apply_window_icon()
        self.resize(_WINDOW_WIDTH_PX, _WINDOW_HEIGHT_PX)

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._build_header())

        self._stack = QStackedWidget()
        root_layout.addWidget(self._stack, stretch=1)

        self.monitor_view = MonitorView(self._config)
        self._stack.addWidget(self.monitor_view)                 # VIEW_MONITOR

        self._config_placeholder = QLabel(tr("config_loading"))
        self._config_placeholder.setAlignment(Qt.AlignCenter)
        self._stack.addWidget(self._config_placeholder)          # VIEW_CONFIG

        self.diagnostics_view = DiagnosticsView(self._config)
        self._stack.addWidget(self.diagnostics_view)             # VIEW_DIAGNOSTICS

        root_layout.addWidget(self._build_footer())
        self.apply_theme()
        self.set_status_message(tr("status_started"))

    # ── API pública ──────────────────────────────────────────────────────────

    def set_gpio(self, gpio_controller: object, gpio_thread: object = None):
        """
        Inyecta el controlador de GPIO y, si lo hay, el hilo que informa las entradas.

        Sin esto el botón de GPIO no aparece: es la mitad de UI de un subsistema que el
        template todavía no tiene. Ver `ui/dialogs/gpio_dialog.py`.
        """
        self._gpio_controller = gpio_controller
        self._gpio_thread = gpio_thread
        self._gpio_group.setVisible(gpio_controller is not None)

    def reload_config_view(self):
        """
        Recarga el panel de configuración desde el config, si ya está construido.

        Para cuando el `config.yaml` cambió por afuera de la aplicación: sin esto el
        panel sigue mostrando lo que leyó al abrirse y lo escribiría de vuelta.
        """
        if self._config_view is not None:
            self._config_view.reload()

    def set_service_status(self, service: str, status: str):
        """Estado de un servicio, a las dos vistas que lo muestran."""
        self.monitor_view.set_service_status(service, status)
        self.diagnostics_view.set_service_status(service, status)

    def set_client_count(self, service: str, count: int):
        self.diagnostics_view.set_client_count(service, count)

    def set_stream_urls(self, service: str, urls: list):
        """URLs de los streams de un servidor de video, ya armadas por quien las publica."""
        self.diagnostics_view.set_stream_urls(service, urls)

    def log_event(self, level: str, message: str, module: str = ""):
        """Una línea de log a la barra lateral del monitor y a la pestaña de logs."""
        self.monitor_view.log_event(level, message)
        self.diagnostics_view.log_event(level, message, module)

    def set_status_message(self, message: str = ""):
        """Cambia el texto del footer y lo hace destellar para que el cambio se note."""
        self._footer.setText(message or tr("status_ready"))
        self._flash_step = 0
        self._flash_timer.start(_FLASH_INTERVAL_MS)

    def apply_theme(self, dark: bool | None = None):
        """
        Aplica el tema a la ventana y lo baja a los widgets que se pintan por código.

        Con `dark=None` lo lee del config, que es lo que hace el arranque y lo que hace
        el guardado de la configuración.
        """
        if dark is None:
            dark = bool(self._config.get("ui.dark_mode", True))
        self._is_dark = dark
        self.setStyleSheet(theme.load_stylesheet(dark))
        self.monitor_view.apply_theme(dark)
        self.diagnostics_view.apply_theme(dark)

    # ── Header ───────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("appHeader")
        header.setFixedHeight(_HEADER_HEIGHT_PX)
        layout = QHBoxLayout(header)
        # El margen derecho es el mismo `_GROUP_SPACING_PX` que separa al reloj del grupo
        # de GPIO: así el reloj queda con el mismo aire a los dos lados. Todos los huecos
        # de la mitad derecha del header miden lo mismo.
        layout.setContentsMargins(_HEADER_MARGIN_PX, 0, _GROUP_SPACING_PX, 0)
        # Explícito y no el default del estilo: los márgenes del grupo de GPIO lo
        # descuentan para que sus huecos den justo.
        layout.setSpacing(_NAV_SPACING_PX)

        layout.addWidget(self._build_logo())
        layout.addSpacing(_LOGO_TITLE_SPACING_PX)

        title = QLabel(str(self._config.get("system.app_name", "")))
        title.setObjectName("appTitle")
        layout.addWidget(title)
        layout.addStretch()

        for index, text_key in ((VIEW_MONITOR, "nav_monitor"),
                                (VIEW_CONFIG, "nav_config"),
                                (VIEW_DIAGNOSTICS, "nav_diagnostics")):
            button = QPushButton(tr(text_key))
            button.setObjectName("navButton")
            button.setFixedWidth(_NAV_BUTTON_WIDTH_PX)
            button.clicked.connect(lambda _, target=index: self._navigate(target))
            self._nav_buttons.append(button)
            layout.addWidget(button)
        self._refresh_nav_buttons(VIEW_MONITOR)

        layout.addWidget(self._build_gpio_group())
        layout.addWidget(self._build_clock())
        return header

    def _build_gpio_group(self) -> QWidget:
        """
        Divisor y botón de GPIO, juntos en un grupo que se muestra o se esconde entero.

        Van en un contenedor y no sueltos en el header por dos razones: el divisor sólo
        tiene sentido si hay algo del otro lado —sin controlador de GPIO se esconden los
        dos y no queda una línea huérfana—, y el aire alrededor se declara una vez en los
        márgenes del grupo en vez de repartirlo en `addSpacing()` sueltos.
        """
        self._gpio_group = QWidget()
        self._gpio_group.setObjectName("headerGroup")
        row = QHBoxLayout(self._gpio_group)
        # Sólo el margen izquierdo, y descontando el spacing del header, que ya separa al
        # grupo del último botón de navegación. El hueco de la derecha lo pone el reloj:
        # si lo pusiera este grupo, con el GPIO escondido el reloj quedaría pegado a la
        # navegación.
        row.setContentsMargins(_GROUP_SPACING_PX - _NAV_SPACING_PX, 0, 0, 0)
        row.setSpacing(_GROUP_SPACING_PX)

        # Un QWidget con fondo, no un `QFrame(VLine)`: la línea de un VLine se pinta con
        # los colores de la paleta y no con los del QSS, así que sobre el degradado del
        # header salía como una banda gris de dos o tres píxeles en vez de un filete.
        separator = QWidget()
        separator.setObjectName("headerSeparator")
        separator.setFixedSize(_SEPARATOR_WIDTH_PX,
                               _HEADER_HEIGHT_PX - 2 * _SEPARATOR_INSET_PX)
        row.addWidget(separator, alignment=Qt.AlignVCenter)

        self._gpio_button = QPushButton(tr("nav_gpio"))
        self._gpio_button.setObjectName("headerButton")
        self._gpio_button.setToolTip(tr("gpio_tooltip"))
        self._gpio_button.clicked.connect(self._on_gpio_clicked)
        row.addWidget(self._gpio_button)

        self._gpio_group.setVisible(False)
        return self._gpio_group

    def _build_logo(self) -> QLabel:
        label = QLabel()
        label.setObjectName("appLogo")
        pixmap = QPixmap(self._get_icon_path(_LOGO_FILENAME))
        if not pixmap.isNull():
            label.setPixmap(pixmap.scaledToHeight(_LOGO_HEIGHT_PX, Qt.SmoothTransformation))
        return label

    def _build_clock(self) -> QWidget:
        """
        Reloj del header, con su aire a la izquierda dentro de su propio grupo.

        El hueco es del reloj y no de lo que tenga al lado: así mide lo mismo con el
        grupo de GPIO presente y sin él, que es el caso de cualquier instalación que
        todavía no tenga el módulo de GPIO.
        """
        self._clock_label = QLabel()
        self._clock_label.setObjectName("headerClock")
        timer = QTimer(self)
        timer.timeout.connect(self._on_clock_tick)
        timer.start(_CLOCK_INTERVAL_MS)
        self._on_clock_tick()

        group = QWidget()
        group.setObjectName("headerGroup")
        row = QHBoxLayout(group)
        row.setContentsMargins(_GROUP_SPACING_PX - _NAV_SPACING_PX, 0, 0, 0)
        row.addWidget(self._clock_label)
        return group

    def _build_footer(self) -> QWidget:
        """
        Barra de estado: el mensaje a la izquierda y la versión pegada a la derecha.

        La versión no cambia nunca en la corrida, así que se arma una vez. El mensaje sí,
        y es el que hace destellar la barra.
        """
        self._footer_bar = QWidget()
        self._footer_bar.setObjectName("appFooter")
        self._footer_bar.setFixedHeight(_FOOTER_HEIGHT_PX)
        row = QHBoxLayout(self._footer_bar)
        # Alineados con el header: el mensaje bajo el logo, la versión bajo el reloj.
        row.setContentsMargins(_HEADER_MARGIN_PX, 0, _GROUP_SPACING_PX, 0)

        self._footer = QLabel()
        self._footer.setObjectName("footerMessage")
        row.addWidget(self._footer)
        row.addStretch()

        version_label = QLabel(self._build_version_text())
        version_label.setObjectName("footerVersion")
        row.addWidget(version_label)

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._on_flash_tick)
        return self._footer_bar

    def _build_version_text(self) -> str:
        """
        Id del proyecto y versión del programa.

        Sin `project.project_id` declarado muestra sólo la versión: un separador colgando
        de la nada se lee como un error de la aplicación.
        """
        project_id = str(self._config.get("project.project_id", "") or "").strip()
        if not project_id:
            return tr("footer_version_only").format(version=APP_VERSION)
        return tr("footer_version").format(project_id=project_id, version=APP_VERSION)

    def _on_flash_tick(self):
        """
        Un paso de la interpolación del color de aviso al fondo del tema.

        El selector es explícito y no un `background-color` suelto: una hoja sin selector
        se propaga a los hijos, y el mensaje y la versión quedarían con su propio recuadro
        de color en vez de sobre la barra.
        """
        if self._flash_step >= _FLASH_STEPS:
            self._flash_timer.stop()
            self._footer_bar.setStyleSheet("")
            return
        palette = theme.get_palette(self._is_dark)
        progress = self._flash_step / (_FLASH_STEPS - 1)
        color = _blend(QColor(palette["footer_flash"]),
                       QColor(palette["footer_background"]), progress)
        self._footer_bar.setStyleSheet(
            f"QWidget#appFooter {{ background-color: {color.name()}; }}"
        )
        self._flash_step += 1

    # ── Navegación ───────────────────────────────────────────────────────────

    def _navigate(self, index: int):
        if index == VIEW_CONFIG:
            self._ensure_config_view()
        self._stack.setCurrentIndex(index)
        self._refresh_nav_buttons(index)

    def _refresh_nav_buttons(self, active_index: int):
        for index, button in enumerate(self._nav_buttons):
            button.setProperty(_ACTIVE_PROPERTY, index == active_index)
            button.style().unpolish(button)
            button.style().polish(button)

    def _ensure_config_view(self):
        """Arma el ConfigView la primera vez que se navega a él."""
        if self._config_view is not None:
            return
        self._config_view = ConfigView(self._config)
        self._config_view.config_saved.connect(self._on_config_saved)
        self._config_view.open_roi_requested.connect(self._on_open_roi_requested)

        placeholder = self._stack.widget(VIEW_CONFIG)
        self._stack.removeWidget(placeholder)
        placeholder.deleteLater()
        self._stack.insertWidget(VIEW_CONFIG, self._config_view)

    # ── Slots ────────────────────────────────────────────────────────────────

    def _on_clock_tick(self):
        self._clock_label.setText(QTime.currentTime().toString("HH:mm:ss"))

    def _on_config_saved(self):
        """
        El idioma y el tema son lo único que se aplica sin reiniciar.

        El resto —cámaras, servidores, modelos— se lee al arrancar cada subsistema, y
        el propio panel avisa que hay que reiniciar. Reenvía la señal para que el
        cableado haga lo suyo.
        """
        strings.set_language(str(self._config.get("ui.language", "es")))
        self.apply_theme()
        self.set_status_message(tr("status_config_saved"))
        self.config_saved.emit()

    def _on_open_roi_requested(self, camera_slot: str):
        """
        Abre el diálogo de ROI alimentado con el frame en vivo de esa cámara.

        El frame sale del panel que la vista de monitor tenga para ese slot: si el fork
        no puso ninguno, el diálogo se abre igual y se dibuja sobre negro, que es mejor
        que no abrirse.
        """
        from ui.dialogs.roi_dialog import RoiDialog

        dialog = RoiDialog(self._config, camera_slot, self)
        panel = self._get_camera_panel(camera_slot)
        if panel is not None:
            panel.frame_updated.connect(dialog.update_frame)
            dialog.update_frame(panel.get_last_frame())
        dialog.exec()
        if panel is not None:
            panel.frame_updated.disconnect(dialog.update_frame)
        if self._config_view is not None:
            self._config_view.refresh_roi_fields(camera_slot)

    def _on_gpio_clicked(self):
        if self._gpio_controller is None:
            logger.warning("[UI] No hay controlador de GPIO cableado.")
            return
        if self._gpio_dialog is None or not self._gpio_dialog.isVisible():
            from ui.dialogs.gpio_dialog import GpioDialog

            self._gpio_dialog = GpioDialog(self._gpio_controller, self)
            if self._gpio_thread is not None:
                self._gpio_thread.inputs_updated.connect(self._gpio_dialog.update_inputs)
        self._gpio_dialog.show()
        self._gpio_dialog.raise_()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _get_camera_panel(self, camera_slot: str):
        """
        Panel de esa cámara en el contenido del monitor, o None.

        Lo busca por tipo y no por una referencia guardada porque el área central la
        arma el fork: la ventana no sabe qué widget puso ni cómo lo organizó.
        """
        from ui.widgets.camera_panel import CameraPanel

        for panel in self.monitor_view.findChildren(CameraPanel):
            if panel.camera_slot == camera_slot:
                return panel
        return None

    def _apply_window_icon(self):
        icon_path = self._get_icon_path(_WINDOW_ICON_FILENAME)
        if os.path.isfile(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _get_icon_path(self, filename: str) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            _ICONS_DIRNAME, filename)


def _blend(start: QColor, end: QColor, progress: float) -> QColor:
    """Color intermedio entre dos, con `progress` de 0.0 (start) a 1.0 (end)."""
    return QColor(
        int(start.red() * (1 - progress) + end.red() * progress),
        int(start.green() * (1 - progress) + end.green() * progress),
        int(start.blue() * (1 - progress) + end.blue() * progress),
    )
