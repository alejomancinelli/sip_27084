# Estructura de la telemetría en InfluxDB

Qué series publica el equipo, con qué tags y con qué nombres de campo. Es el contrato con
los dashboards: una vez que un panel consulta un campo, renombrarlo lo rompe.

**Estado: implementado.** Las cuatro series salen por muestra, con el tag `device`, la red
aplanada y cada punto sellado con el instante en que se midió. El muestreo es de un segundo
y no se configura.

**Esto vale también para MQTT.** Los dos backends reciben el mismo registro, así que las
series, los tags y los nombres de campo de acá son los que publica el broker. Cómo se ve
eso en el cable —el tópico y el JSON— está en [`mqtt.md`](mqtt.md).

El público es quien arma los dashboards y quien verifica una instalación desde afuera:
con InfluxDB se contesta «¿esto viene midiendo bien desde que lo dejamos?» sin entrar al
equipo, que es para lo que existe la telemetría acá.

## Las cuatro series

| Serie | Qué sale por muestra |
|---|---|
| `system` — hardware | 1 punto, con la red aplanada |
| `camera` — estado por cámara | 1 por cámara |
| `services` — los seis canales | 1 punto, seis campos |
| `inference` — el resultado | 1 por par (cámara, pipeline) con resultados nuevos |

Las tres primeras responden «¿el equipo está sano?», que es lo que no se puede ver desde
afuera de otra forma.

## Cuántos puntos por segundo

**La cola no es el límite.** El hilo de telemetría la drena de continuo mientras junta el
batch, así que en régimen aguanta mucho más de lo que estas series producen: medido con un
backend doble, **50 puntos/s sin descartar uno solo**.

Lo que sí se achica al publicar más seguido son dos cosas:

1. **El margen ante un InfluxDB trabado.** La cola guarda 100 puntos y sólo se acumula
   mientras la escritura está bloqueada. A `N` puntos/s tolera `100/N` segundos de
   escritura lenta antes de empezar a descartar:

   | Puntos/s | Margen |
   |---|---|
   | 6 | ~17 s |
   | 10 | 10 s — una ventana de publicación |
   | 14 | ~7 s |
   | 50 | 2 s |

   Por debajo de una ventana de publicación de margen, **una sola escritura lenta ya pierde
   datos**. Ese es el umbral que avisa el arranque, y por eso el aviso habla de margen y no
   de tope.

2. **El volumen guardado.** 6 puntos/s son ~500 mil puntos por día; 14 son 1,2 millones. Con
   veinte campos cada uno, eso decide la retención y si hace falta downsampling del lado de
   InfluxDB. Es la razón práctica para no publicar más seguido de lo que se va a mirar.

Con 4 cámaras en un pipeline y el muestreo en 1 s: 1 de `system` + 4 de `camera` + 1 de
`services` + 4 de `inference` = **10 puntos/s**, justo en el umbral del aviso. Con 2 cámaras
son 6. El arranque hace esta cuenta —contando pares (cámara, pipeline)— y avisa si el margen
baja de una ventana de publicación.

Igual la telemetría se publica **por muestra y no por evento**, y no por la cola: una serie
de salud por evento no agregaría información, y el detalle por resultado ya va al dataset y
al PLC.

### Dos intervalos que no son lo mismo, y ninguno está en el config

Los dos se llaman «intervalo», hacen cosas distintas y los dos son constantes de código:

| | Qué controla | Dónde |
|---|---|---|
| `_SAMPLE_INTERVAL_MS` | **cada cuánto se mide** — la resolución de los datos | `main.py`, 1000 ms |
| `_PUBLISH_INTERVAL_S` | **cada cuánto se escribe** — el batch al backend | `persistence.py`, 10 s |

Entre los dos, cada batch lleva diez muestras de cada serie y el dashboard las ve hasta
10 s después de medidas. **Cada punto viaja con el instante en que se midió**, así que la
ventana de escritura no deforma la serie: sólo agrega latencia.

Ninguno es configuración, y por razones distintas:

- **El muestreo** no lo es porque no hay instalación que quiera otro: un punto por segundo
  es la resolución con la que se miran estas series. Más fino no agrega información —el
  hardware no cambia más rápido— y más grueso pierde el detalle de un pico.
- **La ventana de escritura** no lo es por decisión del módulo, que lo dice en su
  docstring: no cambia el dato, sólo cada cuánto salen las escrituras.

