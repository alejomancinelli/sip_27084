# Plan: probar el sistema con video grabado, imágenes sueltas y ground truth

**Estado: video e imágenes sueltas hechos; ground truth postergado.** Branch
`machinery/replay-and-ground-truth`, sobre `main`. Lo operativo de lo que ya existe está en
`docs/offline_testing.md`; este plan queda por el porqué y por lo que falta.

- **Pieza A (driver `video_file`): hecha.**
- **Pieza B (`--replay`): descartada.** Ver §4.
- **Pieza C (`InferenceThread.process()`): hecha.**
- **Pieza D: hecha con la opción (a).** `system/inference/preprocessing.py` y
  `system/inference/hooks.py`, con una sola `build_hooks(config)` que devuelve
  `EngineHooks` y lee `process:` una vez.
- **Pieza E: hecha, más chica que §7.** El runner es `system/inference/offline.py`
  (`OfflineRunner`) y no un paquete `system/offline/` con CLI: sin ground truth, el único
  comando era `run`, y quedó como prueba manual en `manual_test/offline_inference/`, que
  corre una carpeta de imágenes y escribe en `output/<fecha>_<hora>/`. Al retomar el ground
  truth, `evaluate` y `snapshot` se apoyan en el mismo runner.
- **Pieza F (ground truth): postergada.** `ci/check.py` ya está en `main`, así que el
  subcomando `ground-truth` se puede agregar cuando se retome.

Este documento está escrito para que otra instancia lo tome sin haber visto la
conversación que lo originó: el porqué de cada decisión está acá y no por referencia.

---

## 1. Qué se quiere

Tres capacidades de desarrollo, todas maquinaria del template:

1. **Correr el sistema entero con videos grabados** en una carpeta, en loop infinito, como
   si fueran cámaras en vivo: captura, inferencia, overlay, streams, Modbus, telemetría,
   UI. Cómo se graban los videos queda fuera del alcance.
2. **Correr los pipelines y los modelos sobre una imagen suelta**, sin levantar la app.
3. **Ground truth por fork**: imágenes con el resultado esperado, versionadas en el repo del
   fork, y un comando que dice cuáles pasan y cuáles no.

## 2. La decisión de fondo: el video se lee directo, no por un servidor RTSP

**Un driver nuevo, `video_file`, que lee el archivo con OpenCV.** El RTSP queda como
receta opcional en `manual_test/`, para otra cosa (ver §9).

| | Driver `video_file` | Servidor RTSP (MediaMTX + ffmpeg) |
|---|---|---|
| Dependencias | ninguna: OpenCV ya está | dos binarios externos por equipo |
| Arranque | `driver: video_file` en el config | levantar el servidor, publicar cada video, apuntar las cámaras |
| Loop | lo controla el driver | `-stream_loop -1` de ffmpeg; un corte del publicador deja la cámara caída |
| Testeable en la suite | sí, con un video de 10 frames en `tmp_path` | no sin red ni procesos externos |
| Qué ejercita | todo menos el transporte | además, el `RtspDriver`: reconexión, credenciales, latencia |

Lo que se quiere probar es **el sistema**, no el transporte: el pipeline, las métricas, el
overlay y la publicación se comportan igual venga el frame de donde venga, porque el
contrato del driver es el mismo. El RTSP agrega fallas que no son del proyecto —el
publicador que se cae, el puerto ocupado— justo en la herramienta que tiene que ser la más
confiable. Sirve para otra pregunta: si el `RtspDriver` aguanta cortes, y eso es una prueba
manual de ese driver, no el entorno de desarrollo.

## 3. Pieza A — el driver `video_file`

`tools/camera/video_file_driver.py`, +1 línea en `_DRIVER_CLASSES` y una entrada en
`CAMERA_CATALOG` (`replay`: «Video grabado», driver `video_file`, modelo `file`).

Contrato, sobre el de `AbstractCameraDriver`:

- **`address` es una ruta a un archivo o a una carpeta**, relativa a la raíz del repo y
  resuelta con `system.paths.resolve`, igual que `inference.models.<slot>.path`. Una
  carpeta se reproduce entera en orden alfabético (`.mp4 .avi .mkv .mov`) y vuelve a
  empezar: es una playlist en loop.
