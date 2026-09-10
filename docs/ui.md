# La interfaz gráfica

Cómo se toca `ui/`: dónde vive cada atributo —color, texto, medida, estado— y qué hay
que escribir para agregar una pantalla, un campo de configuración o un widget.

El público de este documento es quien forkea el template y tiene que armar la vista de
operador de su instalación. El mapa del repo y las decisiones generales están en
[CLAUDE.md](../CLAUDE.md); las convenciones de nombres, comentarios y límites, en
`.claude/skills/`.

## Cómo se la mira andando

    .venv\Scripts\python.exe manual_test\ui\ui_app.py       # Windows
    .venv/bin/python manual_test/ui/ui_app.py               # Linux

Levanta la aplicación completa con cámaras sintéticas y su propio `config.yaml` al lado.
No son dobles: los hilos de captura, el de inferencia, el monitor de hardware, el
servidor HTTP de video, los bitfields y el esquema de registros son los del repo. Lo
único simulado son los frames —del `mock` driver— y el estado de los servicios que la
prueba no levanta.

Es además **la referencia de cómo se cablea la UI**: lo que hace `_DemoApp` es lo que va
a hacer `main.py`, con los mismos métodos públicos. Su docstring lista qué mirar y qué
probar, incluida una advertencia que conviene leer antes de apretar Guardar: **el
guardado reescribe el `config.yaml` y se pierden los comentarios**, porque
`ConfigManager.save()` vuelca el diccionario en memoria y el YAML no los conserva. La
copia comentada es la de git.

## La regla de oro

**Ningún atributo visual se escribe dos veces.** Cada uno tiene un dueño, y si hay que
cambiarlo se cambia ahí:

| Atributo | Dónde vive | Cómo se usa |
|---|---|---|
| Color de un widget estándar | `ui/styles/dark.qss` y `light.qss` | selector de QSS; nadie llama a `setStyleSheet()` |
| Color de algo que se dibuja | `ui/theme.py` (`get_palette`, `get_color`) | el widget lo pide en su `set_dark()` |
| Color de una serie de gráfica | `ui/theme.py` (`SERIES_COLORS`) | `RealtimeChart(series_role="cpu")` |
| Color de una detección | `system/inference/overlay.py` | **no es de la UI**: el frame llega anotado |
| Texto que ve el operador | `ui/strings.py` | `tr("clave")`; nunca un literal |
| Medida (ancho, alto, margen) | constante `_ALGO_PX` del módulo que la usa | si la usan dos módulos, va en `ui/widgets/form.py` |
| Alto de una pestaña | no se declara: la envuelve un área con scroll | ver «Agregar una pestaña» |
| Estado de un servicio | `ui/service_status.py` | `describe(service, status)` |
| Estado de un chip | `ui/widgets/status_chip.py` (`CHIP_*`) | `chip.set_state(CHIP_OK)` |
| Valor de configuración | `config.yaml` | la pestaña dueña de esa sección |

Si un color, un texto o una medida aparecen en dos archivos, uno de los dos está mal.

## Colores y temas

Hay dos temas y **tienen que espejarse regla por regla**. Un selector que existe en
`dark.qss` y no en `light.qss` deja al widget sin estilo cuando el operador cambia de
tema, y no hay forma de darse cuenta sin probarlo.

La línea entre el QSS y `theme.py` es una sola pregunta: **¿puede Qt pintar ese widget
solo?**

- **Sí** → va al QSS. Botones, inputs, tablas, pestañas, groupbox, labels.
- **No** → va a `theme.py`. Todo lo que se dibuja con `QPainter`, las series de
  QtCharts y los colores de los *items* de una `QTableWidget`, que el QSS no alcanza.

El tema se aplica desde arriba: `MainWindow.apply_theme(dark)` carga la hoja de estilos
y llama al `apply_theme()` de cada vista, que lo baja a los widgets que se pintan por
código. Un widget nuevo que dibuja tiene que exponer `set_dark(bool)` y que su vista lo
llame; si no, se queda con el tema del arranque.

**El único estilo inline permitido es una animación**, porque el QSS no interpola
colores: hoy es el destello del footer (`main_window.py`). Cualquier otro
`setStyleSheet()` en un widget es un error de límite.

