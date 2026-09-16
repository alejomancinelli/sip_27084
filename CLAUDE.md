# CLAUDE.md

**SIP Smart Belt Monitor — Cargill APG, proyecto 27084.** Mide la composición del material
que pasa por una cinta: qué proporción es pellet entero y qué proporción viene desmenuzado,
y cuán cargada está la cinta. Una cámara GigE sobre la cinta, un YOLO de segmentación en una
Jetson, y el resultado al PLC por Modbus y a un dashboard de Grafana por InfluxDB.

Es un fork del template de visión artificial industrial de la familia. La plomería —captura,
inferencia, telemetría, video, licencia, interfaz— viene del template y se cross-portea; lo
del proyecto son `config.yaml`, el mapa de registros, el modelo, el pipeline, las métricas y
los annotators.

**El equipo corre en una NVIDIA Jetson.** El `.engine` de TensorRT se compila ahí y no en la
PC de desarrollo —está atado a la arquitectura de GPU y a la versión de TensorRT—, las
métricas de GPU y consumo salen de sysfs de Tegra y del INA3221, y el GPIO es libgpiod sobre
`/dev/gpiochip0`. El desarrollo y la suite de tests corren en Windows sin GPU: el modelo
`mock` cubre el camino entero.

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

    main.py                   nivel 5 — el cableado: instancia todo y conecta las señales

    system/                   la app: subsistemas propios, con Qt y con I/O
      config_manager.py       nivel 0 — singleton thread-safe sobre config.yaml
      env.py                  nivel 0 — carga el .env del equipo en el entorno
      logger.py               nivel 0 — logger único; nadie crea otro
      paths.py                nivel 0 — rutas del proyecto sin depender del CWD
      version.py              nivel 0 — la versión del programa; va en código, no en config
      gpio_control.py         entradas y salidas digitales; degrada a simulado
      system_monitor.py       métricas de hardware: CPU, RAM, disco, red, GPU
      camera/
        capture_thread.py     un hilo por cámara; entrega frames y telemetría por señales
        lens_health.py        nivel 1 — ¿el vidrio está sucio? nitidez sobre el ROI
      formats/                módulos puros: cada uno arma un bitfield y nadie más corre bits
        camera_health.py      estado de una cámara: adquisición excluyente + lente sucio
        com_status.py         un bit por canal de salida que está andando
        gpio_status.py        las dos palabras de GPIO: estado, falla, ausencia
        system_status.py      ¿se puede confiar en las mediciones de proceso?
      image_collector/
        collector.py          dataset en disco, por intervalo o a pedido
        conditions.py         módulo puro: predicados sobre el dict de inferencia
      inference/
        models/               una implementación por framework, detrás del mismo contrato
          abstract_model.py   el contrato del modelo: ciclo de vida y coordenadas propias
          encrypted_weights.py nivel 1 — dueño del formato de los pesos cifrados
          model_key.py        de dónde sale la clave con la que se abren esos pesos
          model_factory.py    único archivo que conoce las clases concretas
          mock_model.py       detecciones sintéticas, sin framework
          yolo_seg_model.py   DEL PROYECTO — YOLO de segmentación (ultralytics)
          null_model.py       lo que devuelve la fábrica ante una config inválida
        abstract_pipeline.py  maquinaria de las etapas: las crea, las carga y las cronometra
        pipeline.py           DEL PROYECTO — una etapa: el segmentador de la cinta
        result.py             nivel 1 — Detection e InferenceResult, el dato que cruza
        overlay.py            nivel 1 — dibujado del resultado; también lo lee la UI
        annotations.py        DEL PROYECTO — el rectángulo de cinta y el panel
        analysis.py           nivel 1 — agregaciones genéricas sobre detecciones
        rolling.py            nivel 1 — media móvil por ventana; las tendencias de 1 h
        metrics.py            DEL PROYECTO — composición y carga por conteo de píxeles
        engine.py             un hilo por pipeline; fan-in de las cámaras que tiene asignadas
      license/                ata el equipo a la máquina para la que se emitió la licencia
        schema.py             nivel 1 — dueño del formato del .lic: campos, token, firma
        verify.py             nivel 0 — Ed25519 contra la clave pública embebida
        public_key.py         nivel 1 — clave pública por `key_id`; la tabla la genera el
                              build en `_public_key.py`, y la privada NO vive acá
        policy.py             nivel 1 — qué hace el equipo cuando la licencia no vale
        fingerprint.py        nivel 0 — huella por componente; nunca levanta excepción
        clock.py              nivel 0 — último-visto firmado; caza el reloj atrasado
        manager.py            la fachada: lo único que importa main.py
        request.py            arma la solicitud que se manda a firmar
        __main__.py           CLI: status | fingerprint | request | install
      modbus/
        schema.py             nivel 1 — maquinaria del mapa: carga, escalas, espejo R/W
        register_map.yaml     DEL PROYECTO — las direcciones son las que ya lee el PLC
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
        rtsp_driver.py        cámara IP por RTSP, con FFmpeg de OpenCV
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
        diagnostics/          hardware, Modbus, video, licencia y logs
      widgets/                form, status_chip, log_table, realtime_chart,
                              camera_panel (una cámara), camera_grid (N, con foco)
      dialogs/                roi_dialog, gpio_dialog
      styles/                 dark.qss y light.qss, espejados regla por regla

    test/                     espeja el árbol de arriba; conftest.py en la raíz
    manual_test/              pruebas con hardware o pantalla, con su config.yaml al lado
      ui/                     levanta la app entera con cámaras mock; hace de main.py
      license/                emite la solicitud y simula el binario para probar la licencia
      model_protection/       cifra unos pesos y los abre en memoria; mide el costo
    setup/                    instalación de SDK de cámara (en inglés, ver skill)
    packages/                 wheels que no están en PyPI (stapipy)
    build/                    compilar con Nuitka y armar el entregable; ver su README
    docs/                     documentos con público propio: el mapa Modbus generado,
                              ui.md, influxdb.md (la estructura de las series) y
                              licensing.md (cómo se pide y se renueva una licencia),
                              influxdb_template.md (la estructura CANÓNICA del
                              template, de la que este equipo diverge a propósito),
                              mqtt.md (la misma estructura, en tópicos y JSON) y
                              model_protection.md (cómo se protege y se reemplaza un modelo)
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
- **Modelo de inferencia** — `system/inference/models/abstract_model.py`: el ciclo de vida,
  las coordenadas en el espacio del frame recibido, el umbral por detección y el
  vocabulario de `task` con el que `_verify_task()` confronta los pesos.