- **El ritmo lo pone el reloj, no el consumidor.** El fps es el del archivo
  (`CAP_PROP_FPS`), con `acquisition.fps_limit` como tope; sin fps en el contenedor, el
  tope o 15. El frame que corresponde sale del tiempo transcurrido desde que arrancó el
  archivo, y los del medio se saltean con `grab()` sin `retrieve()`. Así un consumidor
  lento pierde frames igual que con una cámara en vivo —que es la entrega always-fresh del
  contrato— y el video no se «estira» cuando la inferencia es lenta: un minuto de video
  dura un minuto.
- **Al final del archivo se reabre**, no se busca el frame 0: `CAP_PROP_POS_FRAMES` es
  impreciso en contenedores sin índice y en GOPs largos, y reabrir cuesta milisegundos una
  vez por vuelta. En una playlist, el final de uno abre el siguiente.
- **`is_synthetic = True`.** Los frames no son de la línea en este momento, y ese marcador
  es el que llega al bit de la palabra de estado de la cámara que lee el PLC. Un video
  grabado es imagen real pero no es una medición de ahora.
- `get_status()` con las claves estables; `connected` es «el archivo abrió»,
  `temperature` 0.0, y `error` cuando la ruta no existe o la carpeta no tiene videos.
- Deshabilitada (`capture_enabled` en False) suelta el archivo; al rehabilitar sigue por
  reloj, como una cámara que estuvo filmando mientras nadie miraba.
- Sin hilo propio: a diferencia de FFmpeg sobre RTSP, un archivo no acumula paquetes si
  nadie lee, así que `get_frame()` puede leer en el momento. Si medido resulta que la
  decodificación bloquea demasiado al hilo de captura, se copia el patrón de cola de dos
  de `RtspDriver`.

## 4. Pieza B — `main.py --replay`: descartada

La idea era un flag que cambiara el driver de cada cámara por la corrida, sin tocar el
`config.yaml`. No hace falta: el video se prueba en la máquina de desarrollo, antes de que
exista la configuración final del equipo, así que editar el config no pone en riesgo nada.
Cada cámara que se quiera reproducir declara `driver: video_file` y su `address` en el
config, y `CaptureThread` queda como está.

Los videos viven en `recordings/` de la raíz, con `.gitkeep` y el contenido ignorado por
git, igual que `models/`: pesan y no se versionan. Una carpeta por cámara
(`recordings/camera_1/`) cuando cada una tiene que ver su propia escena; si todas apuntan
a la misma carpeta, todas reproducen lo mismo, casi en sincronía.

## 5. Pieza C — el motor corre un frame de forma sincrónica

`InferenceThread._process()` ya hace el ciclo completo de un frame —preprocessor, ROI,
iluminación, pipeline, classifier, confianza, analyzer, overlay— pero además publica y
cuenta. Se separa:

- **`process(frame_bgr, camera_slot) -> InferenceResult`**, público: el ciclo completo, sin
  emitir ni tocar contadores. Levanta si el hilo está corriendo: el pipeline no es
  reentrante y comparte instancia.
- `run()` sigue llamando a eso y después a `_publish()`.

Sin `annotate_gate` el motor ya dibuja siempre, que es lo que se quiere offline. El
resultado es **exactamente** el que saldría en vivo: no hay un segundo camino de
inferencia que pueda divergir del primero.

## 6. Pieza D — los hooks del fork salen de `main.py`

El runner offline necesita el mismo preprocessor, classifier, analyzer y annotator que la
app. Hoy son métodos de `Application`, y `main.py` no lo importa nadie. Opciones:

- (a) **Moverlos a funciones que reciben el config** —recomendada—:
  - `system/inference/hooks.py` (del fork): `build_classifier(config)`,
    `build_analyzer(config)`, `build_annotator(config)`. Pasa a la lista de «se
    reescriben» y `main.py` los llama.
  - `system/inference/preprocessing.py` (maquinaria): `build_preprocessor(config)` y
    `chain_preprocessors()`, que hoy son `_build_preprocessor` y `_chain_preprocessors` de
    `main.py`.
  - En `main.py` quedan del fork sólo `_build_monitor_content` y `_build_dataset_context`,
    que son de la app y no de la medición.
