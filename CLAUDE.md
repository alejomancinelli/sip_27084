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
repiten acá.

## Cómo se corre

El intérprete del proyecto es el del venv, **no** el `python` del PATH:

    .venv\Scripts\python.exe -m pytest -q          # Windows
    .venv/bin/python -m pytest -q                  # Linux

Python 3.10 de 64 bits, fijado por el wheel cp310 de stapipy. La suite completa
corre sin hardware, sin GUI y sin red: todo lo externo se reemplaza por dobles
dentro del propio archivo de test.

Lo que sí necesita hardware vive en `manual_test/<tema>/`, cada uno con su
`config.yaml` al lado. La instalación de los SDK de cámara está en
`setup/cameras/{windows,linux}/`.

## Mapa

    system/                   la app: subsistemas propios, con Qt y con I/O
      config_manager.py       nivel 0 — singleton thread-safe sobre config.yaml
      logger.py               nivel 0 — logger único; nadie crea otro
      paths.py                nivel 0 — rutas del proyecto sin depender del CWD
      system_monitor.py       métricas de hardware: CPU, RAM, disco, red, GPU
      camera/
        capture_thread.py     un hilo por cámara; entrega frames y telemetría por señales
      image_collector/
        collector.py          dataset en disco, por intervalo o a pedido
        conditions.py         módulo puro: predicados sobre el dict de inferencia
      telemetry/
        persistence.py        junta puntos en una ventana y los escribe en batch
        backends/
          abstract_backend.py el contrato: ciclo de vida, registro y vocabulario de status
          influxdb_backend.py InfluxDB 2.x
          mqtt_backend.py     paho-mqtt
      video/
        http_server.py        streams MJPEG: uno crudo y uno anotado por cámara

    tools/                    librerías portables; no conocen la app
      camera/
        abstract_driver.py    contrato del driver: always-fresh, rotación, fps medido
        camera_factory.py     único archivo que conoce las clases concretas
        basler_driver.py      pypylon
        st_driver.py          stapipy (Sentech / Omron)
        mock_driver.py        frames sintéticos, sin hardware
        null_driver.py        lo que devuelve la fábrica ante una config inválida
        camera_catalog.py     modelos por fabricante, para la UI

    test/                     espeja el árbol de arriba; conftest.py en la raíz
    manual_test/              pruebas con hardware, cada una con su config.yaml
    setup/                    instalación de SDK de cámara (en inglés, ver skill)
    packages/                 wheels que no están en PyPI (stapipy)
    data/                     logs y dataset en runtime

La dirección de las dependencias y las reglas de límites están en la skill
`modularidad`.

## Contratos que cruzan módulos

Son punteros: el contrato vive en el archivo, no acá.

- **Driver de cámara** — `tools/camera/abstract_driver.py`: entrega always-fresh y
  las claves estables de `get_status()`.
- **Backend de telemetría** — `system/telemetry/backends/abstract_backend.py`: la
  forma del registro (`measurement`, `tags`, `fields`, `time`) y los `STATUS_*`.
- **Configuración** — `system/config_manager.py`: rutas punteadas, copias en la
  entrega, config de rescate.
- **Señales de Qt** — cada una documenta su payload y su frecuencia donde se declara.

## Decisiones vigentes

Lo que no se deduce leyendo un archivo suelto:

- Las claves de `config.yaml` van en inglés y la jerarquía espeja los módulos, no
  las pantallas de la UI.
- Las credenciales nunca van al config: salen del entorno (`INFLUXDB_TOKEN`,
  `MQTT_PASSWORD`).
- **En las cámaras no se ajusta nada en caliente.** Exposición, ganancia, fps y
  `enabled` se cambian en `config.yaml` y se reinicia la app. Cuando exista la UI,
  la config se va a editar solo desde ahí.
- Un hilo por cámara, y el ritmo lo pone la cámara (free-run). Si alguna vez se
  captura por trigger, el ritmo y el orden de los disparos son de quien orqueste
  la captura, no del hilo.
- La telemetría se acumula en una ventana y sale en batch; cada punto viaja con el
  instante en que se midió, no con el de la escritura.
- Un módulo al que le falta su librería de sistema degrada a no-op adentro y expone
  la misma API. El llamador no pregunta si está disponible.
- `ui/` no lo importa nadie de `system/` ni de `tools/`.

## Qué todavía no existe

- **`main.py`**: no hay composition root; hoy nadie cablea los subsistemas entre sí.
- `system/modbus/` (servidor, schema, mapa de registros), `system/formats/`
  (bitfields de estado), `system/inference/`, `system/video/rtsp_server.py`, `ui/`.
- La captura por trigger de software está diseñada y diferida en
  `.claude/plans/software-trigger-capture.md`.

## Dónde va lo que se escribe

- El contrato de un módulo → su docstring, en la misma edición que el código.
- El mapa, las decisiones y lo que falta → este archivo.
- Un documento con público propio —mapa de registros Modbus, notas de puesta en
  marcha— → `docs/`, que nace con su primer documento.
- Convenciones → las skills. No se copian acá.

Un subsistema gana carpeta propia cuando tiene más de un archivo; hasta entonces es
un archivo suelto en `system/`.