- **Pesos protegidos** — `system/inference/models/encrypted_weights.py`: la disposición
  del contenedor cifrado, qué claves entiende su metadata y qué se levanta cuando no abre.
  De dónde sale la clave es de `model_key.py`, y cómo se protege un modelo está en
  `docs/model_protection.md`.
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
- **Classifier** — `system/inference/engine.py`: `(detections, camera_slot) -> detections`,
  entre el pipeline y el promedio de confianza. Es donde entra lo calibrado por cámara, que
  el modelo no puede aplicar porque es uno solo para todas las del pipeline.
- **Mapa de registros Modbus** — `system/modbus/schema.py`: los campos de una fila,
  el vocabulario de `producer`, las escalas y el espejo R/W del bloque de config.
  El mapa concreto es `system/modbus/register_map.yaml`.
- **Bitfields** — `system/formats/*.py`: cada archivo es el dueño de su palabra y
  documenta qué significa cada bit. Nadie corre bits afuera.
- **GPIO** — `system/gpio_control.py`: el ciclo de vida de las líneas, qué devuelve una
  lectura que falló y qué significa `hardware_available`. Las dos palabras que van al PLC
  son de `system/formats/gpio_status.py`; el controlador entrega estados por canal y no
  arma bits. La mitad de interfaz es `ui/dialogs/gpio_dialog.py`.
- **Medias móviles** — `system/inference/rolling.py`: la ventana evicciona por tiempo y
  `tick()` va separado de `add()`, para que una inferencia caída drene la ventana en vez de
  congelarla. Cuánto de la ventana se midió lo dice `fill_pct()`, que es lo que valida la
  media.
- **Salud de la óptica** — `system/camera/lens_health.py`: el vocabulario `STATE_*`, qué
  necesita calibrarse y cuándo la ventana habilita a afirmar que el vidrio está sucio. La
  puerta es `LensHealthMonitor`, que guarda **una ventana y una referencia por cámara**;
  las funciones sueltas son sus primitivos. El veredicto sale como estado propio y quien
  cablea lo traduce al bit de lente sucio de `formats/camera_health.py`.
