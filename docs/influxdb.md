# Estructura de la telemetría en InfluxDB

Qué series publica este equipo, con qué tags y con qué nombres de campo. Es el contrato con
los dashboards: una vez que un panel consulta un campo, renombrarlo lo rompe.

El público es quien arma los dashboards y quien verifica una instalación desde afuera: con
InfluxDB se contesta «¿esto viene midiendo bien desde que lo dejamos?» sin entrar al equipo,
que es para lo que existe la telemetría acá.

> **Esta estructura NO es la del template.** El dashboard de la cinta ya existía cuando el
> proyecto se migró, así que se conservaron los measurements y los nombres de campo de la
> versión anterior —varios en castellano— en vez de renombrar las series. Una serie
> renombrada no se migra: la historia queda con el nombre viejo y el panel deja de
> encontrarla. La estructura canónica, que es la que usan los **proyectos nuevos**, está en
> [`influxdb_template.md`](influxdb_template.md), con la tabla de diferencias.
>
> Todo lo que diverge vive en un solo bloque de `main.py` —el de los `_MEASUREMENT_*`— y en
> `_build_inference_fields()`, `_build_optics_fields()` y `_build_system_fields()`.

**Esto vale también para MQTT.** Los dos backends reciben el mismo registro, así que las
series, los tags y los nombres de campo de acá son los que publica el broker. Cómo se ve
eso en el cable —el tópico y el JSON— está en [`mqtt.md`](mqtt.md).

## Las cinco series

| Serie | Qué sale por muestra |
|---|---|
| `sistema` — hardware | 1 punto, con la red aplanada |
| `camara` — estado por cámara | 1 por cámara |
| `optica` — salud del vidrio | 1 por cámara |
| `servicios` — los seis canales | 1 punto, seis campos |
| `inferencia` — el resultado | 1 por par (cámara, pipeline) con resultados nuevos, **más** 1 con las medias de la hora |

Las tres primeras responden «¿el equipo está sano?», que es lo que no se puede ver desde
afuera de otra forma.

## Tags

Tres, y ninguno es un literal en el código:

| Tag | De dónde sale | Para qué |
|---|---|---|
| `proyecto` | `project.project_id` del config (`27084`) | distinguir equipos que comparten bucket |
| `camara_id` | la clave del slot (`camera_1`) | una serie por cámara, mismos campos |
| `pipeline` | la clave del slot (`pipeline_1`) | separar dos pipelines sobre la misma cámara |

**La cámara va en un tag y no en el nombre del campo.** Con el tag, agregar una cámara
agrega series y los dashboards no se tocan: se agrupa por `camara_id`. Con el prefijo en el
campo, cada cámara nueva es una consulta nueva.

## Los dos intervalos, y ninguno está en el config

| | Qué controla | Dónde |
|---|---|---|
| `_SAMPLE_INTERVAL_MS` | **cada cuánto se mide** — la resolución de los datos | `main.py`, 1000 ms |
| `_PUBLISH_INTERVAL_S` | **cada cuánto se escribe** — el batch al backend | `persistence.py`, 10 s |

Entre los dos, cada batch lleva diez muestras de cada serie y el dashboard las ve hasta 10 s
después de medidas. **Cada punto viaja con el instante en que se midió**, así que la ventana
de escritura no deforma la serie: sólo agrega latencia.

## Las series

### `sistema` — hardware del equipo

Una por muestra, tag `proyecto`. Los campos son los de `SystemMonitor.get_metrics()`, con el
mismo nombre.

| Campo | Unidad | Tipo |
|---|---|---|
| `cpu_usage`, `gpu_usage` | % | entero |
| `temp_cpu`, `temp_gpu` | °C | entero |
| `ram_mb` | MB usados | entero |
| `ram_total_mb` | MB | entero |
| `disk_gb` | GB libres | entero |
| `power_w` | W | entero |
| `rx_eth0`, `tx_eth0`, `rx_eth1`, `tx_eth1` | Mbps | entero |

**Los nombres no son los de `SystemMonitor.get_metrics()`**, que los llama `cpu_usage_pct`,
`ram_used_mb`, `disk_free_gb`, `cpu_temp_c` y `gpu_temp_c`. La traducción está en
`_LEGACY_SYSTEM_FIELDS` de `main.py`, y existe por lo mismo que todo el resto de esta
divergencia: son los nombres con los que la serie ya está en el bucket.