Si un dashboard en vivo con 10 s de retraso molesta, lo que se baja es `_PUBLISH_INTERVAL_S`
—y se pagan escrituras más chicas y más frecuentes—, no el muestreo.

## Tags

Tres, y ninguno es un literal en el código:

| Tag | De dónde sale | Para qué |
|---|---|---|
| `device` | `system.device_id` del config | distinguir equipos que comparten bucket |
| `camera` | la clave del slot (`camera_1`) | una serie por cámara, mismos campos |
| `pipeline` | la clave del slot (`pipeline_1`) | separar dos pipelines sobre la misma cámara |

**La cámara va en un tag y no en el nombre del campo.** Es la diferencia entre

    camera,camera=camera_1 fps=9.8          ← una serie por cámara, un solo panel
    system cam01_fps=9.8,cam02_fps=5.9      ← dos campos, y el panel se reescribe al sumar una

Con el tag, agregar una cámara agrega series y los dashboards no se tocan: se agrupa por
`camera`. Con el prefijo en el campo, cada cámara nueva es una consulta nueva.

## Las series

### `system` — hardware del equipo

Una por muestra, tag `device`. Los campos son los de `SystemMonitor.get_metrics()`, con el
mismo nombre: no hay traducción en el camino.

| Campo | Unidad |
|---|---|
| `cpu_usage_pct`, `gpu_usage_pct` | % |
| `cpu_temp_c`, `gpu_temp_c` | °C |
| `ram_used_mb`, `ram_total_mb` | MB |
| `disk_free_gb` | GB |
| `power_w` | W |
| `net_<iface>_rx_mbps`, `net_<iface>_tx_mbps` | Mbps |

**Ojo con los dos anidados.** `get_metrics()` devuelve `net_mbps` como
`{iface: {rx_mbps, tx_mbps}}` y `temps_c` como `{zona: °C}`, y **los fields de Influx son
escalares**: un dict no entra. Hay que aplanarlos —`net_eth0_rx_mbps`— y no filtrar por
tipo, que es la forma silenciosa de perder el tráfico de red entero.

`temps_c` no se publica: es el detalle por zona térmica que ya resumen `cpu_temp_c` y
`gpu_temp_c`, y sus nombres cambian entre equipos, así que no sirve para un dashboard
portable. Queda para el log de diagnóstico.

### `camera` — una serie por cámara

Una por cámara por muestra, tags `device` y `camera`. Campos de
`AbstractCameraDriver.get_status()` más la iluminación, que la mide la inferencia:

| Campo | Qué es |
|---|---|
| `connected` | 0/1 — hay sesión con el hardware |
| `capture_enabled` | 0/1 — se le están pidiendo frames |
| `fps_estimated` | fps medidos sobre los frames entregados |
| `temperature_c` | °C, 0 si el driver no la da |
| `illumination_pct` | brillo medio del ROI, 0–100 |
| `misconfigured` | 0/1 — el driver no se pudo construir |

`connected` y `capture_enabled` van separados a propósito: una cámara deshabilitada no es
lo mismo que una caída, y el gráfico tiene que poder distinguirlas.

### `inference` — el resultado, agregado

Un punto por par `(cámara, pipeline)` que haya producido resultados desde la muestra
anterior. Tags `device`, `camera` y `pipeline`.

**Se agrega, no se muestrea.** Con una inferencia de 200 ms y una muestra por segundo
entran cinco resultados: promediarlos usa los cinco, y el desvío dice si el proceso estuvo
estable dentro de ese segundo. Quedarse con el último tiraría cuatro de cada cinco.

| Campo | Qué es |
|---|---|
| `sample_count` | resultados **confiables** que entraron al promedio |
| `result_count` | resultados totales de la muestra |
| `invalid_count` | cuántos no eran una medición |
| `invalid_reason` | el motivo del último descarte —vocabulario `REASON_*`—, vacío si no hubo |
| `confidence_pct_mean`, `detection_count_mean` | sólo sobre los confiables |
| `inference_time_ms_mean`, `inference_time_ms_max` | sobre todos |
| `illumination_pct_mean` | sobre todos |
| `stage_<slot>_ms_mean` | por etapa del pipeline |
| `<métrica>_mean`, `_std`, `_min`, `_max` | las del analyzer, vía `analysis.summarize_metrics()` |

Qué se promedia sobre qué no es un detalle: la confianza y el conteo de detecciones **sólo
sobre los confiables**, porque promediar la confianza de un descarte da un número que no
significa nada; el tiempo y la iluminación **sobre todos**, porque valen igual y la
iluminación baja es justamente uno de los motivos de descarte.