- **Licencia** — `system/license/manager.py`: el vocabulario `STATE_*`, qué habilita cada
  estado y las claves de `get_status()`. Es la única puerta del subsistema. El formato del
  `.lic` es de `schema.py`, qué hace el equipo cuando no vale es de `policy.py`, y cómo se
  pide y se renueva está en `docs/licensing.md`.
- **Configuración** — `system/config_manager.py`: rutas punteadas, copias en la
  entrega, config de rescate.
- **Pestaña de configuración** — `ui/views/config/abstract_tab.py`: `TITLE_KEY`,
  `load()` y `save()`, y la regla de que `save()` no persiste el archivo.
- **Área central del monitor** — `ui/views/monitor_view.py`: `set_content()` es el punto
  de extensión de la vista de operador; el resto de la UI no conoce ese widget.
- **Textos de la UI** — `ui/strings.py`: la clave es estable y en inglés, el texto sale
  por `tr()` en el idioma de `ui.language`, que queda fijo por corrida.
- **Señales de Qt** — cada una documenta su payload y su frecuencia donde se declara.

## Decisiones vigentes

Lo que no se deduce leyendo un archivo suelto. Las primeras son de este proyecto; las que
siguen vienen del template.

- **Lo que se mide es superficie de píxeles segmentados, contada por unión.** Un píxel es
  pellet, o es desmenuzado, o es cinta a la vista, y no puede ser dos cosas: las máscaras se
  rasterizan sobre un lienzo de etiquetas antes de contar y donde dos se superponen gana la
  última, que es el mismo criterio con el que se pinta el overlay. Sumar el área de cada
  instancia contaría dos veces lo que dos polígonos se pisan e inflaría la carga sin que
  pase nada en la cinta.
- **Los dos ejes de la medición tienen denominadores distintos, y es a propósito.**
  `pct_<clase>` se divide por el frame completo; `pct_carga`, por el rectángulo de cinta de
  `process.belt_roi_px`. Por eso la suma de las clases no da la carga, y por eso la carga
  puede pasar de 100 % —que es el dato de que la cinta desbordó— sin que ningún porcentaje
  por clase lo haga.
- **El rectángulo de cinta va en `process:` y NO en `cameras.<slot>.roi`.** Si fuera el ROI
  de cámara, el motor recortaría antes de inferir y los dos denominadores pasarían a ser el
  mismo, cambiando en silencio todos los porcentajes publicados. Como está en `process:`, lo
  lee `main.py` una sola vez y se lo pasa al analyzer y al annotator: el rectángulo que ve
  el operador y el que se usó para el número que salió al PLC son el mismo.
- **El brillo ya no entra a la inferencia.** La versión anterior infería sobre el frame con
  un ajuste de software de ×4.6 y calibraba los umbrales contra esa imagen. Acá el nivel lo
  fija la **exposición de la cámara**, que es donde corresponde: el brillo cambia cómo se ve
  un píxel y no dónde está, así que va sólo en el camino de visualización y el modelo y el
  dataset siguen viendo el frame crudo. El umbral de fondo oscuro hay que recalibrarlo con
  la exposición definitiva: 60 sobre una imagen amplificada no significa lo mismo que 60
  sobre el frame crudo.
- **Las medias de la hora se alimentan una vez por tick, no una por frame.** La ventana se
  compara contra el ritmo de publicación para saber cuánto de la hora se llegó a medir, y
  metiendo los quince frames de cada segundo esa cobertura daría siempre llena aunque la
  inferencia se hubiera caído media hora. Entra el promedio del segundo. Y viven en
  `main.py` y no en el analyzer porque hay que envejecerlas en cada tick, haya medición o
  no: el analyzer corre sólo cuando hay resultado y ahí la ventana se congelaría.
- **La composición sólo promedia con material y la carga promedia todo.** En una cinta vacía
  la composición no es 50/50 ni 0/0: no existe, y meterla en el promedio lo corre hacia
  donde no hay proceso. La carga sí: ahí el 0 es una medición legítima, y es el dato de que
  la cinta estuvo parada. Por eso hay dos registros que validan las medias —cuánto de la
  hora se midió y cuánto de lo medido tenía material—: sin ellos, una media de tres muestras
  sobre una hora se lee igual que una hora entera bien medida.
