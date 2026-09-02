"""
Pestaña de cámaras: una sub-pestaña por slot de `cameras:`.

Los slots salen del config y son fijos por proyecto: la pestaña edita los que están y
no crea ni borra ninguno. Agregar una cámara es una edición del `config.yaml` y un
reinicio, porque la topología del sistema no es una preferencia de la UI.

Los intrínsecos del lente se muestran pero no se editan: una matriz 3x3 y hasta 14
coeficientes no son un formulario, y equivocar un dígito ahí mueve todas las
coordenadas del proyecto. Se editan en el config.yaml y acá se ve si están puestos.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QTabWidget, QVBoxLayout, QWidget

from system.config_manager import ConfigManager
from tools.camera.camera_catalog import CAMERA_CATALOG

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_combo_box, build_double_spin_box,
    build_group_box, build_line_edit, build_readonly_line_edit, build_spin_box,
    set_combo_value, wire_enable_toggle,
)

# Texto que ve el operador -> valor de `rotation` en el config. El valor es el que
# entiende el driver: no se traduce en el camino.
_ROTATIONS = {
    "rotation_none":  None,
    "rotation_90cw":  "90cw",
    "rotation_90ccw": "90ccw",
    "rotation_180":   "180",
}

_MAX_FRAME_SIDE_PX = 8192      # tope de los spin de ROI; cubre cualquier sensor del catálogo
_MAX_EXPOSURE_US = 1000000
_MAX_GAIN = 48.0
_MAX_FPS = 240


class CamerasTab(AbstractConfigTab):
    """Sub-pestañas de cámara. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_cameras"

    open_roi_requested = Signal(str)     # slot de la cámara cuyo ROI hay que dibujar

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._forms: dict[str, _CameraForm] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        for camera_slot in (self._config.get("cameras", {}) or {}):
            form = _CameraForm(self._config, camera_slot)
            form.open_roi_requested.connect(self.open_roi_requested)
            self._forms[camera_slot] = form
            tabs.addTab(form, form.camera_name)

    def load(self):
        for form in self._forms.values():
            form.load()

    def save(self):
        for form in self._forms.values():
            form.save()

    def refresh_roi_fields(self, camera_slot: str):
        """Recarga los campos de ROI de una cámara tras guardar el diálogo interactivo."""
        form = self._forms.get(camera_slot)
        if form is not None:
            form.load_roi()


