# En qué diverge este equipo del template

Este proyecto es un fork del template de visión artificial de la familia. La regla del
`README.md` es que **la maquinaria se cross-portea sin editarla** y lo del proyecto vive en
un puñado de archivos declarados. Este documento lista dónde no se cumplió eso y por qué,
para que un merge desde el template no pise algo a ciegas ni conserve algo que ya no hace
falta.

El público es quien traiga una versión nueva del template a este repo, o quien lleve una
mejora de acá para allá.

Tres categorías, y se tratan distinto:

| | Qué es | Qué hacer en un merge |
|---|---|---|
| **A. Deuda de compatibilidad** | Nombres viejos que se conservaron porque hay algo afuera —un dashboard, un PLC, un navegador— apuntando a ellos | **Conservar** mientras ese algo exista. Cuando deje de existir, revertir al nombre del template |
| **B. Mejoras genéricas** | Correcciones y puntos de extensión que no tienen nada de esta planta | **Devolver al template** con un PR. Si el template ya las trae, quedarse con la del template |
| **C. Límites mal puestos** | Cosas del proyecto que quedaron dentro de maquinaria | **Arreglar**, no conservar. Están listadas con el arreglo que corresponde |

---

## A. Deuda de compatibilidad

Todo lo de esta sección existe porque el equipo **reemplazó a una versión anterior que ya
estaba en producción**, con su dashboard, su PLC y sus URLs. Un nombre nuevo no migra lo
viejo: la historia queda con el nombre viejo y lo que consulta deja de encontrarla, sin que
nada falle. Son decisiones tomadas a propósito, no accidentes.

### A1. Nombres de las series de InfluxDB

**Dónde:** `main.py`, bloque de `_MEASUREMENT_*`, más `_build_inference_fields()`,
`_build_optics_fields()`, `_build_system_fields()` y `_camera_tags()`.
**Config:** `telemetry.influxdb.legacy_camera_ids`, `telemetry.influxdb.legacy_net_labels`.

| | Template | Este equipo |
|---|---|---|
| Measurements | `system`, `camera`, `services`, `inference` | `sistema`, `camara`, `servicios`, `inferencia`, `optica` |
| Tag del equipo | `device`, de `system.device_id` | `proyecto`, de `project.project_id` |
| Tag de cámara | `camera` = clave del slot | `camara_id` = `cam1` (traducido desde `camera_1`) |
| Salud del hardware | `cpu_usage_pct`, `ram_used_mb`, `disk_free_gb`, `cpu_temp_c`, `gpu_temp_c` | `cpu_usage`, `ram_mb`, `disk_gb`, `temp_cpu`, `temp_gpu` |
| Red | `net_<iface>_rx_mbps` | `rx_eth0`, `tx_eth0`, `rx_eth1`, `tx_eth1` |
| Métricas del proceso | `<métrica>_mean`, `_std`, `_min`, `_max` | `<métrica>` a secas |
| Confianza / iluminación | `confidence_pct_mean`, `illumination_pct_mean` | `confianza`, `iluminacion` |
| Cuántos resultados | `result_count` | `frames` |
| Salud de la óptica | campos de `camera` | measurement `optica` propio |
| Medias de la hora | no existen | `<métrica>_1h` + `cobertura_pct` + `material_presente_pct` |

**Y los tipos.** Toda la serie `sistema` es entera, y los porcentajes instantáneos de
`inferencia` también —la versión anterior los pasaba por `round()`—. InfluxDB fija el tipo
de cada campo con el primer punto que lo trae: un decimal donde hay un entero hace que
rechace **el batch entero**, y se pierden también las otras series que viajaban con él.

**Cómo revertir:** cambiar los `_MEASUREMENT_*` por los del template, sacar
`_LEGACY_SYSTEM_FIELDS`, `_camera_tags()` y `_rounded()`, y volver a la versión del template
de los tres constructores de fields. **Sólo tiene sentido con un bucket nuevo o con el
dashboard rehecho.** La estructura canónica está en
[`influxdb_template.md`](influxdb_template.md); lo que publica hoy, en
[`influxdb.md`](influxdb.md). Los tipos están afirmados campo por campo en
`test/test_main.py::TestLegacyFieldTypes`.