- **La estructura de la telemetría NO es la del template, y es una deuda consciente.** El
  dashboard ya existía cuando el proyecto se migró, así que se conservaron los measurements
  y los nombres de campo viejos —varios en castellano— en vez de renombrar las series: una
  serie renombrada no se migra, la historia queda con el nombre viejo y el panel deja de
  encontrarla. Toda la divergencia está junta y marcada en un bloque de `main.py`, y la
  estructura canónica quedó guardada en `docs/influxdb_template.md` para que el próximo fork
  no la copie.
- **El mapa de registros es el de la versión anterior del equipo.** Direcciones, escalas y
  semántica se preservan porque el PLC ya está programado contra ellas; lo único que cambió
  son los `name:`, que son el contrato con el código y no con el PLC. Dos excepciones: el
  registro 51 pasó del enum 0-5 al bitfield del template —que es lo que transporta el bit de
  lente sucio— y se agregaron 97-99 sobre direcciones que estaban libres.
- **El nombre de una clase es dato de configuración y decide el nombre de su métrica.** De
  `inference.models.segmenter.class_names` salen `pct_pellet`, `pct_pellet_norm` y el color
  con el que la clase se dibuja; invertir ese orden invierte en silencio todos los
  porcentajes. Se declara una sola vez, ahí: el analyzer y el annotator lo reciben leído por
  `main.py`, y el refinamiento de fondo oscuro lo nombra desde `process:`.

- Las claves de `config.yaml` van en inglés y la jerarquía espeja los módulos, no
  las pantallas de la UI.
- **Las credenciales nunca van al config: salen del entorno**, y al entorno las pone el
  `.env` de la raíz —que no se versiona; el ejemplo con los nombres es `.env.example`—.
  Lo carga `system/env.py` en el arranque y nadie más se entera: cada consumidor sigue
  leyendo `os.environ` (`INFLUXDB_TOKEN`, `MQTT_PASSWORD`, `RTSP_USER` /
  `RTSP_PASSWORD`). Una variable ya definida en el entorno gana sobre el archivo, así
  que un servicio o una prueba a mano mandan sin editarlo. Los scripts de
  `manual_test/` que usan un secreto lo cargan ellos, porque no pasan por `main.py`.
- **Una cámara RTSP con cuenta propia declara `credentials_env`** en su sección y usa
  `RTSP_USER_<sufijo>` / `RTSP_PASSWORD_<sufijo>`; sin esa clave valen las compartidas.
  El sufijo nombra al secreto y no a la cámara —dos cámaras con la misma cuenta apuntan
  al mismo, y una que cambia de slot no obliga a renombrar nada en el equipo—. Si la
  variable falta no se cae a la cuenta compartida: prestarle a una cámara las
  credenciales de otra da un 401 que culpa a lo que no es, o una sesión con una cuenta
  que nadie eligió.
- **La versión del programa va en código** (`system/version.py`), no en `config.yaml`: el
  config declara lo que cambia entre instalaciones y la versión cambia cuando cambia el
  código. En el config, una planta podría decir que corre una versión que no corre, y ese
  número es justo el que el operador lee por teléfono cuando algo anda mal. Se muestra
  abajo a la derecha, junto al `project_id`, y se sube en el commit que cierra el cambio.
- **En las cámaras no se ajusta nada en caliente.** Exposición, ganancia, fps y
  `enabled` se cambian en `config.yaml` —desde la UI o a mano— y se reinicia la app. Lo
  único que la UI aplica sin reiniciar es el tema.
