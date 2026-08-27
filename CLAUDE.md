# CLAUDE.md

Plantilla de proyectos de visión artificial industrial: captura de una o varias
cámaras GigE, inferencia, publicación del resultado a PLC y a dashboards. Cada
instalación es un fork de este repo; lo que cambia entre forks son los valores de
`config.yaml`, el mapa de registros Modbus y el modelo de inferencia, no la
plomería.

**Este archivo es el mapa, no la documentación.** El contrato de cada módulo está
en su propio docstring, que es la fuente de verdad y se lee antes de tocarlo. Las
convenciones de nombres, comentarios y límites entre módulos están en las skills
de `.claude/skills/` (`nomenclatura`, `comentarios`, `modularidad`) y no se
repiten acá. El orden de trabajo para arrancar un fork y la tabla archivo por
archivo de qué se toca están en `README.md`.

## Cómo se corre

El intérprete del proyecto es el del venv, **no** el `python` del PATH:

    .venv\Scripts\python.exe -m pytest -q          # Windows
    .venv/bin/python -m pytest -q                  # Linux

Python 3.10 de 64 bits, fijado por el wheel cp310 de stapipy. La suite completa
corre sin hardware, sin GUI y sin red: todo lo externo se reemplaza por dobles
dentro del propio archivo de test.

Lo que sí necesita hardware —o una pantalla, como la prueba de la interfaz— vive en
`manual_test/<tema>/`, cada uno con su `config.yaml` al lado. La instalación de los
SDK de cámara está en `setup/cameras/{windows,linux}/`.