### Estados por propiedad de Qt

Los colores que dependen de un estado no se aplican por código: el widget deja el estado
en una propiedad de Qt y el QSS lo pinta.

```python
self.setProperty("state", "ok")
self.style().unpolish(self)     # Qt no reevalúa el QSS solo
self.style().polish(self)
```

```css
QLabel#statusChip[state="ok"] { background-color: rgba(190,255,190,120); }
```

Las propiedades en uso son `state` (chips, indicadores de GPIO), `active` (botón de
navegación de la vista actual) y `selected` (filtro de log elegido). Agregar un estado
es una regla en cada uno de los dos temas.

## Textos e idiomas

`ui/strings.py` es la tabla de idiomas: **la clave es un identificador estable en
inglés** y el valor es el texto en cada idioma soportado (`es`, `en`, `pt`).

```python
from ui.strings import tr
label = QLabel(tr("cam_box_roi"))
```

- **Ningún widget escribe un literal visible.** Si hace falta un texto nuevo, se agrega
  la clave con sus tres traducciones y se usa `tr()`.
- Un texto sin traducir cae al idioma de respaldo (`es`), así que se puede traducir de a
  partes.
- Una clave que no existe devuelve la clave misma y deja un warning: el hueco se ve en
  pantalla en vez de romper la vista.
- El idioma sale de `ui.language` del config y **queda fijo por corrida**: cada widget
  resuelve su texto al construirse, así que cambiarlo con la aplicación andando dejaría
  la pantalla partida en dos idiomas. El panel lo guarda como cualquier otra clave y la
  nota al pie del grupo «Interfaz» avisa que toma efecto al reiniciar. `set_language()`
  queda para las pruebas y para quien arme la UI antes de mostrarla.
- Para agregar un idioma: sumar su código a `LANGUAGES` y su entrada a cada texto.

Los nombres del código siguen en inglés y los comentarios en español, como en todo el
repo: lo que cambia de idioma es sólo lo que ve el operador.

## Estructura

```
ui/
  strings.py            tabla de textos por idioma + tr()
  theme.py              paletas de color y carga del QSS
  service_status.py     estado de un servicio -> texto y color del chip
  main_window.py        header, las tres vistas y la barra de estado
  views/
    monitor_view.py     barra lateral + área central VACÍA (la llena el fork)
    config_view.py      cascarón: arma las pestañas y guarda una sola vez
    diagnostics_view.py cascarón: arma las pestañas y reparte lo que le llega
    config/             una pestaña por sección del config.yaml
      abstract_tab.py   el contrato: TITLE_KEY, load(), save()
      cameras_tab.py video_tab.py inference_tab.py collector_tab.py
      telemetry_tab.py modbus_tab.py system_tab.py
      process_tab.py    VACÍA en el template: la llena cada fork
    diagnostics/        una pestaña por tema de diagnóstico
      abstract_tab.py   el contrato: TITLE_KEY, apply_theme()
      hardware_tab.py modbus_tab.py video_tab.py logs_tab.py
  widgets/
    form.py             constructores de campos, filas, grupos y tarjetas
    status_chip.py      chip de estado con cuatro colores semánticos
    log_table.py        tabla de eventos FIFO con filtro por nivel
    realtime_chart.py   gráfica de línea de ventana fija
    camera_panel.py     feed de UNA cámara + chips + recuadro del ROI
    camera_grid.py      N paneles, con foco y modo crudo/anotado
  dialogs/
    roi_dialog.py       dibujar el ROI con el mouse sobre el frame en vivo
    gpio_dialog.py      entradas y salidas digitales
  styles/               dark.qss y light.qss
  icons/                logos y el .ico de la ventana
```

## La vista de monitor viene vacía

`MonitorView` trae **sólo la barra lateral** —estado de los seis canales de salida y log
de eventos— y un área central vacía. Qué se mira mientras la línea trabaja es lo más
específico de cada instalación, así que el template no lo decide.

El fork le pasa su widget:

```python
window.monitor_view.set_content(CameraGrid(cfg))     # el caso normal, ya escrito
```

`CameraGrid` es genérica y cubre lo que casi todos los proyectos necesitan:

- **muestra todas las cámaras configuradas**, no las que están andando: una cámara caída
  queda en su lugar con «SIN SEÑAL», porque el operador tiene que ver que falta y no
  descubrirlo porque la grilla cambió de forma;
- **selector de foco**: ver todas, o una sola ocupando toda el área. Se salta de una
  cámara a otra sin pasar por la grilla completa, porque un operador que compara dos
  cámaras no quiere dar dos pasos por cada cambio;
- **selector de modo** crudo / anotado, que publica por `mode_changed` y **no elige qué
  frame entra**: eso lo decide el cableado, que es el que conoce el hilo de captura y el
  de inferencia.

Un proyecto que quiera la grilla más sus propios paneles arma su layout y le pasa ese.

### Otra disposición de cámaras

`CameraGrid` toma por defecto todas las cámaras de `cameras:`, y con `camera_slots`
toma sólo esas y en ese orden. Eso alcanza para cualquier disposición sin escribir un
widget de cero ni editar la grilla: tres cámaras en una pantalla y tres en otra son dos
grillas dentro del widget del fork.

```python
class DualBeltView(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._grids = (
            CameraGrid(config, camera_slots=("camera_1", "camera_2", "camera_3")),
            CameraGrid(config, camera_slots=("camera_4", "camera_5", "camera_6")),
        )
        tabs = QTabWidget()
        tabs.addTab(self._grids[0], tr("mi_pantalla_entrada"))
        tabs.addTab(self._grids[1], tr("mi_pantalla_salida"))
        QVBoxLayout(self).addWidget(tabs)

    # La misma API por slot que la grilla: así el cableado no cambia.
    def update_frame(self, frame_bgr, camera_slot):
        for grid in self._grids:
            grid.update_frame(frame_bgr, camera_slot)
```

Tres cosas que hacen que esto funcione y conviene no romper:

- **La API por slot es lo que se conserva, no la clase.** Mientras el widget exponga
  `update_frame(frame, slot)` y `update_status(status, slot)`, `main.py` conecta las
  señales de captura igual que con una grilla sola. Si en cambio expone `grid_a` y
  `grid_b` y deja que el cableado reparta, cada fork reescribe ese reparto.
- **Cada grilla descarta lo que no es suyo**, así que se le puede ofrecer el frame a
  todas y no hace falta un mapa de slot a grilla. `camera_slots` está disponible si el
  compositor igual prefiere repartir.
- **Un panel en una pestaña que no está al frente devuelve `isVisible() == False`**, y
  el `annotate_gate` del motor es `(camera_slot) -> bool`: gateando por visibilidad, el
  overlay no se dibuja para las cámaras que nadie está mirando. Con una sola grilla que
  muestra todo, esa optimización no existe.

Un slot que no está en `cameras:` se descarta con un warning y no frena la grilla: las
demás se muestran igual. Y la agrupación en sí va en el código del widget, no en el
`config.yaml`: la jerarquía del config espeja los módulos, no las pantallas.

## Agregar una pestaña de configuración

Una pestaña es dueña de **una sección** de `config.yaml`. Agregar una sección es un
archivo nuevo y una línea en `_TAB_CLASSES` de `config_view.py`; ningún otro archivo se
toca.

```python
class MiTab(AbstractConfigTab):
    TITLE_KEY = "tab_mi_seccion"      # clave de ui/strings.py
    # El alto no se declara: el ConfigView envuelve toda pestaña en un área con
    # scroll, así que puede tener los campos que necesite.

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        box, form = build_group_box(tr("mi_box"))
        self._mi_campo = add_form_row(form, tr("mi_label"), build_spin_box(0, 100, 50))
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        self.load()

    def load(self):
        self._mi_campo.setValue(int(self._config.get("mi_seccion.mi_campo", 50)))

    def save(self):
        self._config.set("mi_seccion.mi_campo", self._mi_campo.value())
```

Tres reglas del contrato, que están en `ui/views/config/abstract_tab.py`:

1. **`save()` no persiste.** Deja los valores con `set()` y nada más; el `save()` del
   archivo lo llama el `ConfigView` una vez, cuando todas las pestañas escribieron. Así
   un error en la pestaña cinco no deja el `config.yaml` a medio escribir.