- **El idioma queda fijo por corrida.** Cada widget resuelve su texto con `tr()` cuando
  se construye, así que cambiarlo en caliente sólo alcanzaba a lo que se armaba después y
  dejaba la pantalla partida en dos idiomas —y con un combo cuyas opciones quedaban en el
  idioma anterior, el `save()` escribía la etiqueta traducida en el `config.yaml`—. Se
  guarda como el resto del config y toma efecto al reiniciar, que es lo que dice la nota
  al pie donde se lo elige. Retraducir en vivo pediría un `retranslate()` en cascada por
  toda la UI: un invariante que cada widget nuevo tendría que recordar, y que al olvidarse
  no falla, sólo queda mezclado.
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
- Qué significan las detecciones es del proyecto y se inyecta: `metrics.build_analyzer()`
  —una factory, porque la cuenta necesita el rectángulo de cinta y los umbrales— entra por
  el constructor del motor y su salida viaja en `metrics`, un dict plano.
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
- **El lente sucio se detecta por cámara y no agrega un solo registro.** La nitidez es un
  techo y no un promedio —lo que pase por delante sólo puede bajarla—, así que el
  veredicto es el máximo de una ventana de horas contra una referencia calibrada, y la
  alarma recién se afirma cuando la ventana pasó entera. Esa referencia va en
  `cameras.<slot>.lens_health.reference` porque depende del lente y del montaje:
  compartirla entre cámaras no da un error, da un equipo que nunca avisa. Sale al PLC por
  el bit 6 de la palabra de estado de esa cámara, que ya existía en el mapa; el índice de
  nitidez es una tendencia y va por telemetría, donde sirve para ver el vidrio ensuciarse
  semanas antes de que el bit se prenda. Sin calibrar, el estado es «no disponible» y
  nunca alarma: un vidrio limpio que nadie midió no se afirma.
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
  instante en que se midió, no con el de la escritura. El hilo drena la cola de continuo,
  así que el ritmo no lo limita la cola —medido: 50 puntos/s sin descartar—: lo que se
  achica al publicar más seguido es el margen ante un backend trabado (100 puntos de cola
  = 100/N segundos a N puntos/s) y el volumen que queda guardado por día. Aun así se
  publica **por tick y no por evento**, porque una serie de salud por evento no agrega
  información. La estructura de las series —measurements, tags y nombres de campo— es un
  contrato con los dashboards y está en `docs/influxdb.md`.
- **La licencia sólo se enforcea en un build compilado.** Corriendo desde fuentes el
  estado es `unlicensed_build`, la política es `off` y no se restringe nada, con una línea
  de log para que nunca sea silencioso: sacar la validación de un `.py` es borrar un `if`,
  y fingir que protege algo sería peor que decir que no protege nada. La marca la pone
  Nuitka (`__compiled__` o `sys.frozen`) y **no hay variable de entorno ni clave de config
  que lo apague**: un `LICENSE_DEV=1` es un string en el binario y es lo primero que se
  busca. Sin compilar, todo esto es un cartel; `build/` ya compila con Nuitka.
- **Lo que ata el equipo es la huella, no los nombres.** `client` y `project_id` se
  comparan contra `project:` del config —que el cliente edita— así que son una alarma de
  archivo equivocado, no un control. El lock es el N-de-M sobre las fuentes de hardware:
  la licencia declara cuántas tienen que seguir coincidiendo, y con eso un disco o una
  placa de red reemplazados no dejan afuera al cliente que pagó. Esa es la falla que
  hunde estos esquemas, y por eso el N-de-M no es un lujo.
- **El N-de-M tiene un piso y por debajo no hay licencia.** Dos fuentes declaradas y dos
  coincidencias exigidas (`schema.FINGERPRINT_MIN_FLOOR`), y el mismo número del otro lado:
  el firmante no la emite y el equipo no la parsea. Una huella vacía coincidía con todas
  las máquinas, así que una sola licencia forjada corría en cualquier equipo —la falla que
  vuelve decorativo todo el resto del subsistema—, y una sola fuente es una sola pieza que
  reemplazar para que la licencia viaje. Un equipo que no llega a dos fuentes **no se puede
  licenciar**: la solicitud ni se genera, con el motivo, porque el problema está en el
  hardware y hay que verlo con el equipo delante y no por mail tres días después.
- **Sin licencia utilizable el equipo abre igual, en modo de puesta en marcha.** `absent`
  e `invalid` ya no frenan el arranque: son los dos estados que MÁS restringen. Ningún
  pipeline arranca y ninguna medición sale, sin importar la política por defecto —no hay
  payload firmado del que leerla, así que la decisión es del build (`NO_LICENSE_STATES`)—;
  las cámaras siguen capturando y la interfaz completa sigue disponible, empezando por la
  pestaña de licencia. La razón es práctica: un binario compilado no tiene un intérprete
  de Python detrás para generar la solicitud o instalar el archivo que vuelve si el
  programa se negara a abrir. Instalar una licencia no reinicia pipelines ni cámaras
  solo: eso se decide una vez al abrir, así que hace falta reiniciar la aplicación.
- **Con la política `degrade` el canal sigue sirviendo y lo que para es la medición.** El
  heartbeat, la salud del equipo y las palabras de estado se publican igual; los registros
  de proceso quedan con su último valor y el bit de licencia dice por qué. Bajar el Modbus
  dejaría al PLC viendo un enlace muerto, indistinguible de un cable cortado, y mandaría
  al integrador a buscar el problema equivocado.