## Mapa

    system/                   la app: subsistemas propios, con Qt y con I/O
      config_manager.py       nivel 0 — singleton thread-safe sobre config.yaml
      logger.py               nivel 0 — logger único; nadie crea otro
      paths.py                nivel 0 — rutas del proyecto sin depender del CWD
      version.py              nivel 0 — la versión del programa; va en código, no en config
      system_monitor.py       métricas de hardware: CPU, RAM, disco, red, GPU
      camera/
        capture_thread.py     un hilo por cámara; entrega frames y telemetría por señales
      formats/                módulos puros: cada uno arma un bitfield y nadie más corre bits
        camera_health.py      estado de una cámara: adquisición excluyente + lente sucio
        com_status.py         un bit por canal de salida que está andando
        system_status.py      ¿se puede confiar en las mediciones de proceso?
      image_collector/
        collector.py          dataset en disco, por intervalo o a pedido
        conditions.py         módulo puro: predicados sobre el dict de inferencia
      inference/
        abstract_model.py     el contrato del modelo: ciclo de vida y coordenadas propias
        model_factory.py      único archivo que conoce las clases concretas
        mock_model.py         detecciones sintéticas, sin framework
        null_model.py         lo que devuelve la fábrica ante una config inválida
        abstract_pipeline.py  maquinaria de las etapas: las crea, las carga y las cronometra
        pipeline.py           el pipeline concreto; es lo que cambia en cada instalación
        result.py             nivel 1 — Detection e InferenceResult, el dato que cruza
        overlay.py            nivel 1 — dibujado del resultado; también lo lee la UI
        annotations.py        nivel 1 — dibujos propios de la planta; se reescribe en cada fork
        analysis.py           nivel 1 — agregaciones genéricas sobre detecciones
        metrics.py            nivel 1 — las métricas del proceso; se reescribe en cada fork
        engine.py             un hilo por pipeline; fan-in de las cámaras que tiene asignadas
      modbus/
        schema.py             nivel 1 — maquinaria del mapa: carga, escalas, espejo R/W
        register_map.yaml     el mapa concreto; es lo que cambia en cada instalación
        registers.py          carga el YAML al importar y expone el SCHEMA validado
        server.py             servidor esclavo TCP + RTU sobre un datastore en RAM
        export_map.py         genera docs/modbus_map.{md,csv} desde el YAML
      telemetry/
        persistence.py        junta puntos en una ventana y los escribe en batch
        backends/
          abstract_backend.py el contrato: ciclo de vida, registro y vocabulario de status
          influxdb_backend.py InfluxDB 2.x
          mqtt_backend.py     paho-mqtt
      video/
        abstract_video_server.py  el contrato: modos, status y gating por cliente
        http_server.py        streams MJPEG: uno crudo y uno anotado por cámara
        rtsp_server.py        los mismos streams por RTSP, con GStreamer

    tools/                    librerías portables; no conocen la app
      camera/
        abstract_driver.py    contrato del driver: always-fresh, rotación, fps medido
        camera_factory.py     único archivo que conoce las clases concretas
        basler_driver.py      pypylon
        st_driver.py          stapipy (Sentech / Omron)
        mock_driver.py        frames sintéticos, sin hardware
        null_driver.py        lo que devuelve la fábrica ante una config inválida
        camera_catalog.py     modelos por fabricante, para la UI
      image/                  operaciones sobre un frame; no saben de dónde salió
        enhance.py            gamma y contraste local para el camino de visualización
        undistort.py          corrección de lente; va en el preprocessor del motor

    ui/                       nivel 4 — presentación; no la importa nadie de system/ ni tools/
      strings.py              tabla de textos por idioma (es/en/pt); nadie escribe un literal
      theme.py                único dueño de los colores; el QSS y las paletas de lo que se dibuja
      service_status.py       estado de un servicio -> texto y color de su chip
      main_window.py          header, las tres vistas y la barra de estado
      views/
        monitor_view.py       barra lateral + área central VACÍA; la llena el fork
        config_view.py        cascarón: arma las pestañas y persiste una sola vez
        diagnostics_view.py   cascarón: reparte a las pestañas lo que le llega
        config/               una pestaña por sección del config; +1 línea para agregar otra
        diagnostics/          hardware, Modbus, video y logs
      widgets/                form, status_chip, log_table, realtime_chart,
                              camera_panel (una cámara), camera_grid (N, con foco)
      dialogs/                roi_dialog, gpio_dialog
      styles/                 dark.qss y light.qss, espejados regla por regla

    test/                     espeja el árbol de arriba; conftest.py en la raíz
    manual_test/              pruebas con hardware o pantalla, con su config.yaml al lado
      ui/                     levanta la app entera con cámaras mock; hace de main.py
    setup/                    instalación de SDK de cámara (en inglés, ver skill)
    packages/                 wheels que no están en PyPI (stapipy)
    docs/                     documentos con público propio: el mapa Modbus generado y ui.md
    data/                     logs y dataset en runtime

La dirección de las dependencias y las reglas de límites están en la skill
`modularidad`.

## Contratos que cruzan módulos

Son punteros: el contrato vive en el archivo, no acá.

- **Driver de cámara** — `tools/camera/abstract_driver.py`: entrega always-fresh y
  las claves estables de `get_status()`.
- **Backend de telemetría** — `system/telemetry/backends/abstract_backend.py`: la
  forma del registro (`measurement`, `tags`, `fields`, `time`) y los `STATUS_*`.
- **Servidor de video** — `system/video/abstract_video_server.py`: un stream por
  cámara y modo, el vocabulario de `status` y el gating por cliente conectado.
- **Modelo de inferencia** — `system/inference/abstract_model.py`: el ciclo de vida,
  las coordenadas en el espacio del frame recibido, el umbral por detección y el
  vocabulario de `task` con el que `_verify_task()` confronta los pesos.
- **Pipeline de inferencia** — `system/inference/abstract_pipeline.py`: qué es una
  etapa, qué recibe de la anterior, y qué queda en `stage_times_ms` y en `labels`. El
  pipeline concreto es `system/inference/pipeline.py`.
- **Resultado de inferencia** — `system/inference/result.py`: los campos de
  `Detection` e `InferenceResult`, el reparto entre `detections`, `labels` y `metrics`,
  el vocabulario de `invalid_reason` y qué significa `is_valid`. Es el objeto que
  consumen la UI, el dataset, la telemetría y el PLC.