- (b) Que el runner repita el cableado: dos lugares que arman el analyzer son dos lugares
  que pueden discrepar, y el ground truth pasaría con un analyzer que no es el que corre.
- (c) Que el runner levante `Application` en headless: abre el Modbus en el 502, los
  servidores de video y la telemetría para procesar una imagen.

Con (a) cambia una regla de CLAUDE.md («de `main.py` se completan tres métodos»), y los
forks existentes migran moviendo el cuerpo de tres métodos a `hooks.py`: mecánico, pero
toca a todos. **Es la decisión que hay que tomar antes de empezar** (§11).

## 7. Pieza E — `system/offline/`: la CLI

Más de un archivo, así que carpeta propia:

    system/offline/
      runner.py         arma los pipelines y los hooks desde el config; corre imágenes
      ground_truth.py   nivel 1 — formato del esperado y comparación; módulo puro
      __main__.py       CLI: run | evaluate | snapshot

**`OfflineRunner(config)`** instancia un `InferenceThread` por pipeline habilitado —sin
arrancarlo— con los hooks de la pieza D, carga los pipelines una vez, y
`run_image(frame, camera_slot, *, preprocessed=False)` devuelve un `InferenceResult` por
cada pipeline que recibe esa cámara (`inference.pipelines.<slot>.cameras`). La licencia no
se consulta: es una herramienta de fuentes, y desde fuentes el estado es
`unlicensed_build` igual.

Comandos (`python -m system.offline ...`, con el intérprete del venv):

    run <imagen|carpeta> --camera camera_1 [--pipeline pipeline_1] [--out dir]
        Corre el ciclo completo y deja el anotado y el to_dict() del resultado en --out
        (por defecto data/offline/<fecha>/). Para mirar qué hace el pipeline con una
        imagen mientras se desarrolla.

    run <imagen> --model model_1
        Sólo el predict() de ese modelo, sin pipeline, ROI ni hooks: para separar si el
        problema está en el modelo o en lo que el fork hace alrededor.

    evaluate [ground_truth/] [--out dir]
        Corre cada caso y lo compara con su esperado. Sale con código 1 si alguno falla.

    snapshot <imagen> --camera camera_1
        Escribe al lado de la imagen un esperado BORRADOR con lo que hoy devuelve el
        sistema (`reviewed: false`), para corregirlo a mano en vez de tipearlo de cero.

`--preprocessed` dice que la imagen ya es frame de referencia y que no se le vuelve a pasar
el preprocessor (ver trampas, §10). Las imágenes se leen con `cv2.imdecode` sobre los
bytes, no con `imread`, por las rutas con caracteres no ASCII en Windows —el mismo motivo
por el que el colector escribe con `imencode`—.

## 8. Pieza F — el ground truth

### Dónde vive

`ground_truth/` en la raíz, **versionado en el repo del fork**. El template trae sólo un
`README.md` corto con el formato y `.gitkeep`; el caso de ejemplo del template va en
`test/offline/data/`, para que un fork no tenga que borrar nada.

Las imágenes en PNG, sin pérdida —lo mismo que guarda el dataset—. Con un presupuesto de
unos 50 MB alcanza para varias decenas de frames de 1920×1200; más que eso pide Git LFS
para `ground_truth/**/*.png` (§11).

### Formato: un YAML al lado de cada imagen

    ground_truth/
      cinta_vacia.png
      cinta_vacia.yaml
      carga_alta/          # agrupar en carpetas es libre
        tarde_01.png
        tarde_01.yaml