- **El estado inválido se ve en cuatro lados a la vez, con el mismo criterio.** El chip de
  la pestaña, el bit y el registro que van al PLC, la barra de estado pintada de rojo
  —la barra entera y no un chip: en 28 px un chip se pierde, y esto no es algo que se
  pueda pasar por alto— y un cartel modal al abrir la ventana —una sola vez por corrida,
  no en cada recheck horario— salen todos de `should_report_invalid()`: ninguno puede
  decir que está mal mientras otro dice que está bien.
- **Los entitlements restringen distinto según si hay una licencia real que los declare.**
  Sin archivo utilizable (`absent`/`invalid`) el corte es total —nada arranca, nada se
  publica, sin importar la política—: no hay nada que decir qué se compró, así que lo más
  restrictivo es lo que corresponde. Con una licencia que parsea —aunque esté vencida o
  sea de otra máquina— sus entitlements se aplican tal como los declara, porque ahí sí se
  sabe qué se vendió. Una cámara sobre el cupo no desaparece de la pantalla: se publica su
  estado con el motivo, porque un hueco manda a revisar un cable que está bien.
- **Un feature por pipeline, y dos propósitos son dos pipelines.** Medir un proceso y
  vigilar una zona no se juntan aunque miren la misma cámara: son dos hilos, sus
  resultados ya se separan por el par `(cámara, pipeline)`, y se venden por separado.
  Juntarlos haría que el cliente que no compró la vigilancia pierda también la medición
  que sí pagó, porque el pipeline entero no arranca.
- **Contra el reloj atrasado, el equipo solo tiene un techo, y lo que lo completa está en
  el PLC.** El estado firmado de `data/license_state.bin` caza el intento ingenuo, pero
  atrasar la fecha y borrar ese archivo deja el equipo andando: todo lo que guarda vive en
  un disco que el mismo administrador controla. Por eso se publican dos cosas hacia
  afuera, y las dos son requisitos de la integración y no algo que el equipo imponga: el
  bit de licencia, **para que el PLC lo enclave** —el equipo puede levantar la alarma pero
  no borrarla—, y el reloj del equipo como epoch UTC en `clock_epoch_s_high` / `_low`,
  **para que el PLC lo compare con el suyo**, que es el único reloj confiable en una
  instalación sin internet. Acá no se compara ni se juzga: se publica el dato y el integrador arma la
  lógica, como con todo el resto del mapa. Sólo afecta a las licencias con vencimiento;
  una perpetua no tiene fecha que esquivar.
- **La clave privada no vive en este repo y nunca va a vivir acá.** Acá va sólo la pública,
  compilada adentro del binario: si se pudiera cargar de un archivo al lado del ejecutable,
  cualquiera pondría la suya y firmaría sus propias licencias. Hay **una sola clave** para
  todos los proyectos —lo que separa una instalación de otra es la huella, no el nombre de
  la clave— y el `key_id` es un contador (`iea-1`) sin año ni país, porque el verificador
  nunca lo valida como alcance. Rotar es de dos etapas y la segunda —sacar la clave vieja
  de la tabla— es la que cierra el agujero; el procedimiento está en `docs/licensing.md`.
  El repositorio que firma es privado y aparte; su encargo está en
  `.claude/plans/licensing-signer-repo.md`.
- **La clave pública tampoco se escribe a mano: la genera el build**, en un
  `system/license/_public_key.py` gitignoreado, partida en fragmentos enmascarados que se
  rearman al llamarla —el mismo recurso que `_model_key.py`—. Un base64 de 43 caracteres
  adentro del binario se encuentra con un volcado de strings y se reemplaza por otro del
  mismo largo, y con eso cualquiera firma sus propias licencias: la indirección no lo
  impide, lo encarece. `public_key.py` sigue versionado y es sólo la puerta que consulta
  esa tabla; viene vacío, porque un checkout de fuentes no verifica ninguna licencia y no
  tiene por qué —ahí el estado es `unlicensed_build`— y porque los tests inyectan la suya.