class _CameraForm(QWidget):
    """Formulario de una sola cámara: conexión, adquisición, ROI y calibración."""

    open_roi_requested = Signal(str)

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        self._prefix = f"cameras.{camera_slot}"

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_connection_box())
        layout.addWidget(self._build_acquisition_box())
        layout.addWidget(self._build_roi_box())
        layout.addWidget(self._build_calibration_box())
        layout.addStretch()

        self.load()

    @property
    def camera_name(self) -> str:
        """Texto de la sub-pestaña: el `name` del config, o el slot si está vacío."""
        return str(self._config.get(f"{self._prefix}.name", "") or self._camera_slot)

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_connection_box(self) -> QWidget:
        box, form = build_group_box(tr("cam_box_connection"))
        self._enabled_check = add_check_row(form, tr("cam_enabled"), True)
        self._name_edit = add_form_row(form, tr("field_name"), build_line_edit(""))
        self._brand_combo = add_form_row(
            form, tr("cam_brand"),
            build_combo_box([entry["label"] for entry in CAMERA_CATALOG.values()], ""),
        )
        self._model_combo = add_form_row(form, tr("cam_model"), build_combo_box([], ""))
        self._address_edit = add_form_row(
            form, tr("cam_address"), build_line_edit("", "192.168.0.10")
        )
        self._rotation_combo = add_form_row(
            form, tr("cam_rotation"),
            build_combo_box([tr(key) for key in _ROTATIONS], tr("rotation_none")),
        )
        self._brand_combo.currentTextChanged.connect(self._on_brand_changed)
        return box

    def _build_acquisition_box(self) -> QWidget:
        box, form = build_group_box(tr("cam_box_acquisition"))
        self._fps_spin = add_form_row(
            form, tr("cam_fps_limit"), build_spin_box(1, _MAX_FPS, 15)
        )
        self._exposure_spin = add_form_row(
            form, tr("cam_exposure"), build_spin_box(1, _MAX_EXPOSURE_US, 25000)
        )
        self._gain_spin = add_form_row(
            form, tr("cam_gain"), build_double_spin_box(0.0, _MAX_GAIN, 1.0)
        )
        self._illumination_spin = add_form_row(
            form, tr("cam_illumination_min"), build_spin_box(0, 100, 50)
        )
        return box

    def _build_roi_box(self) -> QWidget:
        box, form = build_group_box(tr("cam_box_roi"))
        self._roi_check = add_check_row(form, tr("cam_roi_enabled"), False)
        self._roi_spins = {}
        for key, label_key in (("x_px", "cam_roi_x"), ("y_px", "cam_roi_y"),
                               ("width_px", "cam_roi_width"), ("height_px", "cam_roi_height")):
            self._roi_spins[key] = add_form_row(
                form, tr(label_key), build_spin_box(0, _MAX_FRAME_SIDE_PX, 0)
            )
        roi_button = QPushButton(tr("cam_roi_tool"))
        roi_button.clicked.connect(
            lambda: self.open_roi_requested.emit(self._camera_slot)
        )
        form.addRow("", roi_button)
        wire_enable_toggle(self._roi_check, list(self._roi_spins.values()) + [roi_button])
        return box

    def _build_calibration_box(self) -> QWidget:
        box, form = build_group_box(tr("cam_box_calibration"))
        self._calibration_field = add_form_row(
            form, tr("cam_box_calibration"), build_readonly_line_edit("")
        )
        add_hint_row(form, tr("cam_calibration_note"))
        return box

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._enabled_check.setChecked(bool(self._config.get(f"{self._prefix}.enabled", True)))
        self._name_edit.setText(str(self._config.get(f"{self._prefix}.name", "") or ""))

        brand_label = _get_brand_label(str(self._config.get(f"{self._prefix}.brand", "")))
        self._brand_combo.blockSignals(True)
        set_combo_value(self._brand_combo, brand_label)
        self._brand_combo.blockSignals(False)
        self._reload_models(brand_label, str(self._config.get(f"{self._prefix}.model", "")))

        self._address_edit.setText(str(self._config.get(f"{self._prefix}.address", "") or ""))
        rotation = self._config.get(f"{self._prefix}.rotation", None)
        set_combo_value(self._rotation_combo, _get_rotation_label(rotation))

        acquisition = self._config.get(f"{self._prefix}.acquisition", {}) or {}
        self._fps_spin.setValue(int(acquisition.get("fps_limit", 15)))
        self._exposure_spin.setValue(int(acquisition.get("exposure_time_us", 25000)))
        self._gain_spin.setValue(float(acquisition.get("gain", 1.0)))
        self._illumination_spin.setValue(
            int(self._config.get(f"{self._prefix}.illumination_min", 50))
        )

        # La casilla se lee acá y no en `load_roi()`: son dos llamadores que quieren
        # cosas distintas. Esto es la carga completa desde el archivo; `load_roi()` es el
        # callback del diálogo, que sólo escribe geometría y no tiene que pisar lo que el
        # operador acaba de tildar. Leerla en el lugar equivocado la dejó sin leer en
        # ninguno, y como `save()` la escribe de vuelta, abrir la pestaña y guardar apagaba
        # un ROI que estaba prendido.
        roi_enabled = (self._config.get(f"{self._prefix}.roi", {}) or {}).get("enabled", False)
        self._roi_check.setChecked(bool(roi_enabled))
        self.load_roi()

        calibration = self._config.get(f"{self._prefix}.calibration", {}) or {}
        has_matrix = bool(calibration.get("camera_matrix"))
        self._calibration_field.setText(
            tr("cam_calibration_set") if has_matrix else tr("cam_calibration_unset")
        )

    def load_roi(self):
        """
        Recarga sólo la geometría del ROI: es lo único que cambia el diálogo interactivo.

        **El `enabled` no se toca a propósito.** El diálogo escribe las cuatro coordenadas
        y nada más, así que releer la casilla desde el archivo pisaría lo que el operador
        acaba de marcar y todavía no guardó — y como los campos y el botón cuelgan de esa
        casilla, se apagan solos justo después de definir el ROI.
        """
        roi = self._config.get(f"{self._prefix}.roi", {}) or {}
        for key, spin in self._roi_spins.items():
            spin.setValue(int(roi.get(key, 0)))

    def save(self):
        brand_key = _get_brand_key(self._brand_combo.currentText())
        self._config.set(f"{self._prefix}.enabled", self._enabled_check.isChecked())
        self._config.set(f"{self._prefix}.name", self._name_edit.text())
        self._config.set(f"{self._prefix}.brand", brand_key)
        self._config.set(f"{self._prefix}.model", self._model_combo.currentText())
        # El driver no se elige a mano: sale del catálogo, que es el que sabe qué
        # implementación atiende a cada fabricante. Con una marca que no está en el
        # catálogo se deja el `driver` que ya tenía el config: escribir '' dejaría la
        # cámara sin driver por haber abierto el panel.
        driver = CAMERA_CATALOG.get(brand_key, {}).get("driver", "")
        if driver:
            self._config.set(f"{self._prefix}.driver", driver)
        self._config.set(f"{self._prefix}.address", self._address_edit.text())
        self._config.set(f"{self._prefix}.rotation",
                         _get_rotation_value(self._rotation_combo.currentText()))

        self._config.set(f"{self._prefix}.acquisition.fps_limit", self._fps_spin.value())
        self._config.set(f"{self._prefix}.acquisition.exposure_time_us",
                         self._exposure_spin.value())
        self._config.set(f"{self._prefix}.acquisition.gain", self._gain_spin.value())
        self._config.set(f"{self._prefix}.illumination_min", self._illumination_spin.value())

        self._config.set(f"{self._prefix}.roi.enabled", self._roi_check.isChecked())
        for key, spin in self._roi_spins.items():
            self._config.set(f"{self._prefix}.roi.{key}", spin.value())

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_brand_changed(self, brand_label: str):
        self._reload_models(brand_label, "")

    def _reload_models(self, brand_label: str, current_model: str):
        """
        Rearma la lista de modelos del fabricante elegido.

        Un driver simulado no tiene dirección de red: el campo se apaga para que no
        quede una IP escrita que no se usa.
        """
        entry = CAMERA_CATALOG.get(_get_brand_key(brand_label), {})
        models = list(entry.get("models", []))
        # Un modelo que no está en el catálogo se agrega como opción en vez de perderse:
        # ver el porqué en `build_combo_box`.
        if current_model and current_model not in models:
            models.insert(0, current_model)
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(models)
        if current_model:
            self._model_combo.setCurrentText(current_model)
        self._model_combo.blockSignals(False)
        self._address_edit.setEnabled(entry.get("driver") != "mock")


def _get_brand_label(brand_key: str) -> str:
    """Clave de `brand` del config -> texto que muestra el catálogo."""
    entry = CAMERA_CATALOG.get(brand_key, {})
    return str(entry.get("label", brand_key))


def _get_brand_key(brand_label: str) -> str:
    """Texto del catálogo -> clave de `brand` del config."""
    for key, entry in CAMERA_CATALOG.items():
        if entry.get("label") == brand_label:
            return key
    return brand_label


def _get_rotation_label(rotation: object) -> str:
    """
    Valor de `rotation` -> texto que se muestra.

    Una rotación que el repo no conoce se muestra tal cual y vuelve al config igual:
    caer a «sin rotación» cambiaría, en silencio, cómo entra la imagen.
    """
    for key, value in _ROTATIONS.items():
        if value == rotation:
            return tr(key)
    return "" if rotation is None else str(rotation)


def _get_rotation_value(rotation_label: str) -> object:
    """Texto que se muestra -> valor de `rotation`. Uno desconocido vuelve tal cual."""
    for key, value in _ROTATIONS.items():
        if tr(key) == rotation_label:
            return value
    return rotation_label or None