### A2. Rutas de los streams de video

**Dónde:** `config.yaml`, `video.http.stream_names: {camera_1: cinta}`.

La versión anterior publicaba `/cinta/raw` y `/cinta/annotated`, y esas URLs están en
tableros y navegadores que este equipo no controla. La clave renombra el slot **sólo en la
ruta**: en los registros, en la telemetría y en el resto del sistema la cámara sigue siendo
`camera_1`.

**Cómo revertir:** borrar la clave. Las URLs pasan a ser `/camera_1/...`, que es lo natural.
Hacerlo cuando no quede nadie apuntando a `/cinta/`.

El mecanismo que la lee (`_read_stream_names()`) **no** es deuda: es genérico y ya volvió
al template (sección B).

### A3. Mapa de registros Modbus

**Dónde:** `system/modbus/register_map.yaml`.

Las direcciones, las escalas y la semántica son las que el PLC ya tiene programadas, no las
del template. El template numera su bloque de salud distinto (`cpu_usage_pct` en 82) y
escala temperaturas y disco ×10; acá van en las direcciones viejas y sin escala.

**Cómo revertir:** no se revierte. Un mapa de registros es un contrato con el integrador y
se cambia cuando el integrador cambia su programa, no cuando se actualiza el template. Lo
único que se tomó del template es el bitfield del registro 51 y el reloj y la licencia,
que acá ocupan las tres últimas direcciones expuestas, 98-100. El template los tiene
en 97-99 hasta que vuelva el cambio.

---

## B. Mejoras genéricas — devolver al template

Nada de esto tiene que ver con pellet, con cintas ni con esta planta. Son correcciones y
puntos de extensión que cualquier fork necesita, y **conviene que vuelvan al template** en
vez de quedarse acá divergiendo. Ver «Cómo devolver una mejora al template» en el
`README.md`.

Una mejora genérica que nace acá se anota en esta tabla hasta que vuelva. Las primeras son
del build compilado en la Jetson, con el detalle en `.claude/plans/jetson-compilation.md`;
las que siguen salieron de la revisión del template y del proyecto del 2026-10-08.