- **Los pesos del modelo se protegen con cifrado autenticado, no con un hash.** AES-256-GCM
  da confidencialidad e integridad en una sola pasada: si el archivo abre, es auténtico y
  está íntegro; si le cambiaron un bit, no abre. Por eso **no hay ningún hash que guardar
  ni manifiesto que mantener**, y por eso el campo `model_hashes` de la licencia queda
  vacío en las entregas normales: con el hash adentro de la licencia, cada reentrenamiento
  —que al principio de un proyecto es frecuente— obligaría a reemitirla. Con la clave
  estable, reentrenar es cifrar el modelo nuevo y copiarlo: sin reemitir y sin recompilar.
  El cifrado y la licencia son independientes y no se acoplan: uno frena la extracción del
  archivo, la otra la instalación copiada, y ninguno reemplaza al otro.
- **Que los pesos estén cifrados lo dice el archivo, no el config.** Un encabezado mágico
  lo declara y unos pesos en claro se devuelven tal cual, así que desde fuentes todo anda
  sin clave y sin configuración: la protección aparece en el build entregado y no estorba
  el desarrollo, igual que la licencia. Descifrar es del contrato —`_read_weights()` en
  `AbstractModel`— y no de cada implementación, que carga **desde bytes y nunca desde una
  ruta**: escribir el plano a un archivo temporal para que el framework lo lea tiraría a
  la basura casi todo el beneficio.
- **La metadata del modelo viaja adentro de los pesos y le gana al config.** Los nombres
  de clase, el umbral y la tarea van cifrados en el mismo archivo, porque si no el
  `config.yaml` —un archivo de texto al lado del ejecutable— cuenta qué detecta el modelo.
  Le gana al config porque viaja autenticada y el config lo edita el cliente; las dos
  únicas claves que no puede pisar son `path` y `type`, que serían un archivo diciendo
  cómo abrirse.
- **Una clave de modelo por fork, derivada y no escrita entera.** Una clave sacada del
  binario de un cliente expone el modelo de ese cliente y de ningún otro. No se guarda
  como 32 bytes seguidos —eso se encuentra con un volcado de strings incluso compilado—
  sino que se deriva con HKDF de fragmentos y un salt que el build pone en un
  `_model_key.py` gitignoreado. Sube el costo, no cierra la puerta: los pesos en claro
  existen igual en la memoria del equipo mientras el modelo infiere, y lo que esto termina
  es la extracción casual, que es la amenaza realista. La custodia y la herramienta de
  cifrado son del repositorio de firma; el encargo está en
  `.claude/plans/model-protection-signer-repo.md`.
- **Los dos backends de telemetría publican el mismo dato.** `PersistenceThread` arma el
  registro una sola vez y se lo pasa igual a InfluxDB y a MQTT, así que no hay una lista
  de campos por destino: los nombres se eligen una vez y valen para los dos. Lo que cambia
  es la forma en el cable —MQTT publica un JSON plano en `<topic_base>/<measurement>`, con
  los tags y los fields fusionados—, y eso está en `docs/mqtt.md`, que apunta a
  `docs/influxdb.md` para los campos en vez de repetirlos.
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
- **Una métrica llega al PLC si tiene una fila con su mismo nombre en el mapa.** El
  cableado publica los registros de salud y, del dict del analyzer, las claves que existen
  como registro; una que no existe no se publica y se avisa una vez. Así el nombre de la
  métrica es el contrato con el integrador y no hay una tabla de traducción que mantener.
  Dos cámaras que publican la misma métrica se pisan: ahí el nombre lleva el slot adelante.
- **El latido no espera al hardware.** La primera lectura del monitor de sistema bloquea
  unos segundos, así que el ciclo de registros publica el heartbeat y las palabras de
  estado desde el primer tick y agrega las métricas de hardware cuando llegan: un PLC que
  vigila el latido no puede quedarse esperando al primer `nvidia-smi`.
- **La app corre sin ventana con `--headless` o `ui.enabled: false`**, y el cableado no
  cambia: Qt hace falta igual porque las señales son el vínculo entre los hilos, así que
  se arma un `QCoreApplication` y la interfaz la reemplaza un no-op con la misma API —el
  mismo recurso que `NullDriver`—. `main.py` no pregunta en ningún método si hay ventana,
  y en headless el widget del monitor no se construye ni se importa. Sin nadie mirando, el
  gate del anotado queda en manos de los clientes de los streams: sin ninguno, el overlay
  no se dibuja.
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
  modelo que la fábrica todavía no registra. Lo hace `set_combo_value()`, cuando el texto
  del combo **es** el valor del config.
