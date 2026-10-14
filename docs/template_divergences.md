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
que acá ocupan las tres últimas direcciones expuestas, 98-100: las mismas que el template
desde el PR #37.

---

## B. Mejoras genéricas — devolver al template

Nada de esto tiene que ver con pellet, con cintas ni con esta planta. Son correcciones y
puntos de extensión que cualquier fork necesita, y **conviene que vuelvan al template** en
vez de quedarse acá divergiendo. Ver «Cómo devolver una mejora al template» en el
`README.md`.

Una mejora genérica que nace acá se anota en esta tabla hasta que vuelva.

### En el PR #41

Salieron del merge del template del 2026-10-14 y están en el
[PR #41](https://github.com/alejomancinelli/cv_projects_template/pull/41), abierto, junto
con el arreglo de C4 y el de los tests de Sentech —que acá siguen dando 18 errores en
Windows hasta traerlo: sin stapipy en el venv, sus fixtures no encuentran `st_driver.st`—.
Las dos de la tabla son de cableado y no tocan la maquinaria de `system/`.

| Archivo | Qué se cambió | Por qué es genérico |
|---|---|---|
| `main.py` (fuera del bloque del fork) | El motor recibe `canvas_adjust=self._for_display`: el anotado se ve con el mismo ajuste de `video.display` que el stream crudo | El motor del template ya acepta `canvas_adjust`, pero su `main.py` no lo pasa, así que con un gamma de visualización el anotado se ve más oscuro que el crudo |
| `manual_test/ui/ui_app.py` | Arma los hooks con `build_hooks(config)` y el pipeline con `Pipeline(config, slot)`, como `main.py` y `offline.py` | El del template le pasa `compute_metrics` a mano: la demo de la UI mide con otro analyzer y sin los annotators del fork |

Sin tests propios: la primera es una línea de cableado y la segunda una prueba manual.

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

Con el [PR #36](https://github.com/alejomancinelli/cv_projects_template/pull/36), mergeado
el 2026-10-09, volvió lo del build compilado en la Jetson, con el detalle en
`.claude/plans/jetson-compilation.md`: el build acelerado de Linux (`build/build.py`,
`build/make_release.py`, `build/requirements.txt`, `build/README.md`, `build/.gitignore`),
el `APP_DIR` acelerado de `system/paths.py`, el autocontrol de `system/license/verify.py`,
la potencia del INA3221 en `system/system_monitor.py`, el handler de señales que agenda el
cierre en `main.py` e `interrupt()` en el contrato del driver, en `StDriver` y en
`CaptureThread`. Se trajo con el merge del 2026-10-14, junto con el arreglo de
`test/test_paths.py` para comparar rutas absolutas en Windows.

Con los PR [#37](https://github.com/alejomancinelli/cv_projects_template/pull/37) y
[#38](https://github.com/alejomancinelli/cv_projects_template/pull/38), traídos en el mismo
merge, volvió lo de la revisión de la GUI del 2026-10-14 y lo de la revisión del costo por
frame del 2026-10-08: `system.device_id` de sólo lectura, la recolección por cámara
(`image_collector.cameras` y `should_collect()`), `has_measurement_cycle()` y el «A pedido»
que sólo se ofrece con ciclo, la sección `inference.overlay` y los `class_names` fuera de
la UI, `build_slot_tabs()` y `build_form_page()` con el ancho común de tarjeta, el frame que
no se copia en `push_frame()` ni en el recolector, el bloque Modbus escrito bajo un solo
lock con `pymodbus==3.15.0`, el polling de GPIO por tramos, el reloj monotónico de la
captura, el buffer de InfluxDB ante un corte, `class_names` en el contrato del modelo, el
`frame_ready` directo con el buzón de la GUI, el dataset que no se arma con la recolección
apagada, `ConfigManager.revision`, el anotado HTTP codificado a pedido, el `CameraPanel`
que no pinta oculto y la `LogTable` de una fila por evento. Desde ahí son idénticos a los
del template, salvo `main.py`, que diverge por A1 y por las medias de la hora.

En el mismo merge entraron dos PR que no nacieron acá: el
[#39](https://github.com/alejomancinelli/cv_projects_template/pull/39), con el lint y la
suite locales (`ci/check.py` y los hooks de `.githooks/`), y el
[#40](https://github.com/alejomancinelli/cv_projects_template/pull/40), con el driver
`video_file`, la corrida de imágenes sueltas y los hooks del proyecto fuera de `main.py`.
Por ese último, el analyzer y los annotators de la cinta se mudaron de `main.py` a
`system/inference/hooks.py`, y `BeltPipeline` pasó a llamarse `Pipeline` y a leer el umbral
de fondo oscuro del config, que es como lo arma `offline.py`.

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

### C4. La prueba de la corrida offline nombra cosas del template

**Dónde:** `test/inference/test_offline.py` y `manual_test/offline_inference/config.yaml`,
que llegaron con el PR #40.

`offline.py` es maquinaria, pero su test y el config de su prueba manual declaran el modelo
en el slot `model_1` —el de `Pipeline.model_slots` del template— y prueban los hooks del
template —`max_load_y_px`, `detection_count`—. Con el pipeline de la cinta, que pide el slot
`segmenter`, el modelo no carga y seis tests fallan. Acá se adaptaron al slot y a los hooks
de la cinta, así que en el próximo merge van a chocar.

**El arreglo** es del template y está en el
[PR #41](https://github.com/alejomancinelli/cv_projects_template/pull/41): el test arma los
slots desde `Pipeline.model_slots` y prueba los hooks con espías en lugar de suponer qué
métricas salen, y el `README.md` del template suma el config de
`manual_test/offline_inference/` y `test_hooks.py` a lo que el fork reescribe. Cuando se
traiga, quedarse con el `test_offline.py` del template —pasa sin editar contra esta cinta—
y conservar el config de la prueba manual de acá.

## Dónde más hay divergencia, sin ser ninguna de las tres

`main.py` es el composition root y el `README.md` dice que del fork sólo se toca el bloque
marcado. Acá se tocó además:

- El bloque de telemetría entero (A1).
- Las medias móviles: `_update_rolling()`, `_build_rolling_registers()`, `_publish_rolling()`
  y `_inference_age_s()`. Viven acá y no en el analyzer porque hay que envejecer la ventana
  en cada tick, haya medición o no, y el analyzer sólo corre cuando hay resultado.

Todo eso está junto y marcado en el archivo, que es lo que hace que un diff contra el
template las muestre de una.
