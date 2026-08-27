# Plantilla de proyectos de visión artificial industrial

Captura de una o varias cámaras GigE, inferencia, y publicación del resultado a PLC por
Modbus, a dashboards por telemetría, a video por HTTP/RTSP y a un dataset en disco.

**Cada instalación es un fork de este repo.** Lo que cambia entre forks son los valores
del `config.yaml`, el mapa de registros Modbus, el modelo y la cuenta del proceso. La
plomería —captura, hilos, servidores, dataset, overlay— se cross-portea sin editarla.

Este archivo es la guía para arrancar un fork. El mapa del repo, los contratos entre
módulos y las decisiones vigentes están en [CLAUDE.md](CLAUDE.md); las convenciones de
nombres, comentarios y límites, en `.claude/skills/`.

## Cómo se corre

El intérprete del proyecto es el del venv, **no** el `python` del PATH:

    .venv\Scripts\python.exe -m pytest -q          # Windows
    .venv/bin/python -m pytest -q                  # Linux

Python 3.10 de 64 bits. La suite completa corre sin hardware, sin GUI y sin red. Lo que
sí necesita hardware vive en `manual_test/<tema>/`, cada uno con su `config.yaml` al lado.

## Cómo se arranca un fork

1. **Fork del repo** y `pip install -r requirements.txt` en un venv nuevo. Los SDK de
   cámara se instalan con los scripts de `setup/cameras/{windows,linux}/`.
2. **`config.yaml`**: `project`, `system`, y una entrada en `cameras` por cámara física
   —marca, modelo, driver, IP, adquisición, ROI, mínimo de iluminación—. Verificar con
   `manual_test/cameras/camera_live_view.py`.
3. **El modelo**: implementar `AbstractModel` en un archivo nuevo de `system/inference/` y
   registrarlo con una línea en `model_factory.py`. Una clase por tarea —clasificación,
   detección, segmentación—, porque lo que cambia entre ellas es cómo se decodifica la
   salida. Los pesos afinados no son una clase nueva: son `path` en el config.
4. **`inference.models`** en el config: un slot por modelo, con su `type`, `path`,
   umbral y nombres de clase. El dispositivo no se configura: la implementación usa la
   GPU si hay CUDA y cae a CPU si no.
5. **`pipeline.py`**: declarar `model_slots` y escribir `_run()` con el orden de las
   etapas y sus cortocircuitos. Una sola etapa es el caso normal y ya viene escrito.
6. **`process:`** en el config: los parámetros de lo que se mide en esta planta —escala de
   píxel, umbrales, límites—, y `inference.pipelines` con qué cámaras entran a cada
   pipeline y cuántas imágenes son una medición (`frames_per_cycle`).
7. **`metrics.py`**: la cuenta del proceso, de las detecciones a los números que el
   cliente mide. Es lo que termina en el panel del anotado, en el JSON del dataset, en la
   telemetría y en los registros.
8. **`register_map.yaml`**: las filas `producer: inference` en el rango 3-50, y después
   `python -m system.modbus.export_map` para regenerar la tabla del integrador.
9. **`main.py`**: cablear los subsistemas por señales, leer la sección `process` y pasarle
   al motor el pipeline, el analyzer y —si hacen falta— el preprocessor que corrige el
   lente y los annotators de la planta.
10. **La vista de operador**: escribir el widget del área central del monitor y
    pasárselo con `window.monitor_view.set_content(...)`. Para el caso normal ya está
    `ui/widgets/camera_grid.py` y no hay nada que escribir. Los textos nuevos van a
    `ui/strings.py`, con su clave en inglés y una columna por idioma; el resto de la UI
    —ocho pestañas de configuración, cuatro de diagnóstico, temas y widgets— viene
    andando. Ver [docs/ui.md](docs/ui.md).
11. **`CLAUDE.md`**: actualizar el mapa y las decisiones con lo que el fork agregó.

## Qué se toca y qué no

Tres categorías, y la regla que las separa: **si un archivo describe cómo se hace algo,
es maquinaria y se cross-portea; si describe qué se mide en esta planta, es del fork.**

### Se reescriben en cada fork

| Archivo | Qué se escribe |
|---|---|
| `config.yaml` | todos los valores, y la sección `process:` completa |
| `system/modbus/register_map.yaml` | las filas de inferencia; el resto del mapa es plantilla |
| `system/inference/pipeline.py` | el orden de las etapas y sus cortocircuitos |
| `system/inference/metrics.py` | la cuenta del proceso |
| `system/version.py` | la versión del fork; se sube en cada release |
| `CLAUDE.md` | el mapa y las decisiones del fork |
| `README.md` | esta guía, reemplazada por la del proyecto |

### Se agregan, sin editar lo que ya está

