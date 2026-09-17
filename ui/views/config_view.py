"""
Panel de configuración: el cascarón que junta las pestañas y persiste una sola vez.

No conoce ninguna clave del config. Cada pestaña de `ui/views/config/` es dueña de su
sección y sabe cargarla y guardarla; esta vista sólo las arma, las recorre y llama al
`save()` del ConfigManager cuando todas escribieron. Agregar una sección al config es
un archivo nuevo y una línea en `_TAB_CLASSES`.

Guardar es atómico para el usuario: si una pestaña levanta una excepción se avisa y no
se persiste nada, así el config.yaml no queda con la mitad de los cambios.
"""

from functools import partial

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.logger import logger

from ui.strings import tr
from ui.views.config.cameras_tab import CamerasTab
from ui.views.config.collector_tab import CollectorTab
from ui.views.config.inference_tab import InferenceTab
from ui.views.config.lens_health_tab import LensHealthTab
from ui.views.config.modbus_tab import ModbusTab
from ui.views.config.process_tab import ProcessTab
from ui.views.config.system_tab import SystemTab
from ui.views.config.telemetry_tab import TelemetryTab
from ui.views.config.video_tab import VideoTab
from ui.widgets.form import wrap_in_card

# Pestañas en el orden en que se muestran. Es la única lista que se toca al agregar
# una sección al config.
_TAB_CLASSES = (
    CamerasTab,
    LensHealthTab,
    VideoTab,
    InferenceTab,
    ProcessTab,      # los parámetros de lo que mide esta instalación
    CollectorTab,
    TelemetryTab,
    ModbusTab,
    SystemTab,
)


class ConfigView(QWidget):
    """Panel de configuración. Emite `config_saved` recién cuando el archivo se escribió."""

    config_saved = Signal()               # el config.yaml se persistió sin errores
    # (slot de cámara, clave del config donde va el rectángulo, clave de idioma del título).
    # Lo manda la pestaña que lo pide: esta vista lo reenvía sin leerlo.
    open_roi_requested = Signal(str, str, str)
    calibrate_lens_requested = Signal(str)  # slot de la cámara cuya óptica hay que calibrar

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._tabs: list = []
        # Pestañas que cargaron bien. Una que no cargó tiene los campos en su valor por
        # defecto, así que guardarla escribiría esos defaults encima de la config real.
        self._loaded_tabs: set = set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel(tr("config_title"))
        title.setObjectName("viewTitle")
        layout.addWidget(title)

        tab_widget = QTabWidget()
        for tab_class in _TAB_CLASSES:
            tab = tab_class(self._config)
            self._tabs.append(tab)
            # Todas con scroll: una pestaña más alta que la pantalla estiraría el
            # stack y le cortaría los botones de guardar.
            tab_widget.addTab(wrap_in_card(tab, scroll=True), tr(tab.TITLE_KEY))
        layout.addWidget(tab_widget, stretch=1)

        # Cualquier pestaña que sepa dibujar un rectángulo, sin nombrar ninguna: una
        # pestaña nueva con otro rectángulo se engancha sola.
        self._roi_requester = None
        for tab in self._tabs:
            signal = getattr(tab, "open_roi_requested", None)
            if signal is not None:
                signal.connect(partial(self._on_roi_requested, tab))
        lens_tab = self._get_lens_health_tab()
        if lens_tab is not None:
            lens_tab.calibrate_requested.connect(self.calibrate_lens_requested)

        layout.addLayout(self._build_button_row())
        # Cargar acá y no dejarlo librado a cada pestaña: una que se olvide de hacerlo en
        # su constructor arranca con los campos en su default y el primer guardado los
        # persiste. El invariante lo garantiza quien las crea, no cada una por su cuenta.
        self.reload()

    # ── API pública ──────────────────────────────────────────────────────────

    def reload(self):
        """
        Recarga todas las pestañas desde el config, descartando lo que se haya editado.

        Es lo que hace el botón de descartar, y también lo que hay que llamar cuando el
        `config.yaml` cambió por afuera de la aplicación.

        Una pestaña que falla al cargar no corta a las demás y queda marcada: no se la
        guarda, porque sus campos tienen el valor por defecto y persistirlos borraría la
        configuración real de esa sección sin que nada parezca haber fallado.
        """
        for tab in self._tabs:
            try:
                tab.load()
            except Exception as error:
                self._loaded_tabs.discard(tab)
                logger.error(
                    f"[UI] La pestaña '{tr(tab.TITLE_KEY)}' no pudo cargar su "
                    f"configuración: {error}. No se va a guardar.")
            else:
                self._loaded_tabs.add(tab)

    def refresh_roi_fields(self, camera_slot: str):
        """
        Recarga el rectángulo tras cerrarse el diálogo, en la pestaña que lo pidió.

        Sólo esa: recargarlas todas descartaría lo que el operador esté editando en otra
        —abrir el dibujo de un ROI no es motivo para perder una exposición a medio cambiar—.
        """
        if self._roi_requester is not None:
            self._roi_requester.reload_roi(camera_slot)

    def _build_button_row(self) -> QHBoxLayout:
        discard_button = QPushButton(tr("config_discard"))
        discard_button.setObjectName("discardButton")
        discard_button.clicked.connect(self._on_discard_clicked)

        save_button = QPushButton(tr("config_save"))
        save_button.setObjectName("saveButton")
        save_button.clicked.connect(self._on_save_clicked)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(discard_button)
        row.addWidget(save_button)
        return row

    def refresh_lens_reference(self, camera_slot: str):
        """Recarga la referencia de una cámara cuando la calibración terminó de escribirla."""
        lens_tab = self._get_lens_health_tab()
        if lens_tab is not None:
            lens_tab.refresh_reference(camera_slot)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _get_lens_health_tab(self) -> LensHealthTab | None:
        for tab in self._tabs:
            if isinstance(tab, LensHealthTab):
                return tab
        return None

    def _on_roi_requested(self, tab, camera_slot: str, config_prefix: str,
                          title_key: str):
        """Recuerda quién pidió el rectángulo y reenvía el pedido tal cual."""
        self._roi_requester = tab
        self.open_roi_requested.emit(camera_slot, config_prefix, title_key)

    def _on_discard_clicked(self):
        self.reload()

    def _on_save_clicked(self):
        try:
            for tab in self._tabs:
                if tab not in self._loaded_tabs:
                    logger.error(
                        f"[UI] No se guarda la pestaña '{tr(tab.TITLE_KEY)}': no había "
                        f"cargado su configuración y escribiría valores por defecto.")
                    continue
                tab.save()
        except Exception as error:
            logger.error(f"[UI] No se pudo armar la configuración a guardar: {error}")
            QMessageBox.warning(self, tr("config_saved_title"), tr("config_save_error"))
            return

        if not self._config.save():
            QMessageBox.warning(self, tr("config_saved_title"), tr("config_save_error"))
            return

        logger.info("[UI] Configuración guardada.")
        self.config_saved.emit()
        QMessageBox.information(self, tr("config_saved_title"), tr("config_saved_body"))