**Toda la serie es entera**, incluidos los Mbps de red. `ram_total_mb` es el único campo
que la versión anterior no publicaba.

**Los campos de red llevan la etiqueta y no el nombre de la interfaz.** El dashboard conoce
las interfaces por su papel —`eth0` es la de cámaras, `eth1` la del PLC— y no por el nombre
que les puso el sistema operativo, que además cambia al reinstalar. La traducción está en
`telemetry.influxdb.legacy_net_labels` del config:

```yaml
legacy_net_labels:
  enP1p1s0: eth0
  enP8p1s0: eth1
```

Una interfaz sin etiqueta declarada sale con su nombre crudo, que es mejor que no salir.

`temps_c` no se publica: es el detalle por zona térmica que ya resumen `cpu_temp_c` y
`gpu_temp_c`, y sus nombres cambian entre equipos. Queda para el log de diagnóstico.

### `camara` — una serie por cámara

Una por cámara por muestra, tags `proyecto` y `camara_id`.

| Campo | Qué es |
|---|---|
| `connected` | 0/1 — hay sesión con el hardware |
| `capture_enabled` | 0/1 — se le están pidiendo frames |
| `misconfigured` | 0/1 — el driver no se pudo construir |
| `fps` | fps medidos sobre los frames entregados |
| `temperatura` | °C, 0 si el driver no la da |

`connected` y `capture_enabled` van separados a propósito: una cámara deshabilitada no es lo
mismo que una caída, y el gráfico tiene que poder distinguirlas.

### `optica` — salud del vidrio

Una por cámara por muestra, tags `proyecto` y `camara_id`. Mide la nitidez del ROI —la
varianza del Laplaciano, que el polvo sobre el vidrio hace caer— contra la referencia
calibrada de esa cámara.

| Campo | Qué es |
|---|---|
| `estado` | 0 = limpio, 1 = alarma de lente sucio, 2 = no disponible |
| `nitidez_max_pct` | nitidez máxima de la ventana, en % de la referencia |
| `muestras` | mediciones que hay en la ventana |
| `referencia` | varianza calibrada con el vidrio limpio |
| `varianza_max` | máximo de la ventana — sólo si hubo alguna medición |
| `varianza`, `nitidez_pct`, `luma` | de la última medición — sólo si hubo alguna |

**Los cuatro últimos son condicionales a propósito**: una cámara caída deja de publicarlos
en vez de repetir el último valor bueno. El hueco en la serie dice que no se midió; un
número repetido, no.

El valor de esta serie es la **tendencia**: deja ver el vidrio ensuciarse semanas antes de
que el bit del PLC se prenda. El bit es el aviso; esto es lo que permite programar la
limpieza.

### `inferencia` — el resultado

Es la única serie que sale con **dos puntos por muestra**, y es a propósito.

**Punto 1 — la medición**, uno por par `(cámara, pipeline)` que haya producido resultados
desde la muestra anterior. Tags `proyecto`, `camara_id` y `pipeline`.

| Campo | Qué es |
|---|---|
| `pct_pellet`, `pct_desmenuzado` | % del **frame** cubierto por cada clase |
| `pct_fondo` | % del frame que es cinta a la vista |
| `pct_carga` | % del **ROI de cinta** cubierto; pasa de 100 si hay desborde |
| `pct_pellet_norm`, `pct_desmenuzado_norm` | la composición: entre las dos suman 100 |
| `confianza` | confianza media, sólo sobre los resultados confiables |
| `iluminacion` | brillo medio del frame, 0–100 |
| `inference_time_ms` | duración media del ciclo de inferencia |
| `frames` | cuántos resultados entraron a la muestra |
| `invalid_count` | cuántos no eran una medición |
| `invalid_reason` | el motivo del último descarte, vacío si no hubo |

**Los dos denominadores son distintos a propósito.** `pct_pellet` y `pct_desmenuzado` se
dividen por el frame completo; `pct_carga`, por el rectángulo de cinta de
`process.belt_roi_px`. Por eso `pct_pellet + pct_desmenuzado` no da `pct_carga`, y por eso
la carga puede pasar de 100 % sin que ningún porcentaje por clase lo haga.