2. **`load()` se puede llamar muchas veces.** Es lo que hace el botón de descartar.
3. **`TITLE_KEY` es una clave de idioma**, no un texto.

### La trampa de los combos

**Un valor del config que no está entre las opciones de un combo se pierde en
silencio.** `setCurrentText()` sobre un combo no editable es un no-op cuando el item no
existe: el combo se queda en su primera opción y el `save()` la escribe encima. Abrir el
panel y guardar alcanzaba para cambiar la marca de una cámara sin configurar a la
primera del catálogo.

Por eso **todo combo alimentado por el config se carga con `set_combo_value()`**, que
agrega el valor desconocido como opción y lo deja elegido: vuelve al archivo tal como
estaba y se ve en pantalla que hay que elegir.

```python
set_combo_value(self._tipo, self._config.get("inference.models.model_1.type", "mock"))
```

**Y si el texto del combo se traduce, el valor no puede salir del texto.** Los niveles de
log, los tipos de modelo o la paridad se muestran tal como se guardan, así que ahí el par
de arriba alcanza. Pero rotación, modo del dataset, marca de cámara y codec muestran una
etiqueta y guardan otra cosa, y reconstruir el valor desde la etiqueta lo ata al idioma
con el que se armó el combo: después de cambiar de idioma el mapeo no encontraba nada y
el `save()` escribía «Sob demanda» donde iba `on_demand`. Esos combos se arman con
`build_value_combo_box()` —el valor va en el item— y se leen con `currentData()`:

```python
self._mode_combo = add_form_row(
    form, tr("col_mode"),
    build_value_combo_box({tr(key): value for key, value in _MODES.items()},
                          _MODE_ON_DEMAND),
)
...
set_combo_data(self._mode_combo, self._config.get("image_collector.mode", _MODE_ON_DEMAND))
...
self._config.set("image_collector.mode", self._mode_combo.currentData())
```

`set_combo_data()` mantiene la red de arriba: un valor que no está entre las opciones
entra como opción con su forma cruda y vuelve al archivo igual. Un `rotation` que el repo
no entiende se muestra tal cual; caer a «sin rotación» cambiaría en silencio cómo entra
la imagen.

Un combo así se conecta por `currentIndexChanged` y no por `currentTextChanged`: el
índice es lo que identifica la opción cuando el texto no la identifica.

## Agregar una pestaña de diagnóstico

Una pestaña de diagnóstico **muestra, no edita**: recibe lo que le empujan y no tiene
`load()` ni `save()`. Se agrega igual —un archivo y una línea en `_TAB_CLASSES` de
`diagnostics_view.py`— y expone `apply_theme()` si dibuja algo por código.

Acá el scroll sí se declara, con `IS_SCROLLABLE`: una pestaña que apila contenido de
alto fijo lo pone en True, y una que ya tiene una tabla adentro lo deja en False, porque
la tabla scrollea sola y anidar las dos barras deja la de afuera moviendo algo que nunca
crece. `IS_CENTERED = False` es para las que tienen que ocupar todo el ancho.

`DiagnosticsView.set_service_status()` reparte a todas las pestañas que tengan ese
método, y **una pestaña que no conoce un servicio lo ignora**: así el cableado publica
todo en un solo lugar y no tiene que saber quién lo dibuja.

Una pestaña que además necesita **disparar una acción** —no sólo mostrar— la emite por
una señal y la vista la reenvía hacia arriba hasta `MainWindow`, que es la única con la
que el cableado habla. La de licencia lo hace con dos: `license_export_requested(str)` y
`license_install_requested(str)`, cada una con la ruta que el operador eligió. La
pestaña no sabe qué pasa después, y el cableado no sabe qué botón se apretó.

Cuando esa acción tiene un resultado que el operador tiene que ver, vuelve por un método
—`show_license_result(ok, mensaje)`— y **no se mezcla con el estado**: el mensaje lo
limpia el próximo clic, no el próximo estado, así el orden en que el cableado llame a
los dos métodos deja de importar.

## Qué NO hace la UI

`ui/` es presentación. Estas cinco cosas son errores de límite, no atajos:

1. **No abre cámaras, sockets, ni archivos** que no sean sus propios estilos e iconos.
   Un `QFileDialog` **no** es una excepción a esto: devuelve una ruta y no toca el
   archivo. La pestaña de licencia es el ejemplo — pregunta dónde guardar la solicitud o
   cuál `.lic` instalar, emite la ruta por una señal, y el que abre, verifica y copia es
   el cableado. Elegir una ruta es presentación; leerla y escribirla, no.
2. **No arma direcciones ni rutas de protocolo.** Las URLs de los streams llegan hechas
   por `set_stream_urls()`: la forma de la ruta la conoce el servidor de video, y si la
   UI la compusiera el formato quedaría definido en dos lugares.
3. **No corre bits ni conoce direcciones de Modbus.** La tabla de registros sale de
   `SCHEMA.descriptions()`; no hay una sola dirección escrita en `ui/`.
4. **No calcula.** Si hace falta un número, lo calcula el módulo dueño del dato. La
   pestaña de hardware consume las claves de `SystemMonitor.get_metrics()` tal como
   salen.
5. **No importa un `QThread` ni un servidor.** Lo que sí puede importar es una tabla de
   datos (`CAMERA_CATALOG`), un módulo puro (`system/formats/com_status.py`) o un
   vocabulario con dueño único (`MODE_RAW`, `REGISTERED_MODELS`).

## Cómo la cablea `main.py`

El cableado le habla **sólo a `MainWindow`**, que reparte a las vistas. No hay que saber
qué pantalla dibuja cada cosa:

```python
window = MainWindow(cfg)
window.monitor_view.set_content(CameraGrid(cfg))

# Estado de los servicios (vocabulario STATUS_* de cada subsistema)
window.set_service_status(service_status.SERVICE_MODBUS_TCP, modbus.tcp_status)
window.set_service_status(service_status.SERVICE_VIDEO_RTSP, rtsp.status)

# URLs de los streams, pedidas a quien las publica
window.set_stream_urls(service_status.SERVICE_VIDEO_RTSP, rtsp.get_stream_urls())

# Métricas, registros y logs
window.diagnostics_view.update_hardware_metrics(monitor.get_metrics())
window.diagnostics_view.update_modbus_values(batch)
window.log_event("WARNING", "mensaje", "Modulo")

# GPIO: sin esto el botón del header no aparece
window.set_gpio(gpio_ctrl, gpio_thread)
```

Los frames van a la grilla con la firma que emiten los hilos de captura,
`(frame, camera_slot)`, así que se conectan directo:

```python
capture_thread.frame_ready.connect(grid.update_frame)
capture_thread.status_updated.connect(grid.update_status)
capture_thread.status_updated.connect(window.diagnostics_view.update_camera_status)
```

Qué frame entra —el crudo o el anotado— lo decide el cableado según `grid.mode`, que es
lo que el operador eligió en el selector.

## Lo que falta

- **`main.py`**: no existe todavía. Hasta que exista, quien construye esta ventana es
  `manual_test/ui/ui_app.py`, que hace de cableado.
- **El módulo de GPIO.** `ui/dialogs/gpio_dialog.py` es la mitad de UI de un subsistema
  que el template no tiene: espera un controlador con la interfaz que documenta su
  docstring, y el botón del header aparece sólo cuando se lo inyecta.
- **Los intrínsecos del lente** se muestran pero no se editan: una matriz 3x3 y hasta 14
  coeficientes no son un formulario, y equivocar un dígito ahí mueve todas las
  coordenadas del proyecto. Se editan en el `config.yaml`.
- **La pestaña de proceso llega vacía**, y es a propósito: `process:` es la única sección
  cuya *forma* cambia entre proyectos, así que el template no puede declarar sus campos.
  La pestaña existe para que el punto de extensión se vea, y su docstring trae los dos
  patrones que cubren casi todo —un valor suelto y un mapa por cámara—. La validación de
  lo obligatorio no va ahí: un valor cuya ausencia corrompe una medición tiene que frenar
  el arranque, y eso lo chequea `main.py`, que es quien lee la sección y la inyecta.
- **No hay tests de `ui/`.** La suite del repo corre sin GUI, y así se queda. Lo que sí
  se puede testear sin Qt es `service_status.describe()`, que devuelve strings.