```yaml
camera_slot: camera_1     # obligatorio: decide ROI, preprocessor, process: y pipelines
preprocessed: false       # true si la imagen ya es frame de referencia (sale del dataset)
reviewed: true            # false = borrador de `snapshot`: se reporta y no cuenta como pasado
note: cinta vacía, luz de tarde
pipelines:                # ausente = todos los que reciben esa cámara, sin expectativas
  pipeline_1:
    is_valid: true
    invalid_reason: ''    # si se espera un resultado no confiable, el motivo
    labels: {belt_state: running}
    counts: {stone: {value: 12, tol: 1}}   # por class_name; un número suelto = exacto
    metrics:
      load_pct: {value: 63.0, tol: 2.0}
    detections:
      iou_min: 0.5
      min_recall: 0.9
      min_precision: 0.9
      boxes:
        - {class_name: stone, bbox_px: [412, 220, 470, 281]}
```

- **Sólo se chequea lo que está declarado.** Un caso puede afirmar nada más que un conteo,
  o que el resultado sale inválido por `dark_frame`. Etiquetar cajas es caro, y obligar a
  hacerlo en cada caso haría que no se haga ninguno.
- **Las cajas van en el espacio del frame de referencia**, el mismo de
  `Detection.bbox_px`: si el preprocessor corrige el lente, las cajas son sobre la imagen
  corregida.
- **Nunca se compara una confianza exacta.** Una GPU distinta, otro TensorRT u otro
  driver mueven decimales; el ground truth afirma qué se detecta, no con cuánta
  seguridad. Por eso todo número numérico lleva tolerancia.
- El matching de cajas es greedy por IoU dentro de cada clase; de ahí salen precisión y
  recall del caso.

`ground_truth.py` es puro: recibe el dict del YAML y un `InferenceResult`, y devuelve un
reporte con qué chequeo falló y por cuánto. No lee archivos ni conoce el runner.

### El reporte

Por consola: una línea por caso (pasa / falla / borrador) con el primer chequeo que falló.
En `--out` (por defecto `data/ground_truth/<fecha>/`, ignorado por git): un JSON con todo,
el anotado de cada caso que falló, y el agregado por pipeline —precisión, recall y F1 por
clase donde hubo cajas, error medio de conteos y de métricas, y el tiempo medio por etapa,
que sirve para ver una regresión de rendimiento—.

### Cómo se corre en la verificación

`ci/check.py ground-truth` llama a `python -m system.offline evaluate`. **No entra en los
hooks de git del template**: necesita los pesos del fork, que no se versionan, y quizá
GPU. Un fork cuyo modelo corre en CPU en pocos segundos lo puede sumar a su pre-push. La
suite del template sí corre el ejemplo de `test/offline/data/` contra el `MockModel`, que
es determinista: es la prueba de punta a punta del runner y del evaluador.

## 9. Lo que se agrega además

- **Receta RTSP** en `manual_test/rtsp_replay/`: un README con MediaMTX y
  `ffmpeg -re -stream_loop -1 -i video.mp4 -c copy -f rtsp rtsp://localhost:8554/camera_1`,
  y un `config.yaml` con la cámara en `driver: rtsp`. Para probar el `RtspDriver` —cortes,
  reconexión, credenciales—, no el sistema. Es lo último y opcional.
- **`docs/offline_testing.md`** —documento con público propio—: cómo tienen que ser los
  videos (resolución de la cámara real, frames crudos, sin overlay, H.264 en `.mp4` o MJPG
  en `.avi`), cómo se declara la cámara, los tres comandos y el formato del ground truth. El README de
  `ground_truth/` apunta ahí.
- **CLAUDE.md**: el mapa (driver, `system/offline/`, `hooks.py`, `preprocessing.py`,
  `ground_truth/`, `recordings/`), una decisión vigente por §2 y §8, y «Qué se toca en un
  fork» si se toma (a) en §6. **README.md**: la tabla de archivos. `system/version.py`
  sube en el commit que cierra.

### Tests

