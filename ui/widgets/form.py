"""
Constructores de los campos y los contenedores de un formulario de configuración.

Toda pestaña de `ui/views/config/` se arma con estas funciones, y por eso los anchos,
los altos de fila y el aspecto de una tarjeta son iguales en todas sin que ninguna
los repita. Una pestaña nueva no elige medidas: pide `build_group_box()` y
`add_form_row()`.

Módulo de presentación: no lee ni escribe el config. Los valores entran por
parámetro y quien los saca del config es la pestaña, que es la que sabe qué clave
alimenta cada campo.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGraphicsOpacityEffect,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSpinBox, QVBoxLayout,
    QWidget,
)

_LABEL_MIN_WIDTH_PX = 240    # ancho mínimo de la etiqueta, para alinear todas las filas
_ROW_MIN_HEIGHT_PX = 28      # alto mínimo de una fila, para que todas midan igual
_BOX_MIN_WIDTH_PX = 640      # ancho mínimo de un QGroupBox
_CARD_MIN_WIDTH_PX = 720     # ancho mínimo de la tarjeta de una pestaña
_CARD_MAX_WIDTH_PX = 1600    # ancho máximo de la tarjeta; de ahí en más queda centrada
_CARD_MARGIN_PX = 14
_DISABLED_OPACITY = 0.35     # opacidad de un campo que su checkbox de habilitado apagó

_CARD_OBJECT_NAME = "configCard"     # lo pinta el QSS del tema
_HINT_OBJECT_NAME = "hintLabel"


def build_group_box(title: str) -> tuple[QGroupBox, QFormLayout]:
    """Devuelve un QGroupBox con su QFormLayout, listos para agregarle filas."""
    box = QGroupBox(title)
    box.setMinimumWidth(_BOX_MIN_WIDTH_PX)
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setSpacing(8)
    box.setLayout(form)
    return box, form


def add_form_row(form: QFormLayout, label: str, widget: QWidget) -> QWidget:
    """Agrega una fila etiqueta + campo con las medidas del formulario, y devuelve el campo."""
    label_widget = QLabel(label)
    label_widget.setMinimumWidth(_LABEL_MIN_WIDTH_PX)
    label_widget.setMinimumHeight(_ROW_MIN_HEIGHT_PX)
    widget.setMinimumHeight(_ROW_MIN_HEIGHT_PX)
    form.addRow(label_widget, widget)
    return widget


def add_check_row(form: QFormLayout, text: str, checked: bool) -> QCheckBox:
    """Agrega un checkbox suelto —sin etiqueta a la izquierda— y lo devuelve."""
    check = QCheckBox(text)
    check.setChecked(bool(checked))
    check.setMinimumHeight(_ROW_MIN_HEIGHT_PX)
    form.addRow("", check)
    return check


def add_hint_row(form: QFormLayout, text: str) -> QLabel:
    """Agrega una nota al pie de un grupo. El color lo pone el QSS por objectName."""
    hint = QLabel(text)
    hint.setObjectName(_HINT_OBJECT_NAME)
    hint.setWordWrap(True)
    form.addRow("", hint)
    return hint


def build_spin_box(min_value: int, max_value: int, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(min_value, max_value)
    spin.setValue(int(value))
    return spin


def build_double_spin_box(min_value: float, max_value: float, value: float, *,
                          decimals: int = 2, step: float = 0.1,
                          special_value_text: str = "") -> QDoubleSpinBox:
    """
    Spin decimal. `special_value_text` es lo que muestra en el mínimo, si se le da.

    Es para las claves donde el mínimo no es un número sino un modo —0 = automático, 0 =
    nativo—: sin esto el operador ve un 0 que parece un valor y el rango no le dice que
    significa otra cosa.
    """
    spin = QDoubleSpinBox()
    spin.setRange(min_value, max_value)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    if special_value_text:
        spin.setSpecialValueText(special_value_text)
    spin.setValue(float(value))
    return spin


def build_combo_box(items: list, current: str) -> QComboBox:
    """
    Combo con `items` y `current` seleccionado.

    **Un `current` que no está en la lista se agrega como opción y queda elegido.** Sin
    eso el combo caería en su primer item y el `save()` escribiría ese valor: abrir el
    panel y guardar cambiaría, en silencio, una cámara sin configurar a la primera
    marca del catálogo, o un modelo que el fork todavía no registró en la fábrica al
    primero que haya. La opción intrusa se ve en pantalla, que es lo que hace falta para
    darse cuenta de que hay que elegir.
    """
    combo = QComboBox()
    options = [str(item) for item in items]
    if current not in ("", None) and str(current) not in options:
        options.insert(0, str(current))
    combo.addItems(options)
    if current not in ("", None):
        combo.setCurrentText(str(current))
    return combo


def set_combo_value(combo: QComboBox, value: object):
    """
    Selecciona `value` en el combo, agregándolo como opción si no está.

    Es lo que hace falta en un `load()`: `setCurrentText()` sobre un combo no editable
    es un **no-op silencioso** cuando el item no existe, así que el combo se queda en
    su primera opción y el `save()` la escribe encima del valor del config. Con esto un
    valor desconocido se ve en pantalla y vuelve al archivo tal como estaba.

    Sólo para los combos cuyo texto **es** el valor del config. Si el texto se traduce,
    el par es `build_value_combo_box()` + `set_combo_data()`.
    """
    text = "" if value is None else str(value)
    if not text:
        return
    if combo.findText(text) < 0:
        combo.insertItem(0, text)
    combo.setCurrentText(text)


def build_value_combo_box(options: dict, current: object) -> QComboBox:
    """
    Combo cuyo valor de config viaja en el item, no en el texto que se muestra.

    `options` va del texto visible al valor que va al config, y ese valor se lee con
    `currentData()`. Es lo que corresponde cuando el texto se traduce: reconstruir el
    valor a partir de lo que se ve lo ata al idioma con el que se armó el combo, y
    después de cambiar de idioma el `save()` escribe la etiqueta —«Sob demanda»— en
    lugar del valor.

    Un `current` que no está entre los valores se agrega como opción con su forma
    cruda, por el mismo motivo que en `build_combo_box()`.
    """
    combo = QComboBox()
    for label, value in options.items():
        combo.addItem(str(label), value)
    set_combo_data(combo, current)
    return combo


def set_combo_data(combo: QComboBox, value: object):
    """
    Selecciona el item cuyo dato es `value`, agregándolo como opción si no está.

    El par de `build_value_combo_box()` para el `load()` de una pestaña: busca por el
    valor del config y no por el texto, que cambia con el idioma. Un valor desconocido
    se agrega mostrando su forma cruda y queda elegido, así vuelve al archivo tal como
    estaba; uno vacío deja el combo donde está, porque no hay nada que mostrar.
    """
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return
    text = "" if value is None else str(value)
    if not text:
        return
    combo.insertItem(0, text, value)
    combo.setCurrentIndex(0)


def build_line_edit(value: str, placeholder: str = "") -> QLineEdit:
    line = QLineEdit(str(value if value is not None else ""))
    line.setPlaceholderText(placeholder)
    return line


def build_readonly_line_edit(value: object) -> QLineEdit:
    """Campo de sólo lectura, para lo que se muestra pero se edita en el config.yaml."""
    line = QLineEdit(str(value if value is not None else ""))
    line.setReadOnly(True)
    line.setObjectName("readonlyField")
    return line


def wrap_in_card(inner: QWidget, *, center: bool = True, scroll: bool = False) -> QWidget:
    """
    Envuelve el contenido de una pestaña en la tarjeta del tema.

    `center` deja el contenido centrado con fondo a los lados, que es lo que quieren
    los grupos de campos; en False ocupa todo el ancho, para una tabla. `scroll`
    agrega la barra vertical, para las pestañas que no entran en pantalla.
    """
    card = QWidget()
    card.setObjectName(_CARD_OBJECT_NAME)
    card.setAttribute(Qt.WA_StyledBackground, True)
    card.setMinimumWidth(_CARD_MIN_WIDTH_PX)
    card.setMaximumWidth(_CARD_MAX_WIDTH_PX if center else _CARD_MAX_WIDTH_PX * 4)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(_CARD_MARGIN_PX, _CARD_MARGIN_PX,
                                   _CARD_MARGIN_PX, _CARD_MARGIN_PX)
    card_layout.addWidget(inner)

    wrapper = QWidget()
    wrapper_layout = QHBoxLayout(wrapper)
    wrapper_layout.setContentsMargins(0, 0, 0, 0)
    if center:
        wrapper_layout.addStretch()
    wrapper_layout.addWidget(card)
    if center:
        wrapper_layout.addStretch()

    if not scroll:
        return wrapper

    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    area.setWidget(wrapper)
    return area


def wire_enable_toggle(check: QCheckBox, dependents: list):
    """
    Ata un checkbox de habilitado a los campos que dependen de él.

    Desmarcado los deshabilita y los atenúa. Un dependiente que también es checkbox
    re-emite su `toggled` al volver a habilitarse, así una cadena de tres niveles
    —recolección → dedup → sus campos— queda consistente sin que el llamador la
    recorra a mano.
    """
    def apply_state(checked: bool, widgets: list = dependents):
        for widget in widgets:
            widget.setEnabled(checked)
            if checked:
                widget.setGraphicsEffect(None)
            else:
                effect = QGraphicsOpacityEffect(widget)
                effect.setOpacity(_DISABLED_OPACITY)
                widget.setGraphicsEffect(effect)
            if isinstance(widget, QCheckBox):
                widget.toggled.emit(widget.isChecked() and checked)

    check.toggled.connect(apply_state)
    apply_state(check.isChecked())


def join_list(values: object) -> str:
    """Lista del config -> texto separado por comas, para un QLineEdit."""
    if not values:
        return ""
    if isinstance(values, (list, tuple)):
        return ", ".join(str(value) for value in values)
    return str(values)


def split_list(text: str) -> list[str]:
    """Texto separado por comas -> lista para el config. Descarta los vacíos."""
    return [item.strip() for item in (text or "").split(",") if item.strip()]