- **Annotator del proyecto** — `system/inference/annotations.py`: una función que recibe
  el frame ya anotado y el resultado, y dibuja in-place sus referencias. Se compone con
  `chain()` y entra por el constructor del motor.
- **Preprocessor** — `system/inference/engine.py`: `(frame, camera_slot) -> frame`, y lo
  que devuelve es el frame de referencia del ciclo. El corrector de lente que lo cumple
  está en `tools/image/undistort.py`.
- **Mapa de registros Modbus** — `system/modbus/schema.py`: los campos de una fila,
  el vocabulario de `producer`, las escalas y el espejo R/W del bloque de config.
  El mapa concreto es `system/modbus/register_map.yaml`.
- **Bitfields** — `system/formats/*.py`: cada archivo es el dueño de su palabra y
  documenta qué significa cada bit. Nadie corre bits afuera.
- **Configuración** — `system/config_manager.py`: rutas punteadas, copias en la
  entrega, config de rescate.
- **Pestaña de configuración** — `ui/views/config/abstract_tab.py`: `TITLE_KEY`,
  `load()` y `save()`, y la regla de que `save()` no persiste el archivo.
- **Área central del monitor** — `ui/views/monitor_view.py`: `set_content()` es el punto
  de extensión de la vista de operador; el resto de la UI no conoce ese widget.
- **Textos de la UI** — `ui/strings.py`: la clave es estable y en inglés, el texto sale
  por `tr()` en el idioma de `ui.language`.
- **Señales de Qt** — cada una documenta su payload y su frecuencia donde se declara.

## Decisiones vigentes

Lo que no se deduce leyendo un archivo suelto:

- Las claves de `config.yaml` van en inglés y la jerarquía espeja los módulos, no
  las pantallas de la UI.
- Las credenciales nunca van al config: salen del entorno (`INFLUXDB_TOKEN`,
  `MQTT_PASSWORD`).
- **La versión del programa va en código** (`system/version.py`), no en `config.yaml`: el
  config declara lo que cambia entre instalaciones y la versión cambia cuando cambia el
  código. En el config, una planta podría decir que corre una versión que no corre, y ese
  número es justo el que el operador lee por teléfono cuando algo anda mal. Se muestra
  abajo a la derecha, junto al `project_id`, y se sube en el commit que cierra el cambio.
- **En las cámaras no se ajusta nada en caliente.** Exposición, ganancia, fps y
  `enabled` se cambian en `config.yaml` —desde la UI o a mano— y se reinicia la app. Lo
  único que la UI aplica sin reiniciar son el idioma y el tema.
- Un hilo por cámara, y el ritmo lo pone la cámara (free-run). Si alguna vez se
  captura por trigger, el ritmo y el orden de los disparos son de quien orqueste
  la captura, no del hilo.
- El video sale por dos caminos y no compiten: MJPEG por HTTP es el stream liviano
  para mirar en el navegador, y RTSP es el de resolución completa para un NVR o un
  reproductor. El RTSP se publica por interfaz (`video.rtsp.net_interfaces`), y
  necesita GStreamer instalado en el equipo.
- **Un hilo de inferencia por pipeline, no por cámara.** Las cámaras que comparten
  modelo comparten el hilo: entran por una cola con el último frame de cada una, y el
  hilo las atiende por turno. Así los pesos se cargan una sola vez y el acceso a la
  GPU queda serializado por construcción, sin lock ni copia por cámara. Dos pipelines
  distintos son dos hilos y sí corren en paralelo.
- **El orden de las etapas de un pipeline es código, no configuración.** Vive en
  `system/inference/pipeline.py` porque encadenar dos modelos es lógica del proceso:
  qué recorta el segundo, qué hace con lo que encontró el primero. Del config salen de
  dónde se carga cada modelo y qué cámaras entran a cada pipeline.