| Archivo | Qué prueba |
|---|---|
| `test/tools/camera/test_video_file_driver.py` | loop, playlist en orden, ruta inexistente, carpeta vacía, ritmo con reloj falso, salteo de frames, `is_synthetic`, rotación. El video se escribe en `tmp_path` con `VideoWriter` en MJPG, que no depende de codecs del sistema |
| `test/inference/test_engine.py` | `process()` no emite ni cuenta, y levanta con el hilo corriendo |
| `test/inference/test_preprocessing.py` | lo que se mueve de `main.py`, sin cambio de comportamiento |
| `test/offline/test_ground_truth.py` | tolerancias, matching por IoU, claves ausentes, borradores |
| `test/offline/test_runner.py` | punta a punta con el mock y el caso de `test/offline/data/` |

`docs/tests.md` se regenera con `ci/suite_index.py`.

## 10. Trampas

- **Preprocesar dos veces.** El dataset guarda `source_bgr`, que es el frame de referencia
  —ya corregido y ya ajustado—. Una imagen sacada de `data/dataset/.../original/` va con
  `preprocessed: true`; si no, se le corrige el lente y se le multiplica el brillo otra
  vez. `snapshot` lo infiere cuando la imagen está bajo `system.paths.dataset` y lo deja
  escrito.
- **Rotar dos veces.** Un video grabado del stream ya sale rotado: la cámara de replay
  hereda la `rotation` de la real, así que el video se graba sin rotar o se documenta.
  Lo mismo vale para los videos: tienen que ser frames **crudos** del sensor.
- **La resolución tiene que ser la de la cámara.** El ROI, `process:` y las cajas están en
  píxeles: un video reescalado da números creíbles y equivocados.
- **La compresión cambia la imagen.** H.264 baja la nitidez y mete bloques: la salud de la
  óptica sobre un replay no vale —**no se calibra la referencia del lente con un video**— y
  la confianza del modelo puede moverse respecto de la cámara. El ground truth va en PNG
  justamente por eso.
- **Pesos cifrados.** Desde fuentes no hay `_model_key.py`, así que el ground truth se
  corre con los pesos en claro. Es el mismo modelo; cifrarlo no cambia lo que infiere.
- **Un borrador no es ground truth.** `snapshot` congela lo que el sistema hace hoy, con
  sus errores. Hasta que alguien lo revisa (`reviewed: true`) es un test de regresión, no
  de corrección, y `evaluate` lo cuenta aparte.

## 11. Decisiones abiertas

1. ~~Sacar los hooks del fork de `main.py`~~: **decidido, opción (a).** Cambia una regla
   de fork y obliga a migrar los forks existentes.
2. **Git plano o LFS para las imágenes** del ground truth. Recomendado plano con el
   presupuesto de §8, y LFS el día que un fork lo pase.
3. **Nombre de las carpetas**: `recordings/` y `ground_truth/` en la raíz, como `models/`.
   No van en `data/`, que es lo que la app escribe en runtime.

## 12. Orden de implementación

Un commit por paso, todos de maquinaria:

1. Driver `video_file`, catálogo y su test. **Hecho.**
2. ~~`--replay`~~: descartado (§4). `recordings/` ya existe.
3. `InferenceThread.process()`. **Hecho.**
4. `preprocessing.py` y `hooks.py` fuera de `main.py`, sin cambio de comportamiento. **Hecho.**
5. Runner y prueba manual de imágenes sueltas. **Hecho** (ver el estado, arriba).
6. **Postergado.** `ground_truth.py`, `evaluate`, `snapshot`, `ground_truth/README.md` y
   `ci/check.py ground-truth`.
7. `docs/offline_testing.md`, CLAUDE.md, README.md y la versión (0.7.0). **Hecho.**
8. ~~La receta RTSP de `manual_test/rtsp_replay/`~~: **descartada.** El `RtspDriver` se
   prueba con `manual_test/cameras/` contra una cámara RTSP de verdad, que es más fiel que
   una cámara falsa sirviendo un video.

## 13. Fuera de alcance

- Grabar o editar los videos.
- Importar etiquetas de una herramienta de anotación (COCO, YOLO): el formato de §8 lo
  admite después como un subcomando `import` sin cambiar nada de lo anterior.
- Comparar dos corridas de `evaluate` entre sí (antes/después de reentrenar). El JSON del
  reporte queda con la forma necesaria para hacerlo.
- La captura por trigger, que sigue diferida.