| Archivo | Cuándo |
|---|---|
| `main.py` | siempre: es el cableado, y todavía no existe en el template |
| `system/inference/<mi_modelo>.py` | el modelo del proyecto, más **una línea** en `model_factory.py` |
| `system/inference/annotations.py` | referencias de la planta: un límite de carga, una zona |
| `tools/camera/<mi_driver>.py` | una cámara de otro fabricante, más **una línea** en `camera_factory.py` |
| `tools/camera/camera_catalog.py` | un modelo de cámara que falte en el catálogo |
| el widget central del monitor | qué mira el operador; entra por `set_content()`. Una disposición propia —tres cámaras por pantalla, por ejemplo— son varias `CameraGrid(cfg, camera_slots=...)` dentro de ese widget |
| `ui/views/config/process_tab.py` | los campos de `process:`; la pestaña ya está registrada y vacía |
| `ui/views/config/<otra>_tab.py` | otra sección propia, más **una línea** en `_TAB_CLASSES` |
| `test/` | un test por archivo nuevo, espejando el árbol |
| `manual_test/<tema>/` | verificaciones con hardware, con su `config.yaml` al lado |
| `docs/` | notas de puesta en marcha, protocolos nuevos |

### Maquinaria: no se edita

Si hay que editar alguno de estos para que el fork funcione, el límite está mal puesto y
lo que falta es un punto de extensión, no un parche.

| Carpeta | Archivos |
|---|---|
| infraestructura | `system/config_manager.py`, `logger.py`, `paths.py`, `system_monitor.py` |
| captura | `system/camera/capture_thread.py`, `tools/camera/abstract_driver.py`, `camera_factory.py`, `basler_driver.py`, `st_driver.py`, `mock_driver.py`, `null_driver.py` |
| imagen | `tools/image/enhance.py`, `undistort.py` |
| inferencia | `system/inference/abstract_model.py`, `abstract_pipeline.py`, `model_factory.py`, `mock_model.py`, `null_model.py`, `result.py`, `overlay.py`, `analysis.py`, `engine.py` |
| bitfields | `system/formats/camera_health.py`, `com_status.py`, `system_status.py` |
| Modbus | `system/modbus/schema.py`, `registers.py`, `server.py`, `export_map.py` |
| telemetría | `system/telemetry/persistence.py`, `backends/*` |
| video | `system/video/abstract_video_server.py`, `http_server.py`, `rtsp_server.py` |
| dataset | `system/image_collector/collector.py`, `conditions.py` |
| interfaz | `ui/` entero salvo lo de arriba: `strings.py`, `theme.py`, `service_status.py`, `main_window.py`, las tres vistas, `views/config/*`, `views/diagnostics/*`, `widgets/*`, `dialogs/*`, `styles/*` |

Dos excepciones acotadas: `model_factory.py` y `camera_factory.py` reciben **una línea**
cada uno cuando el fork agrega una implementación. Eso es registro, no lógica.

### Generados: nunca a mano

`docs/modbus_map.md` y `docs/modbus_map.csv` salen de
`python -m system.modbus.export_map`. La fuente única es `register_map.yaml`.

## La interfaz

`ui/` viene armada y andando: tres vistas —monitor, configuración, diagnóstico—, ocho
pestañas que cubren todas las secciones genéricas del `config.yaml`, cuatro de
diagnóstico, dos temas y los widgets reutilizables. Los tres huecos son a propósito: el
área central del monitor la llena el fork, la pestaña de proceso llega vacía porque sus
campos cambian en cada instalación, y el diálogo de GPIO espera un
`system/gpio_control.py` que todavía no existe.

Cómo se toca cada atributo —dónde vive un color, un texto, una medida; cómo se agrega
una pestaña o un widget; qué cosas la UI **no** hace— está en [docs/ui.md](docs/ui.md).

## Los cinco puntos de extensión del motor de inferencia

Un fork no edita `engine.py`: le pasa funciones por el constructor. Es lo que mantiene
genérica la maquinaria y testeable lo del proyecto.

| Punto | Qué hace | Dónde vive |
|---|---|---|
| `preprocessor` | sobre qué imagen se mide | `tools/image/undistort.py`, o del fork |
| `pipeline` | qué modelos corren y en qué orden | `pipeline.py` |
| `analyzer` | qué significan las detecciones | `metrics.py` |
| `annotator` | qué se dibuja además del resultado | `annotations.py` |
| `annotate_gate` | si alguien está mirando el stream anotado | `main.py`, del servidor de video |

## Los tres caminos que salen de un frame

No son el mismo frame, y confundirlos es el error caro:

| Camino | Qué imagen | Quién la prepara |
|---|---|---|
| medición | la del `preprocessor`: corregida si el lente lo pide | `tools/image/undistort.py` |
| dataset | la cruda de la cámara, para poder reentrenar | nadie la toca |
| visualización | ajustada para que se vea (gamma, contraste local) | `tools/image/enhance.py`, con `video.display` |

La corrección geométrica cambia **dónde está** un píxel, así que tiene que ser la misma
para el modelo y para el overlay: por eso va en el `preprocessor`, que define el frame de
referencia. El brillo cambia **cómo se ve** y no mueve nada: por eso va sólo en el camino
de visualización y no contamina el dataset.

## Cuando un ciclo son varias imágenes

Un proyecto que responde con N imágenes por medición declara N en
`inference.pipelines.<slot>.frames_per_cycle`. El motor no lo lee —emite un resultado por
frame y no promedia—: lo lee quien orquesta el ciclo, que junta esos N resultados y usa

- `analysis.summarize_metrics(results)` → media, desvío, mínimo y máximo por clave, más
  `sample_count`, todo plano y listo para la telemetría y los registros;
- `analysis.pick_representative(results)` → cuál de los N frames se guarda y se muestra,
  el más cercano al promedio medido en desvíos.