| Archivo | Qué se cambió | Por qué es genérico |
|---|---|---|
| `build/build.py` | En Linux el build es acelerado (`_ACCELERATED`): compila `system`, `tools` y `ui` con `no_site` y `--file-reference-choice=runtime`; lo de terceros —`cryptography` incluida— va suelto | En cualquier Jetson el standalone de torch no termina con 8 GB, y CUDA, TensorRT y Python salen del JetPack igual |
| `build/make_release.py` | El entregable acelerado: `program/site-packages` copiado del venv sin lo de build, symlinks de apt como symlinks, lanzadores con `PYTHONPATH`, verificador de cámara que busca los paquetes donde viajan | Es la otra mitad del build acelerado |
| `build/requirements.txt`, `build/README.md`, `build/.gitignore` | Nuitka 4.2.2 declarado; la forma de Linux documentada | Ídem |
| `system/paths.py` | `APP_DIR` acelerado desde `__compiled__.containing_dir`, no desde `sys.executable` | Acelerado, `sys.executable` es el intérprete del equipo de build |
| `system/license/verify.py` | Autocontrol del verificador con el vector 1 de la RFC 8032 antes de cada verificación | Con `cryptography` suelta, vaciar su `verify()` es el ataque de un rato en cualquier fork |
| `system/system_monitor.py` | Potencia del INA3221 desde `in1_input × curr1_input` | JetPack 6 no publica `power1_input`: toda Jetson con JetPack 6 reporta 0 W |
| `main.py` (fuera del bloque del fork) | El handler de SIGINT/SIGTERM agenda el cierre con `QTimer.singleShot(0, app.quit)` en vez de cerrar (`_build_interrupt_handler()`) | Compilado, el handler corre con el lock del config tomado y Qt 6 emite `aboutToQuit` en el acto: el cierre se trababa y abortaba en cualquier fork |
| `tools/camera/abstract_driver.py`, `tools/camera/st_driver.py`, `system/camera/capture_thread.py` | `interrupt()` en el contrato del driver; `StDriver` despierta con él `connect()` y los reintentos; `CaptureThread.requestInterruption()` lo llama | Sin cámara, un cierre durante `connect()` dejaba el hilo vivo hasta 20 s y Qt abortaba, también desde fuentes |
| `system/inference/engine.py`, `system/inference/models/abstract_model.py` | Docstrings: la lógica por cámara va al pipeline, que recibe `camera_slot` en `_run()`; el `classifier` queda para lo que depende de la posición en el frame | Contradecían el contrato del pipeline desde que `_run()` recibe la cámara |
| `system/inference/engine.py`, `tools/camera/abstract_driver.py` | `push_frame()` guarda el frame sin copiarlo, y el contrato de `get_frame()` declara que el array es propio y que nadie lo modifica in-place | La copia suponía un driver que reusa su buffer, y ninguno lo hace: ~140 MB/s tirados a 3 MP y 15 fps |
| `system/telemetry/persistence.py` | El comentario de `_PUBLISH_INTERVAL_S` ya no dice que la cola topea el ritmo en 10 puntos/s | El hilo drena la cola de continuo |
| `system/modbus/server.py` | `update_block()` escribe el bloque entero bajo un solo lock, y la FC03 del PLC toma ese mismo lock | Una lectura en medio del bloque veía medio reloj: un salto de 18 h cada vez que la palabra baja da la vuelta |
| `requirements.txt`, `setup/venv-setup/verify_env.py` | `pymodbus==3.15.0`, y `verify_env.py` falla si la lectura del PLC deja de pasar por `async_OLD_getValues` | El lock de lectura cuelga de ese método interno de pymodbus |
| `system/gpio_control.py` | El polling espera el intervalo entero, en tramos; una entrada que falla se avisa al empezar y al recuperarse | Con más de 200 ms se leía igual a 5 Hz, y una línea muerta llenaba el log |
| `tools/camera/abstract_driver.py`, `mock_driver.py`, `rtsp_driver.py`, `st_driver.py`, `system/camera/capture_thread.py` | Reloj monotónico para intervalos, plazos y fps; el fps medido vuelve a 0 cuando dejan de llegar frames | Un ajuste de NTP al arrancar movía todo, y una cámara trabada seguía informando sus fps |
| `system/telemetry/backends/influxdb_backend.py`, `abstract_backend.py`, `system/telemetry/persistence.py` | Ante un corte, hasta 6000 puntos en memoria y reintento cada 30 s con timeout de 5 s; un 4xx no se reintenta | Un batch fallido se perdía y cada intento bloqueaba el hilo de telemetría 10 s |
| `system/inference/models/abstract_model.py` | Propiedad `class_names`: los nombres de los pesos, o los del config | Para que un pipeline confronte las clases que espera con las del modelo |
| `main.py` (fuera del bloque del fork) | `frame_ready` conectado directo: en el hilo de captura el frame va a los motores sin ciclo y, sólo con clientes, al video crudo; la GUI recibe el último de cada cámara por un buzón con un aviso en vuelo como mucho | Cada frame pasaba por la cola de la GUI sin tope: una GUI ocupada juntaba frames de 9 MB y atrasaba la inferencia |
| `main.py` (fuera del bloque del fork) | Sin recolección prendida no se arma el JSON ni el contexto del dataset; a la telemetría entra el resultado sin frames ni detecciones | Por resultado y en la GUI: `to_dict()` sobre cientos de detecciones, y dos frames de 9 MB retenidos hasta el tick |
| `system/image_collector/collector.py` | El job guarda referencias a los frames, no copias | Con el contrato del driver nadie los modifica, y copiar eran 18 MB por medición |
| `system/config_manager.py`, `system/inference/engine.py` | `ConfigManager.revision`, y el motor relee su config sólo cuando cambia | Unos veinte `get` por frame, cada uno con el lock global y una copia |
| `system/video/http_server.py` | Un anotado sin clientes se guarda sin codificar y se codifica cuando alguien lo pide; el último sobrevive al último cliente | Con la pantalla en anotado era un resize y un JPEG por frame para nadie |
| `ui/widgets/camera_panel.py` | No pinta oculto, y achica el frame con OpenCV antes de pasarlo a Qt | Convertía los 3 MP enteros a pixmap por frame en la GUI |
| `ui/widgets/log_table.py` | Una fila por evento en vez de reconstruir la tabla | 200 × 4 items por línea de log, en ráfaga |
| `config.yaml` (comentarios que vienen del template) | El comentario de `ui.language` dice que toma efecto al reiniciar, y se sacó `draw_summary`, que nadie lee | Los dos errores están en el `config.yaml` del template |
| `ui/views/config/system_tab.py`, `ui/strings.py`, comentario de `system.device_id` en `config.yaml` | `system.device_id` de sólo lectura, con una nota de dónde se cambia y qué rompe | Es el `client_id` de MQTT y el tag `device` de InfluxDB: editarlo en planta echa equipos del broker y parte las series |
| `system/image_collector/collector.py`, `ui/views/config/collector_tab.py`, `ui/strings.py`, `main.py` (fuera del bloque del fork), `manual_test/ui/config.yaml`, `image_collector.cameras` en `config.yaml` | Recolección por cámara: `image_collector.cameras` (`[]` = todas), filtrada adentro del recolector en los dos modos y expuesta como `should_collect()`, que el cableado usa en vez de `is_active`; un checkbox por cámara en la pestaña | Un fork con varias cámaras no tenía cómo guardar sólo algunas,, la lista vacía deja igual a un config existente |
| `system/camera/capture_scheduler.py`, `ui/views/config/collector_tab.py`, `ui/strings.py`, `main.py` (fuera del bloque del fork) | `has_measurement_cycle()` como regla única de «tener ciclo», que usan el scheduler y la pestaña; «A pedido» se ofrece sólo con ciclo, un `on_demand` sin ciclo se conserva con un aviso, y el arranque lo avisa como warning | Con `frames_per_cycle: 1` el modo a pedido no guarda nunca, y la pestaña lo ofrecía igual |
| `ui/views/config/inference_tab.py`, `ui/strings.py`, `docs/ui.md`, comentario de `inference.overlay` en `config.yaml` | Sale el grupo «Anotado del resultado»: `inference.overlay` se edita sólo en el archivo, como `class_colors_bgr` | Varios valores son de la instalación —el `font_scale` se calibra para el frame de cada cámara— y en constantes de `overlay.py` quedarían en un archivo que se cross-portea |