- La confianza se juzga en dos niveles y son de dueños distintos: el modelo descarta
  la detección por debajo de su `min_confidence_pct` —bajo a propósito cuando hay que
  contar cientos de objetos—, y el pipeline marca el resultado completo como no
  confiable por `min_result_confidence_pct` y `min_detections`. Un resultado no
  confiable se emite igual, con el motivo: el frame anotado sigue sirviendo, los
  números no son una medición.
- **Del motor sale un resultado por frame y nadie promedia en el camino.** Un proyecto que
  responde con N imágenes por medición declara cuántas en
  `inference.pipelines.<slot>.frames_per_cycle` —que el motor no lee— y quien orquesta el
  ciclo agrega esos N resultados con `analysis.summarize_metrics` (media, desvío y rango,
  plano y con `sample_count`) y elige con `analysis.pick_representative` cuál de los N
  frames se guarda y se muestra. Promediar en el motor escondería la dispersión, que suele
  ser el dato que dice si el proceso está estable.
- Qué significan las detecciones es del proyecto y se inyecta: `metrics.compute_metrics`
  entra por el constructor del motor y su salida viaja en `metrics`, un dict plano.
  Plano porque va sin traducir al panel del anotado, al JSON del dataset, a los fields
  de la telemetría y a los registros del PLC.
- **Los parámetros de lo que se mide viven en la sección `process` del config**, que es la
  única cuya forma cambia entre proyectos. No la lee ningún módulo de la maquinaria: la lee
  `main.py` y se la pasa al analyzer y a los annotators, que así reciben números y se
  testean sin archivo. Un valor que alimenta a dos consumidores —la altura del límite de
  carga, que se dibuja y se mide— se lee una sola vez, para que el operador y el PLC no
  puedan estar mirando cosas distintas.
- El frame anotado se dibuja una sola vez, en el motor, y el mismo array viaja a la UI,
  al stream `annotated` y al dataset. Si nadie mira ese stream no se dibuja: quién está
  conectado lo sabe `main.py`, que se lo pasa al motor como gate. Lo que sale de la
  inferencia lo dibuja `overlay`, que es genérico; las referencias de la planta —un
  límite de carga, una zona— son annotators de `annotations.py` que el motor corre
  después, y su geometría va al config porque cambia con cada montaje.
- **Un veredicto del frame no es una detección ni una métrica.** Lo que dice una
  clasificación de la imagen entera —el estado de la cinta— viaja en `labels`, que es
  texto: no tiene forma ni posición, y si tiene que llegar al PLC lo traduce a bit el
  analyzer, que es el único que sabe a qué equivale.
- **La tarea de un modelo la declara la implementación, no el archivo de pesos.** Un
  `.pt` y un `.engine` entran por el mismo `type` del config, y cuando el runtime informa
  qué exportó, `load()` lo confronta y un desacuerdo deja el modelo en error antes del
  primer frame. Olfatear el archivo daría números creíbles con el postproceso equivocado.
- El ROI y el mínimo de iluminación son de la cámara y viven en su sección del config:
  se recorta y se mide el brillo antes de gastar una pasada del modelo, y las
  detecciones vuelven al espacio del frame de referencia antes de salir.
- **Corregir la geometría y ajustar el brillo son cosas distintas y van en caminos
  distintos.** La corrección de lente cambia *dónde está* un píxel, así que entra por el
  `preprocessor` del motor y lo que devuelve es el frame de referencia: el que ve el
  modelo, el que queda en `source_bgr`, el espacio del ROI y el lienzo del overlay. Un solo
  espacio de coordenadas y nada que mapear de vuelta. El brillo cambia *cómo se ve* un
  píxel y no mueve nada, así que va sólo en el camino de visualización
  (`video.display`, `tools/image/enhance.py`): el modelo y el dataset siguen recibiendo el
  frame crudo, porque el dataset es con lo que se reentrena. Un modelo entrenado con
  imágenes crudas que necesite mostrarse corregido tiene dos salidas honestas —reentrenar
  con el dataset corregido, o corregir después de anotar y aceptar que las etiquetas se
  deformen con la imagen—; lo que no se hace es medir en un espacio y dibujar en otro.
