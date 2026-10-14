# Probar sin la línea: video grabado e imágenes sueltas

Cómo se prueba el sistema en la máquina de desarrollo, antes de tener las cámaras o la
configuración final del equipo. Es el documento del que desarrolla un fork: el que tiene
videos o fotos de la planta y quiere ver qué hace el pipeline con ellos.

Hay dos herramientas y contestan preguntas distintas:

| Herramienta | Qué contesta | Cómo se usa |
|---|---|---|
| **Video como cámara** (`driver: video_file`) | ¿el sistema entero anda con imagen de la planta? Captura, inferencia, overlay, streams, PLC, telemetría, UI | se declara la cámara en el `config.yaml` y se levanta la app |
| **Imágenes sueltas** (`manual_test/offline_inference/`) | ¿qué hace el pipeline con *esta* imagen? Detecciones, métricas, anotado | un script, sin levantar la app |

Las dos corren el mismo código que la línea. El driver de video entrega frames por el
mismo contrato que una cámara, y las imágenes sueltas pasan por el mismo ciclo del motor
—preprocessor, ROI, compuertas, pipeline, classifier, analyzer y overlay— con los mismos
hooks del proyecto (`system/inference/hooks.py`). No hay un camino de prueba que pueda
dar distinto que el de producción.

## Video grabado como cámara

### Cómo tienen que ser los videos

Grabarlos queda fuera del template; lo que importa es que se parezcan a lo que entrega la
cámara, porque el sistema los va a medir como si lo fueran.

- **Frames crudos del sensor.** Sin overlay, sin recorte y sin ajuste de brillo: el ROI,
  el preprocessor y el anotado los pone el sistema. Un video grabado del stream
  `annotated` mide encima de sus propias cajas.
- **La resolución de la cámara.** El ROI, `process:` y las detecciones están en píxeles:
  un video reescalado da números creíbles y equivocados.
- **Sin rotar, o con la cámara en `rotation: null`.** La rotación de la cámara se aplica
  a lo que entrega el driver; un video que ya sale rotado se rotaría dos veces.
- **Contenedor y codec**: H.264 en `.mp4` o MJPG en `.avi` andan en cualquier equipo con
  el OpenCV del venv. También se toman `.mkv` y `.mov`.
- **El fps del contenedor tiene que ser el real.** Es el ritmo al que se reproduce. Si el
  archivo no lo informa, se usa `acquisition.fps_limit` (o 15) y el log lo avisa.

### Dónde van

En `recordings/` de la raíz. La carpeta está en el repo y su contenido no: los videos
pesan y se versionan afuera, igual que los pesos del modelo.

    recordings/
      camera_1/            una carpeta por cámara cuando cada una ve su propia escena
        turno_manana.mp4
        turno_tarde.mp4
      camera_2/
        cinta_vacia.mp4

### Cómo se declara la cámara

En la sección de la cámara del `config.yaml`, el driver y la ruta. El resto de la sección
—ROI, calibración, `image_adjust`, `illumination_min`, `process:`— queda como el de la
cámara real, que es justamente lo que se quiere probar.

```yaml
cameras:
  camera_1:
    driver: video_file
    address: recordings/camera_1   # un archivo o una carpeta; relativa a la raíz del repo
    rotation: null
    acquisition:
      fps_limit: 0                 # 0 = el fps del video
```

Desde la pantalla de configuración es la marca «Video grabado». Como todo lo de la cámara,
se aplica al reiniciar la app.

Para ver un video antes de levantar el sistema, el visor de cámaras sirve igual: tiene una
entrada `replay` en `manual_test/cameras/config.yaml`.

    .venv\Scripts\python.exe manual_test\cameras\camera_live_view.py replay

### Qué esperar

- **Loop infinito.** Una carpeta se reproduce entera en orden alfabético y vuelve a
  empezar; un archivo solo, igual. Un video que no abre se saltea con un aviso, y un video
  agregado a la carpeta entra en la próxima reconexión.
- **Al ritmo del reloj, como una cámara en vivo.** Si la inferencia es más lenta que el
  video, se pierden los frames del medio en vez de estirar el video: un minuto de video
  dura un minuto. Con la captura apagada —un ciclo de medición que la libera entre
  rondas— el video sigue corriendo, como una cámara que filmó mientras nadie miraba.
- **Marcado como sintético.** El estado de la cámara que llega al PLC lo dice: la imagen es
  real, pero no es la línea en este momento. `temperature` queda en 0.