Con los tests de cada uno: `test/test_paths.py`, `test/license/test_verify.py`,
`test/test_system_monitor.py`, `test/test_main.py`, `test/camera/test_capture_thread.py`,
`test/tools/camera/test_st_driver.py`, `test/build/`, `test/inference/test_engine.py`,
`test/modbus/test_server.py`, `test/test_gpio_control.py`,
`test/tools/camera/test_mock_driver.py`, `test/telemetry/backends/test_influxdb_backend.py`,
`test/inference/models/test_abstract_model.py`, `test/test_config_manager.py`,
`test/video/test_http_server.py`, `test/ui/test_camera_panel.py`,
`test/ui/test_log_table.py`, `test/ui/test_system_tab.py`,
`test/image_collector/test_collector.py`, `test/ui/test_collector_tab.py`,
`test/camera/test_capture_scheduler.py` y `test/ui/test_inference_tab.py`.

**En un merge:** si el template ya trae una de estas, quedarse con la del template y borrar
la de acá. Si no la trae, conservarla y abrir el PR.

### Lo que ya volvió

Entró al template y ahora es maquinaria: el arreglo de conexión lenta de `st_driver.py`, el
relleno de máscaras por unión y el `canvas_adjust` de `overlay.py` / `engine.py`, los
nombres de stream de los servidores de video, los parámetros del diálogo de ROI, el
subsistema de GPIO —módulos y cableado en `main.py`—, `rolling.py` (con `has_material()`
renombrada `has_composition()`), el `camera_slot` en `_run()` del pipeline y
`to_plc_address()` en las pantallas de Modbus. Se trajeron con el merge del template del
2026-10-06 y desde ahí son idénticos a los del template.