- La telemetría se acumula en una ventana y sale en batch; cada punto viaja con el
  instante en que se midió, no con el de la escritura.
- **El mapa Modbus es un archivo de datos, no código.** Vive en
  `system/modbus/register_map.yaml` porque es lo que cambia en cada instalación, y
  es fuente única: la tabla que lee el integrador se genera con
  `python -m system.modbus.export_map` y no se edita a mano. Direccionamiento
  base-1, regla única **reg N ⇔ 4000N**, y ninguna dirección se escribe como
  literal fuera de `system/modbus/`: se pide con `SCHEMA.addr("nombre")`.
- El mapa se sirve de **sólo lectura**: el PLC lee con FC03 y la app escribe desde
  adentro. Las demás funciones se rechazan con 0x02 en vez de contestar un valor
  que parezca una medición. El bloque de configuración escribible por el PLC existe
  en la maquinaria pero el template no lo usa.
- Las escalas son del esquema, no del productor: quien mide entrega el valor físico
  a `SCHEMA.encode()` y el registro sale escalado y saturado a uint16.
- Modbus TCP y RTU comparten el datastore y son independientes: el que no levanta
  deja el motivo en su `status` y no tumba al otro. El RTU es el que se apaga por
  defecto, porque necesita el puerto serie libre.
- Un módulo al que le falta su librería de sistema degrada a no-op adentro y expone
  la misma API. El llamador no pregunta si está disponible.
- `ui/` no lo importa nadie de `system/` ni de `tools/`.
- **La UI no tiene colores ni textos propios repartidos.** Los colores de los widgets
  estándar están en el QSS de los dos temas y los de lo que se dibuja con QPainter en
  `ui/theme.py`; los textos, en `ui/strings.py`, con la clave en inglés y una columna por
  idioma. Un color o un texto en dos archivos es el bug que este límite evita. La única
  excepción de estilo inline es una animación, porque el QSS no interpola colores.
- **La vista de monitor viene vacía y es a propósito.** Qué se mira mientras la línea
  trabaja es lo más específico de cada instalación: el template trae la barra lateral
  —los seis canales de salida y el log— y el fork le pasa su widget con `set_content()`.
  Para el caso normal ya está `camera_grid`, que muestra **todas** las cámaras
  configuradas —una caída queda con «SIN SEÑAL» en su lugar, para que se vea que falta— y
  no elige qué frame entra: publica el modo crudo/anotado y lo resuelve el cableado. Una
  disposición distinta —tres cámaras por pantalla— no se edita en la grilla: se le pasa
  `camera_slots` y se arman varias dentro del widget del fork, que conserva la misma API
  por slot para que el cableado no cambie.
- **Un valor del config que no está entre las opciones de un combo no se pierde.** Se
  agrega como opción y vuelve al archivo tal como estaba: `setCurrentText()` sobre un
  combo no editable es un no-op silencioso, así que sin eso abrir el panel de
  configuración y guardar alcanzaba para cambiar la marca de una cámara o el tipo de un
  modelo que la fábrica todavía no registra. Lo hace `set_combo_value()`, y los mapeos de
  rotación, modo y codec devuelven el valor crudo cuando no lo conocen en vez de caer a
  un default.
- **La UI no arma direcciones ni rutas de protocolo.** Las URLs de los streams le llegan
  hechas por `set_stream_urls()` desde el cableado, que las pide a cada servidor: la
  forma de la ruta es del servidor de video, y componerla en la UI la dejaría definida en
  dos lugares.

## Qué se toca en un fork

La regla: **si un archivo describe cómo se hace algo, es maquinaria y se
cross-portea; si describe qué se mide en esta planta, es del fork.**

- **Se reescriben**: `config.yaml` —sección `process:` incluida—,
  `system/modbus/register_map.yaml`, `system/inference/pipeline.py`,
  `system/inference/metrics.py`, este archivo y el `README.md`.