- **Un combo con el texto traducido guarda el valor en el item, no en la etiqueta.**
  Rotación, modo del dataset, marca de cámara y codec se arman con
  `build_value_combo_box()` y se leen con `currentData()`: reconstruir el valor desde lo
  que se ve lo ataba al idioma con el que se armó el combo, y alcanzaba con cambiar de
  idioma para que el `save()` escribiera «Sob demanda» donde iba `on_demand`. Un valor
  que no está entre las opciones sigue entrando como opción y volviendo al archivo tal
  cual, que es lo que hace `set_combo_data()`.
- **La UI no arma direcciones ni rutas de protocolo.** Las URLs de los streams le llegan
  hechas por `set_stream_urls()` desde el cableado, que las pide a cada servidor: la
  forma de la ruta es del servidor de video, y componerla en la UI la dejaría definida en
  dos lugares.

## Qué es de este proyecto y qué viene del template

La regla: **si un archivo describe cómo se hace algo, es maquinaria y se cross-portea; si
describe qué se mide en esta cinta, es de este proyecto.**

- **De este proyecto** —lo que se reescribió al migrar—: `config.yaml` (sección `process:`
  incluida), `system/modbus/register_map.yaml`, `system/inference/pipeline.py`,
  `system/inference/metrics.py`, `system/inference/annotations.py`,
  `system/inference/models/yolo_seg_model.py`, este archivo y el `README.md`.
- **Agregados que el template no tenía**: `system/gpio_control.py` y
  `system/formats/gpio_status.py` —el subsistema de GPIO, que el template declaraba
  pendiente—, y `system/inference/rolling.py`, que es la media móvil por ventana de tiempo.
  Los tres son genéricos y **valen para devolver al template**.
- **De `main.py` se completó el bloque marcado**: el analyzer, los annotators, el contexto
  del dataset y el widget del monitor —que es la grilla de cámaras, sin widget propio—.
- **`main.py` diverge además fuera de ese bloque**, y es lo que hay que mirar al
  cross-portear: el bloque de measurements de telemetría, `_build_inference_fields()`,
  `_build_optics_fields()` y `_build_system_fields()` usan los nombres viejos del dashboard;
  y el cableado del GPIO y de las medias móviles no existe en el template.
- **Todo lo demás es maquinaria** y se cross-portea sin editar. Si hay que tocarla para que
  algo de la cinta funcione, el límite está mal puesto: lo que falta es un punto de
  extensión, no un parche.

Los seis puntos de extensión del motor —`preprocessor`, `pipeline`, `classifier`,
`analyzer`, `annotator`, `annotate_gate`— entran por su constructor y los cablea `main.py`.
La tabla completa, archivo por archivo, está en `README.md`.

## Qué todavía no existe

- **La calibración de la exposición.** Es lo que bloquea la puesta en marcha: el modelo
  viene trabajando sobre una imagen amplificada por software y ahora recibe el frame crudo,
  así que hay que subir `exposure_time_us` hasta que el nivel coincida y recalibrar
  `process.dark_background_threshold` contra esa imagen. Se decide mirando la cinta.
- **El orden de las clases hay que confirmarlo contra el `.engine`.** El código de la
  versión anterior decía clase 0 = desmenuzado y su propio docstring decía lo contrario;
  `config.yaml` quedó con el orden del código. Invertirlo invierte todos los porcentajes sin
  que nada falle.
- **La referencia de óptica se recalibra** una vez fijada la exposición nueva: la que está
  (`variance: 50.7`) se midió con las condiciones viejas y deja de valer si cambia.
- **El widget del área central del monitor.** Hoy es la grilla de cámaras genérica. La
  versión anterior tenía un gráfico de áreas apiladas con la composición; si se lo quiere de
  vuelta, entra por `_build_monitor_content()` sin tocar nada más.
- **El loader nativo de TensorRT.** Hoy el modelo carga con ultralytics, que quiere una
  ruta, así que unos pesos protegidos no abren por ese camino y `load()` corta con el
  motivo. Cerrarlo es implementar `deserialize_cuda_engine(bytes)` y el postproceso de
  segmentación, con su test de paridad numérica contra la salida de ultralytics. Ver
  `docs/model_protection.md`.
- **De la licencia falta la mitad que no es código**: la clave pública real, que la genera
  el repositorio de firma al compilar, y compilar. Sin eso el estado es `unlicensed_build` y
  no se restringe nada. El diseño completo está en `.claude/plans/licensing.md`.
- La captura por trigger de software está diseñada y diferida en
  `.claude/plans/software-trigger-capture.md`.

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
