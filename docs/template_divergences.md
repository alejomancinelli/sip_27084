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

El mecanismo que la lee (`_read_stream_names()`) **no** es deuda: es genérico y está en la
sección B.

### A3. Mapa de registros Modbus

**Dónde:** `system/modbus/register_map.yaml`.

Las direcciones, las escalas y la semántica son las que el PLC ya tiene programadas, no las
del template. El template numera su bloque de salud distinto (`cpu_usage_pct` en 82) y
escala temperaturas y disco ×10; acá van en las direcciones viejas y sin escala.

**Cómo revertir:** no se revierte. Un mapa de registros es un contrato con el integrador y
se cambia cuando el integrador cambia su programa, no cuando se actualiza el template. Lo
único que se tomó del template es el bitfield del registro 51 y los registros 97-99.

---

## B. Mejoras genéricas — devolver al template

Nada de esto tiene que ver con pellet, con cintas ni con esta planta. Son correcciones y
puntos de extensión que cualquier fork necesita, y **conviene que vuelvan al template** en
vez de quedarse acá divergiendo. Ver «Cómo devolver una mejora al template» en el
`README.md`.

| Archivo | Qué se cambió | Por qué es genérico |
|---|---|---|
| `tools/camera/st_driver.py` | `connect()` deja vivo el hilo de captura cuando se le acaba la espera, y la espera corta si alguien desconecta | Una GigE que tarda más que el timeout **no conectaba nunca**: cada reintento re-enumeraba desde cero con el mismo presupuesto. Es un bug, no una preferencia |
| `system/inference/overlay.py` | El relleno de las máscaras se pinta por unión de la clase y no una vez por instancia | Con instancias superpuestas el color se saturaba de más y dejaba de coincidir con lo que mide `metrics`, que cuenta cada píxel una vez |
| `system/inference/overlay.py` + `engine.py` | `annotate()` acepta `canvas_adjust`, y el motor lo recibe por constructor | El anotado es lo único que mira una persona y que se dibuja en el motor: sin esto sale sin el ajuste de visualización que sí tiene el stream crudo |
| `system/video/abstract_video_server.py`, `http_server.py`, `rtsp_server.py` | `_read_stream_names()`, `CONFIG_KEY`, y `get_stream_urls()` en el servidor HTTP | Renombrar la ruta es una necesidad de cualquier instalación con URLs ya publicadas. Y las URLs del HTTP las armaba `main.py`, con el formato en dos lugares —su propio docstring lo admitía— |
| `ui/dialogs/roi_dialog.py` | `config_prefix` y `title_key` como parámetros | Un proyecto puede tener más de un rectángulo por cámara; el diálogo es el mismo |
| `system/gpio_control.py`, `system/formats/gpio_status.py` | Módulos nuevos | El template declaraba el subsistema como pendiente y `ui/dialogs/gpio_dialog.py` ya documentaba la interfaz que esperaba |
| `system/inference/rolling.py` | Módulo nuevo | Media móvil por ventana de tiempo, con su cobertura. No hay nada de esta planta adentro |
| `build/build.py` | `--include-package=gpiod` en `_EXTRA_FLAGS` | Va en el bloque del fork, que es su punto de extensión. Deja de hacer falta si el template incorpora el subsistema de GPIO |

**En un merge:** si el template ya trae una de estas, quedarse con la del template y borrar
la de acá. Si no la trae, conservarla y abrir el PR.

---

## C. Límites mal puestos — arreglar, no conservar

Acá está lo que se metió donde no iba. Se lista con el arreglo, no con una justificación.

### C1. `ui/main_window.py` conoce una clave de `process:`

```python
config_prefix=f"process.belt_roi_px.{camera_slot}",
title_key="process_belt_title",
```

`main_window.py` es maquinaria y ahora sabe que esta instalación tiene un rectángulo de
cinta guardado en esa clave. Un fork con otro segundo rectángulo tendría que editar el
mismo archivo, que es exactamente la señal de que falta un punto de extensión.

**Arreglo:** que la señal lleve el dato en vez de que lo sepa quien la recibe.
`ProcessTab` emite `(camera_slot, config_prefix, title_key)`, `ConfigView` la reenvía tal
cual y `MainWindow` abre el diálogo con lo que le dan. Ningún archivo de maquinaria vuelve
a nombrar `belt_roi_px`.

### C2. `ui/views/config_view.py` tiene una señal con nombre de esta planta

`open_belt_roi_requested` y `refresh_belt_roi_fields()` nombran un concepto del proyecto
dentro de maquinaria. Se resuelve con el mismo arreglo de C1: una sola señal genérica de
«dibujar un rectángulo», sin adjetivo.

### C3. El umbral de fondo oscuro es por modelo y debería ser por cámara

**Dónde:** `inference.models.<slot>.params.dark_background_threshold`, aplicado en
`system/inference/models/yolo_seg_model.py`.

El umbral depende de la **iluminación**, que es de cada cámara y de su montaje. Está en el
modelo porque ahí estaba el frame antes de que dibujara el overlay, y el modelo es **uno por
pipeline y compartido por todas sus cámaras**: dos cámaras con luz distinta no pueden tener
umbrales distintos hoy. Con una sola cámara no se nota, y por eso pasó.

**Arreglo:** mudarlo al `classifier` del motor, que corre antes del analyzer y del overlay
—así la máscara que se mide y la que se dibuja siguen siendo la misma— y que **sí recibe el
slot de la cámara**. Para eso el `classifier` tiene que recibir también el frame de
referencia: hoy su firma es `(detections, camera_slot)`. Es un cambio de contrato de
maquinaria, y de los tres de esta sección es el único que no es sólo mover código.

---

## Dónde más hay divergencia, sin ser ninguna de las tres

`main.py` es el composition root y el `README.md` dice que del fork sólo se toca el bloque
marcado. Acá se tocó además:

- El bloque de telemetría entero (A1).
- El cableado del subsistema de GPIO: construcción, arranque, parada y las dos palabras a
  los registros. Deja de ser divergencia el día que el template traiga el subsistema (B).
- Las medias móviles: `_update_rolling()`, `_build_rolling_registers()`, `_publish_rolling()`
  y `_inference_age_s()`. Viven acá y no en el analyzer porque hay que envejecer la ventana
  en cada tick, haya medición o no, y el analyzer sólo corre cuando hay resultado.

Todo eso está junto y marcado en el archivo, que es lo que hace que un diff contra el
template las muestre de una.