**Los descartes se publican**: no como puntos propios, sino en `invalid_count` y
`invalid_reason`, que es lo que permite preguntar cuántas mediciones se cayeron y por qué.
Un panel que grafique la medición usa los `_mean` y mira `sample_count` para saber sobre
cuántas muestras está.

**Cada punto se sella con el instante del último resultado**, no con el de la muestra: el
valor vale por cuándo se midió. Es lo que hace el `time_s=` de `push_data()`.

Las claves de `metrics` son las del analyzer, sin renombrar: el nombre de la métrica es el
mismo en el panel del anotado, en el JSON del dataset, en los fields de acá y —cuando
tiene fila— en el registro Modbus. Una sola fuente de nombres.

### `services` — los seis canales de salida

Una por muestra, tag `device`, un campo por canal con el estado como 0/1:

| Campo |
|---|
| `modbus_tcp`, `modbus_rtu`, `video_http`, `video_rtsp`, `influxdb`, `mqtt` |

Son los mismos seis de `system/formats/com_status.py` y el 1 significa lo mismo que el
bit: **levantó y está andando**. Así el panel y la palabra que lee el PLC no pueden
discrepar.

Con `influxdb` hay una limitación obvia y vale decirla: si InfluxDB se cae, el punto que
dice que se cayó no llega. Sirve para ver cortes cortos —el hueco en la serie es el dato— y
para ver los otros cinco canales, que es lo que interesa.

### `optica` — lo agrega el fork

El template no trae salud de óptica. Un proyecto que la mida —lente sucio, nitidez— publica
su propia serie: es el caso de `sip-smart-belt-monitor`, que tiene `docs/optica_lente.md` y
una serie `optica` con la varianza, la nitidez y el veredicto de lente sucio.

## Lo que NO va a InfluxDB

| Dato | Dónde va | Por qué no acá |
|---|---|---|
| Los frames | dataset en disco, streams de video | InfluxDB es de series numéricas |
| Cada detección | JSON del dataset, al lado del anotado | son cientos por frame |
| Cada detalle de cada resultado | dataset y registros | la muestra ya trae media, desvío y rango; el detalle por frame es del dataset |
| Las zonas térmicas por nombre | log | sus nombres cambian entre equipos |

## Verificaciones típicas

```sql
-- ¿El equipo estuvo midiendo? Huecos = estuvo caído o sin publicar.
-- `result_count` es cuántos resultados entraron a cada muestra: si baja, se infiere menos.
from(bucket: "…") |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "inference" and r._field == "result_count")

-- ¿Cuántas mediciones se descartaron, y por qué?
from(bucket: "…") |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "inference" and r._field == "invalid_reason")
  |> group(columns: ["_value"]) |> count()

-- ¿Se está quedando sin disco?
from(bucket: "…") |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "system" and r._field == "disk_free_gb")

-- ¿Alguna cámara perdió fps?
from(bucket: "…") |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "camera" and r._field == "fps_estimated")
  |> group(columns: ["camera"])
```

## Publicar cada resultado

Si una instalación necesitara un punto por resultado —depurar una puesta en marcha, o un
proceso donde cada frame es una medición que no se puede promediar—, el cambio es chico y
está en un solo lugar. En `_on_result_ready()` de `main.py`, en vez de dejar el resultado
en el buzón:

    self._telemetry.push_data(
        _MEASUREMENT_INFERENCE,
        _build_inference_fields([result]),      # el mismo armador, con un solo resultado
        tags={**self._telemetry_tags,
              "camera": result.camera_slot, "pipeline": result.pipeline_slot},
        time_s=result.timestamp_s,
    )

Con un solo resultado, `sample_count` da 1, los `_std` dan 0 —que es la respuesta correcta:
no hay dispersión que reportar— y los `_mean` son el valor. **La forma de la serie no
cambia**, así que los dashboards siguen andando: es la misma consulta con más puntos.

Lo que conviene mirar antes:

| | Agregado (hoy) | Por resultado |
|---|---|---|
| Puntos/s, 2 cámaras a 5 resultados/s | 2 | 10 |
| Margen ante un InfluxDB trabado | ~17 s | ~7 s |
| Volumen por día | ~500 mil | ~1,7 millones |
| Dispersión dentro del segundo | la da `_std` | se ve en los puntos |

Y hay que sacar el buzón del camino: si se publica por resultado **y** además queda el
tick, la misma medición sale dos veces.