Con el [PR #35](https://github.com/alejomancinelli/cv_projects_template/pull/35), traído
en el merge del 2026-10-09, volvió además todo lo de la Jetson: `setup/jetson/`, el venv
aislado, `verify_env.py` —salvo su bloque del fork—, el build y el entregable de Linux,
el borrado de las claves del build, `probe_codec()` y el `nvvidconv` de los codecs `_hw`,
el filtro de registros de `ui_app.py`, el timestamp del overlay y el STC-MCS312POE del
catálogo.

---

## C. Límites mal puestos — arreglar, no conservar

Acá está lo que se metió donde no iba. Se lista con el arreglo, no con una justificación.
Los dos primeros ya están arreglados y quedan asentados porque el arreglo es el patrón a
aplicar la próxima vez.

### ~~C1 y C2~~ — resueltos

`ui/main_window.py` armaba la clave `process.belt_roi_px.<slot>` y `ui/views/config_view.py`
tenía una señal llamada `open_belt_roi_requested`: dos archivos de maquinaria que sabían que
esta instalación mide una cinta.

Se resolvieron juntos, porque eran el mismo agujero: **faltaba que el pedido llevara el
dato**. Ahora la señal es `open_roi_requested(camera_slot, config_prefix, title_key)` y la
manda la pestaña, que es la dueña de su sección del config —`cameras_tab` manda
`cameras.<slot>.roi`, `process_tab` manda `process.belt_roi_px.<slot>`—. `ConfigView` la
reenvía sin leerla y `MainWindow` abre el diálogo con lo que le dan.

`ConfigView` engancha a **cualquier** pestaña que declare esa señal, sin nombrar ninguna,
así que una pestaña nueva con otro rectángulo se conecta sola. Y recuerda cuál pidió para
recargar sólo esa al cerrarse el diálogo: recargarlas todas descartaría lo que el operador
esté editando en otra.

Queda como referencia de la forma que tiene el arreglo cuando algo del proyecto se filtra
en maquinaria: **el dato viaja con el pedido**, no lo deduce quien lo recibe.

### ~~C3~~ — resuelto, ensanchando el contrato del pipeline

El umbral de fondo oscuro colgaba del modelo, que es uno solo para todas las cámaras del
pipeline: dos cámaras con distinta iluminación no podían tener umbrales distintos. Con una
sola cámara no se notaba, que es lo que lo hacía peligroso.

Se resolvió donde estaba el problema y no alrededor: **`AbstractPipeline._run()` pasa a
recibir `(frame_bgr, camera_slot)`**. Es un ensanche —ninguna implementación existente
cambia de comportamiento— y con eso el refinamiento se mudó a `BeltPipeline`, que es una
etapa posterior a la segmentación, corre antes del analyzer y del overlay, y ahora sabe de
qué cámara viene el frame. El umbral volvió a `process.dark_background_threshold`, por
cámara y por clase.

Es un cambio de maquinaria y **ya volvió al template** (sección B): `engine.py` le pasa el slot,
`abstract_pipeline.py` lo declara, y al modelo se le sigue sin pasar a propósito. Lo que
queda para el `classifier` es lo que necesite coordenadas del frame completo, porque corre
después de `offset_detections`.

---

## Dónde más hay divergencia, sin ser ninguna de las tres

`main.py` es el composition root y el `README.md` dice que del fork sólo se toca el bloque
marcado. Acá se tocó además:

- El bloque de telemetría entero (A1).
- Las medias móviles: `_update_rolling()`, `_build_rolling_registers()`, `_publish_rolling()`
  y `_inference_age_s()`. Viven acá y no en el analyzer porque hay que envejecer la ventana
  en cada tick, haya medición o no, y el analyzer sólo corre cuando hay resultado.

Todo eso está junto y marcado en el archivo, que es lo que hace que un diff contra el
template las muestre de una.