**Los tipos importan, y acá se pagan caro.** Todos los `pct_*` de este punto son
**enteros**, igual que `confianza` y `iluminacion`; sólo `inference_time_ms` es decimal. Los
de la ventana de una hora, en cambio, sí son decimales.

InfluxDB **fija el tipo de cada campo con el primer punto que lo trae**, y estos los fijó la
versión anterior del equipo. Mandar un decimal donde hay un entero no convierte nada: el
backend contesta `422 field type conflict` y **descarta el batch entero**, así que se pierden
también los puntos de las otras series que viajaban con él. Los tipos están afirmados campo
por campo en `test/test_main.py`.

**Punto 2 — la ventana de una hora**, uno por muestra, con tag `proyecto` solamente.

| Campo | Qué es |
|---|---|
| `pct_pellet_norm_1h`, `pct_desmenuzado_norm_1h` | media de la hora, **sólo con material en la cinta** |
| `pct_carga_1h` | media de la hora, sobre **todas** las mediciones |
| `cobertura_pct` | cuánto de la hora se llegó a medir — valida `pct_carga_1h` |
| `material_presente_pct` | cuánto de lo medido tenía material — valida los otros dos |
| `inference_age_s` | segundos desde la última medición; `65535` mientras no hubo ninguna |

**Este punto sale en todos los ticks, aunque no se haya medido nada**, y es lo que lo
distingue del primero. Si saliera sólo con la medición, el día que la inferencia se cae
dejarían de llegar los dos y el dashboard mostraría el último valor bueno para siempre. Acá
lo que cuenta la historia es el desplome de `cobertura_pct` y la subida de
`inference_age_s`.

**Por qué la composición sólo promedia con material.** En una cinta vacía la composición no
es 50/50 ni 0/0: no existe. Meterla en el promedio lo corre hacia donde no hay proceso. La
carga sí promedia todo: ahí el 0 es una medición legítima, y es justamente el dato de que la
cinta estuvo parada.

Las claves de la medición son las del analyzer, sin renombrar: el nombre de la métrica es el
mismo en el panel del anotado, en el JSON del dataset, en los fields de acá y —cuando tiene
fila— en el registro Modbus. Una sola fuente de nombres.

### `servicios` — los seis canales de salida

Una por muestra, tag `proyecto`, un campo por canal con el estado como 0/1:

| Campo |
|---|
| `modbus_tcp`, `modbus_rtu`, `video_http`, `video_rtsp`, `influxdb`, `mqtt` |

Son los mismos seis de `system/formats/com_status.py` y el 1 significa lo mismo que el bit:
**levantó y está andando**. Así el panel y la palabra que lee el PLC no pueden discrepar.

Con `influxdb` hay una limitación obvia y vale decirla: si InfluxDB se cae, el punto que dice
que se cayó no llega. Sirve para ver cortes cortos —el hueco en la serie es el dato— y para
ver los otros cinco canales, que es lo que interesa.

## Lo que NO va a InfluxDB

| Dato | Dónde va | Por qué no acá |
|---|---|---|
| Los frames | dataset en disco, streams de video | InfluxDB es de series numéricas |
| Cada detección | JSON del dataset, al lado del anotado | son cientos por frame |
| Las zonas térmicas por nombre | log | sus nombres cambian entre equipos |

## Verificaciones típicas

```sql
-- ¿El equipo estuvo midiendo? Huecos = estuvo caído o sin publicar.
from(bucket: "27084") |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "inferencia" and r._field == "frames")

-- ¿Se puede confiar en las medias de la hora?
from(bucket: "27084") |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "inferencia" and r._field == "cobertura_pct")

-- ¿Hace cuánto que no mide?
from(bucket: "27084") |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "inferencia" and r._field == "inference_age_s")

-- ¿Se está ensuciando el vidrio?
from(bucket: "27084") |> range(start: -30d)
  |> filter(fn: (r) => r._measurement == "optica" and r._field == "nitidez_max_pct")

-- ¿Se está quedando sin disco?
from(bucket: "27084") |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "sistema" and r._field == "disk_gb")

-- ¿La cámara perdió fps?
from(bucket: "27084") |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "camara" and r._field == "fps")
  |> group(columns: ["camara_id"])
```
