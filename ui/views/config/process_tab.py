"""
Pestaña de proceso: la sección `process:` del config.

Es la única pestaña cuya *forma* cambia entre proyectos, porque `process:` es la única
sección cuya forma cambia: lo que se mide en esta cinta no tiene nada en común con lo que
se mide en la siguiente instalación. Acá están los tres parámetros de este equipo:

  - **El rectángulo de cinta**, por cámara. Es la referencia del 100 % de carga y lo que se
    dibuja sobre el anotado. Se puede escribir a mano o dibujar sobre el video en vivo, que
    es como se hace en planta.
  - **El umbral de fondo oscuro**, por cámara y por clase. Sólo aparecen las clases que el
    modelo declara en `class_names`: son datos de configuración y no identificadores, así
    que la pestaña las lee en vez de nombrarlas.
  - **La ventana de las medias**, que es sobre cuánto tiempo se promedian las tendencias
    que lee el PLC.

Dos cosas que **no** están acá, y por qué:

  - **El `enabled` del rectángulo.** No existe: el rectángulo de cinta siempre se usa, y sin
    declarar la carga se mide contra el frame entero. Una casilla más sería un estado más
    que puede quedar mal.
  - **La validación de lo obligatorio.** Un valor cuya ausencia corrompe una medición tiene
    que frenar el arranque, y eso lo chequea `main.py`, que es quien lee la sección y la
    inyecta. Acá los tres tienen un default que es un no-op documentado.

El rectángulo de cinta se dibuja con el mismo diálogo que el ROI de cámara, apuntado a otra
clave: son el mismo gesto sobre el mismo frame en vivo y lo único que cambia es dónde se
guarda. Ver `ui/dialogs/roi_dialog.py`.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import add_form_row, add_hint_row, build_group_box, build_spin_box

_MAX_FRAME_SIDE_PX = 8192
_MAX_LUMA = 255              # el umbral se compara contra un nivel de gris de 8 bits
_MAX_WINDOW_S = 86400        # un día: más que eso deja de ser una tendencia operativa
_DEFAULT_WINDOW_S = 3600

# Clave del rectángulo de cinta, en el orden en que se muestra, con su etiqueta. Las
# etiquetas son las mismas del ROI de cámara: es la misma geometría y el operador ya las
# conoce.
_BELT_ROI_FIELDS = (
    ("x_px", "cam_roi_x"),
    ("y_px", "cam_roi_y"),
    ("width_px", "cam_roi_width"),
    ("height_px", "cam_roi_height"),
)

_BELT_ROI_PREFIX = "process.belt_roi_px"
_DARK_THRESHOLD_PREFIX = "process.dark_background_threshold"
_WINDOW_KEY = "process.rolling_window_s"


class ProcessTab(AbstractConfigTab):
    """Sección `process:` del config, con una sub-pestaña por cámara. Ver `abstract_tab.py`."""

    TITLE_KEY = "tab_process"

    open_belt_roi_requested = Signal(str)   # slot de la cámara cuyo rectángulo hay que dibujar

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._forms: dict[str, _CameraProcessForm] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        camera_slots = tuple(self._config.get("cameras", {}) or {})
        if not camera_slots:
            note = QLabel(tr("process_no_cameras"))
            note.setObjectName("hintLabel")
            note.setWordWrap(True)
            layout.addWidget(note)
        else:
            tabs = QTabWidget()
            for camera_slot in camera_slots:
                form = _CameraProcessForm(self._config, camera_slot)
                form.open_belt_roi_requested.connect(self.open_belt_roi_requested)
                self._forms[camera_slot] = form
                tabs.addTab(form, form.camera_name)
            layout.addWidget(tabs)

        layout.addWidget(self._build_window_box())
        layout.addStretch()

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        for form in self._forms.values():
            form.load()
        self._window_spin.setValue(
            int(self._config.get(_WINDOW_KEY, _DEFAULT_WINDOW_S) or _DEFAULT_WINDOW_S))

    def save(self):
        for form in self._forms.values():
            form.save()
        self._config.set(_WINDOW_KEY, self._window_spin.value())

    # ── API pública ──────────────────────────────────────────────────────────

    def refresh_belt_roi_fields(self, camera_slot: str):
        """Recarga el rectángulo de una cámara tras cerrarse el diálogo interactivo."""
        form = self._forms.get(camera_slot)
        if form is not None:
            form.load_belt_roi()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_window_box(self) -> QWidget:
        box, form = build_group_box(tr("process_box_window"))
        self._window_spin = add_form_row(
            form, tr("process_window_s"),
            build_spin_box(60, _MAX_WINDOW_S, _DEFAULT_WINDOW_S),
        )
        add_hint_row(form, tr("process_window_note"))
        return box


class _CameraProcessForm(QWidget):
    """Parámetros de proceso de una cámara: el rectángulo de cinta y los umbrales."""

    open_belt_roi_requested = Signal(str)

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_belt_roi_box())
        layout.addWidget(self._build_dark_background_box())
        layout.addStretch()

        self.load()

    @property
    def camera_name(self) -> str:
        """Texto de la sub-pestaña: el `name` del config, o el slot si está vacío."""
        return str(self._config.get(f"cameras.{self._camera_slot}.name", "")
                   or self._camera_slot)

    # ── Contrato ─────────────────────────────────────────────────────────────

    def load(self):
        self.load_belt_roi()
        thresholds = self._get_thresholds()
        for class_name, spin in self._threshold_spins.items():
            spin.setValue(int(thresholds.get(class_name, 0) or 0))

    def load_belt_roi(self):
        """
        Sólo la geometría, que es lo que escribe el diálogo interactivo.

        Va aparte de `load()` porque son dos llamadores con intenciones distintas: uno
        recarga la pestaña entera desde el archivo y el otro vuelve del diálogo, donde lo
        único que cambió fue el rectángulo.
        """
        roi_px = self._get_belt_roi()
        for key, spin in self._roi_spins.items():
            spin.setValue(int(roi_px.get(key, 0) or 0))

    def save(self):
        for key, spin in self._roi_spins.items():
            self._config.set(f"{_BELT_ROI_PREFIX}.{self._camera_slot}.{key}", spin.value())
        for class_name, spin in self._threshold_spins.items():
            self._config.set(
                f"{_DARK_THRESHOLD_PREFIX}.{self._camera_slot}.{class_name}", spin.value())

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_belt_roi_box(self) -> QWidget:
        box, form = build_group_box(tr("process_box_belt"))
        self._roi_spins = {}
        for key, label_key in _BELT_ROI_FIELDS:
            self._roi_spins[key] = add_form_row(
                form, tr(label_key), build_spin_box(0, _MAX_FRAME_SIDE_PX, 0)
            )
        draw_button = QPushButton(tr("process_belt_tool"))
        draw_button.clicked.connect(
            lambda: self.open_belt_roi_requested.emit(self._camera_slot)
        )
        form.addRow("", draw_button)
        add_hint_row(form, tr("process_belt_note"))
        return box

    def _build_dark_background_box(self) -> QWidget:
        """
        Un umbral por clase del modelo. Sin clases declaradas, el grupo queda con la nota.

        Las clases se leen del modelo y no se escriben acá: son datos de configuración, y
        una lista propia en la interfaz sería una segunda declaración que puede discrepar
        con la que de verdad usa el analyzer.
        """
        box, form = build_group_box(tr("process_box_dark"))
        self._threshold_spins = {}
        for class_name in self._get_class_names():
            spin = build_spin_box(0, _MAX_LUMA, 0)
            spin.setSpecialValueText(tr("process_dark_off"))
            self._threshold_spins[class_name] = add_form_row(
                form, f"{class_name} — {tr('process_dark_threshold')}", spin
            )
        add_hint_row(form, tr("process_dark_note"))
        return box

    # ── Lectura del config ───────────────────────────────────────────────────

    def _get_belt_roi(self) -> dict:
        return (self._config.get(_BELT_ROI_PREFIX, {}) or {}).get(self._camera_slot, {}) or {}

    def _get_thresholds(self) -> dict:
        return (self._config.get(_DARK_THRESHOLD_PREFIX, {})
                or {}).get(self._camera_slot, {}) or {}

    def _get_class_names(self) -> list:
        """
        Nombres de clase de todos los modelos declarados, sin repetir y en orden.

        Se recorren todos y no un slot fijo porque la pestaña no tiene por qué saber cómo se
        llama el modelo de este proyecto: un pipeline de dos etapas tendría dos, y las dos
        pueden necesitar umbral.
        """
        names = []
        for model_slot in (self._config.get("inference.models", {}) or {}):
            for class_name in (self._config.get(
                    f"inference.models.{model_slot}.class_names", []) or []):
                if str(class_name) not in names:
                    names.append(str(class_name))
        return names