- **Se agregan sin editar lo que ya está**: `main.py`, el modelo del proyecto en
  `system/inference/` (+1 línea en la fábrica), `annotations.py`, un driver nuevo
  en `tools/camera/` (+1 línea en su fábrica), el widget del área central del monitor
  (+1 línea de `set_content()` en `main.py`), y lo que corresponda en `test/`,
  `manual_test/` y `docs/`.
- **De `ui/` sólo se agrega**: el widget del monitor, los textos que ese widget necesite
  en `strings.py`, y —si el fork quiere editar `process:` desde la pantalla— una pestaña
  en `views/config/` con +1 línea en `_TAB_CLASSES`. Las pestañas genéricas, los widgets,
  los temas y las tres vistas son maquinaria: ver `docs/ui.md`.
- **Todo lo demás es maquinaria.** Si hay que editarla para que el fork funcione,
  el límite está mal puesto: lo que falta es un punto de extensión, no un parche.

Los cuatro puntos de extensión del motor de inferencia —`pipeline`, `analyzer`,
`annotator`, `annotate_gate`— entran por su constructor y los cablea `main.py`.
La tabla completa, archivo por archivo, está en `README.md`.

## Qué todavía no existe

- **`main.py`**: no hay composition root; hoy nadie cablea los subsistemas entre sí.
  Es lo que falta para que el Modbus publique algo: el servidor y el mapa están,
  pero nadie llena los registros todavía. También es donde se instancian el pipeline
  y el motor de inferencia, y donde se le pasan el analyzer y el gate del anotado.
- El rango 3-50 del mapa de registros sigue reservado y vacío: la inferencia ya corre,
  pero qué publica es lo más específico de cada fork y se declara al escribirlo.
- **El área central de la vista de monitor** y el módulo de GPIO. La UI está completa y
  andando —tres vistas, siete pestañas de configuración, cuatro de diagnóstico— salvo dos
  huecos a propósito: el widget que va en el centro del monitor lo pone el fork con
  `set_content()`, y `ui/dialogs/gpio_dialog.py` es la mitad de UI de un
  `system/gpio_control.py` que todavía no existe (su docstring declara la interfaz que
  espera, y el botón del header aparece sólo cuando se lo inyecta). Cómo se toca todo eso
  está en `docs/ui.md`.
- El modelo del proyecto. El template trae el mock —detecciones sintéticas, sin
  framework— y el fork agrega el suyo en `system/inference/` registrándolo en la
  fábrica; el pipeline y las métricas son los otros dos archivos que se reescriben.
- La captura por trigger de software está diseñada y diferida en
  `.claude/plans/software-trigger-capture.md`.
- Las próximas líneas de trabajo —visuales de configuración, protección del entregable
  (hash del modelo, licencia por hardware, compilar) y rendimiento/despliegue
  (optimización en Jetson, Docker)— están en `.claude/plans/roadmap.md`, con qué hay que
  averiguar antes de empezar cada una. Ninguna está decidida.

## Dónde va lo que se escribe

- El contrato de un módulo → su docstring, en la misma edición que el código.
- El mapa, las decisiones y lo que falta → este archivo.
- Un documento con público propio —notas de puesta en marcha, un protocolo nuevo—
  → `docs/`. El mapa de registros ya vive ahí, y es generado: se edita el YAML.
- Cómo se tocan los atributos de la interfaz —colores, textos, medidas, una pestaña
  nueva— → `docs/ui.md`. No se repite en los docstrings de `ui/`: ellos apuntan ahí.
- Convenciones → las skills. No se copian acá.
- Cómo devolver una mejora de maquinaria al template —cuándo es un punto de extensión y
  cuándo un PR, y por qué los commits de maquinaria van separados desde el primer día—
  → la sección «Cómo devolver una mejora al template» del `README.md`.

Un subsistema gana carpeta propia cuando tiene más de un archivo; hasta entonces es
un archivo suelto en `system/`.