- **Varias cámaras sobre la misma carpeta ven lo mismo**, casi en sincronía. Sirve para
  ver la carga de N cámaras, pero no para probar nada que dependa de que vean escenas
  distintas: el ROI o la escala de una cámara se aplicarían sobre la vista de otra.

### Qué no se prueba con video

- **La salud de la óptica.** La compresión baja la nitidez y mete bloques, así que el
  índice de nitidez de un video no dice nada del vidrio. **No se calibra la referencia de
  lente sobre un video**: quedaría una referencia que la cámara real nunca alcanza.
- **La confianza exacta del modelo.** Un modelo entrenado sobre frames sin comprimir
  puede dar otra confianza sobre H.264. Para comparar números, imágenes sin pérdida.
- **El transporte de una cámara IP.** El driver lee el archivo, no la red: la reconexión y
  las credenciales del `RtspDriver` se prueban contra una cámara.

## Imágenes sueltas

### Cómo se corre

Las imágenes van en `manual_test/offline_inference/images/` (su contenido no se versiona) y
se corre:

    .venv\Scripts\python.exe manual_test\offline_inference\run_images.py
    .venv/bin/python manual_test/offline_inference/run_images.py

Sin argumentos usa el `config.yaml` de esa carpeta, que corre el modelo mock: sirve para
ver que todo anda en cualquier equipo. **Para probar el modelo del fork se le pasa el
config de la app**, que es la prueba que importa:

    run_images.py --config config.yaml

| Opción | Qué hace |
|---|---|
| `--config ruta` | config con los pipelines, los modelos, las cámaras y `process:` |
| `--images ruta` | otra carpeta de imágenes |
| `--camera slot` | cámara de las imágenes sueltas; por defecto la primera de `cameras:` |
| `--preprocessed` | las imágenes ya son frame de referencia: no se les pasa el preprocessor |

### De qué cámara es cada imagen

Importa, porque la cámara decide el ROI, el mínimo de iluminación, el preprocessor,
`process:` y qué pipelines la reciben. Las imágenes sueltas en `images/` son de la cámara
de `--camera`; las de una subcarpeta con el nombre de una cámara son de esa cámara.

    images/
      foto_1.png           de --camera, o de la primera cámara del config
      camera_2/
        foto_2.png         de camera_2

Una subcarpeta con otro nombre se saltea y se avisa. Se toman `.png .jpg .jpeg .bmp .tif
.tiff`.

### Qué deja

Cada corrida escribe en `manual_test/offline_inference/output/<fecha>_<hora>/`, así una
corrida no pisa a la anterior y se pueden comparar:

    pipeline_1/camera_1/foto_1.png     el anotado, igual al que se vería en vivo
    pipeline_1/camera_1/foto_1.json    el resultado completo: detecciones, métricas, motivo
    summary.csv                        una fila por imagen y pipeline, métricas en columnas
    run.json                           config, carpeta, cámara y qué modelos cargaron

Dos imágenes con el mismo nombre que caen en la misma carpeta de salida —`a.png` y
`a.jpg`— salen como `a` y `a_2`; la columna `output` del resumen dice cuál es cuál.

### Cómo leer el resultado

- **`no_model`** en todo un pipeline: un modelo no cargó. La consola lo dice antes de la
  primera imagen, con el motivo; casi siempre es el `path` de los pesos.
- **`dark_frame`**: el brillo del ROI quedó por debajo de `illumination_min` de la cámara
  y el modelo ni corrió, igual que en la línea. Con el config de la app el mínimo es el de
  la instalación, que puede ser más alto que el de la prueba.
- **`low_confidence` / `few_detections`**: el modelo corrió, pero el resultado no pasa los
  umbrales del pipeline (`min_result_confidence_pct`, `min_detections`). Las cajas están
  igual en el anotado.
- **`is_synthetic: true`**: el pipeline tiene una etapa con el modelo mock.

### Imágenes del dataset

El dataset guarda el frame de referencia —ya corregido de lente y ya ajustado de brillo—,
así que una imagen sacada de `data/dataset/.../original/` va con `--preprocessed`. Sin eso,
una cámara con `image_adjust` le multiplica el brillo dos veces y una con calibración le
corrige el lente dos veces, y el resultado no es el que dio en la línea.

## Lo que falta

El **ground truth** —imágenes con el resultado esperado, versionadas en el fork, y un
comando que dice cuáles pasan— está diseñado y postergado. El diseño está en
`.claude/plans/replay-and-ground-truth.md` y se apoya en el mismo runner que usan las
imágenes sueltas.
