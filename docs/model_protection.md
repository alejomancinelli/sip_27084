# Protección de los pesos del modelo

Cómo se protege un modelo, cómo se reemplaza en un equipo ya entregado y qué hacer
cuando no carga. Es el documento del que arma la entrega y del que atiende un llamado de
«el modelo no arranca».

**Estado: implementado y sin usar en producción.** El mecanismo es
`system/inference/models/encrypted_weights.py` y ya está en el contrato del modelo, pero
como la licencia le falta lo que no es código: la **clave del fork** —que la genera y la
custodia el repositorio de firma— y un **build compilado**. Desde el código fuente los
pesos van en claro y todo anda igual.

## Qué protege, y qué no

| Amenaza | Lo detiene |
|---|---|
| El cliente **extrae los pesos** y los usa en otro programa, o se los pasa a un tercero | **Esto**: los pesos cifrados |
| El cliente **copia la instalación entera** a un segundo equipo | La **huella de la licencia** (`docs/licensing.md`) |

Los dos mecanismos son independientes y **ninguno reemplaza al otro**: el cifrado no
frena la instalación copiada —el binario copiado descifra igual— y la licencia no frena
la extracción de los pesos, que están ahí a la vista. El subsistema de licencia no
pregunta por los pesos y esto no pregunta por la licencia.

**Lo que no logra, dicho de frente:** la aplicación descifra para poder inferir, así que
en tiempo de ejecución los pesos en claro están en la memoria de un equipo que administra
el cliente. Quien enganche un debugger y vuelque el buffer los obtiene. Lo que sí compra
es que **la extracción casual deje de funcionar por completo**: ya no hay un `.pt` para
arrastrar a un pendrive, y sacarlos pasa a requerir una persona con ganas y con oficio.
Contra la amenaza realista —el cliente o su integrador que se copia un archivo porque
estaba a mano— eso es casi todo el valor.

Hay una tercera protección que sale gratis y conviene tener presente: **un `.engine` de
TensorRT ya está atado al equipo por construcción**, porque depende de la arquitectura de
GPU y de la versión de TensorRT y no carga en otra máquina. Pero no se puede contar con
ella: el mismo template corre en Windows sobre `.pt` o `.onnx`, y ahí no hay nada
equivalente.

## Cómo se ve un modelo protegido

Un archivo con el sufijo `.enc` al final del nombre original —`best.engine` pasa a ser
`best.engine.enc`— para que se vea de un vistazo qué está protegido y qué no. **El
programa no mira el nombre**: mira los ocho bytes del encabezado, así que renombrar el
archivo no engaña a nadie ni en un sentido ni en el otro.

En el `config.yaml` **no cambia nada**: `inference.models.<slot>.path` sigue apuntando al
archivo de pesos, cifrado o no. No hay una clave de config que diga si está protegido,
porque eso lo dice el archivo mismo.

Adentro del archivo van los pesos **y la metadata del modelo** —nombres de clase, umbral
de confianza, tarea—, cifrados los dos. Que la metadata viaje adentro es lo que evita que
el `config.yaml`, que es un archivo de texto al lado del ejecutable, cuente qué detecta el
modelo y con qué umbral.

**Lo que viene adentro de los pesos gana sobre el config.** La metadata viaja cifrada y
autenticada; el config lo edita el cliente. Con los pesos en claro no hay metadata y manda
el config, que es el caso de desarrollo. Las dos únicas claves que la metadata **no**
puede pisar son `path` y `type`: un archivo que declarara desde dónde se carga y con qué
clase sería un archivo diciendo cómo abrirse.

## Proteger un modelo

La herramienta de cifrado **vive en el repositorio de firma** —el mismo que custodia la
clave privada de las licencias—, no en este repo: este se forkea y se entrega, y una
herramienta de cifrado acá viajaría al cliente. El encargo completo de esa herramienta
está en `.claude/plans/model-protection-signer-repo.md`.

    licensing> python -m licensing protect-model \
        --key-label acme-linea3-1 \
        --weights best.engine \
        --task detection \
        --class-names tornillo tuerca arandela \
        --min-confidence-pct 40 \
        --out best.engine.enc

Sale un `best.engine.enc` que sólo abre el build de ese cliente.

## Reemplazar el modelo en un equipo entregado

Es el flujo de un reentrenamiento, que es frecuente sobre todo al principio de un
proyecto:

1. Se entrena y se valida el modelo nuevo (fuera de esto).
2. Se cifra con la clave del fork, en el repositorio de firma.
3. Se copia el `.enc` al equipo, reemplazando el anterior.
4. Se reinicia la aplicación.

**Sin reemitir licencia y sin recompilar.** Ése es justamente el motivo por el que el hash
del modelo *no* va en la licencia aunque el formato tenga el campo: con el hash adentro,
cada reentrenamiento obligaría a reemitir la licencia de la planta.

Un cambio en los nombres de clase o en el umbral tampoco toca el config del equipo: van
adentro del archivo nuevo.

## La clave

**Una por fork.** Cada instalación ya se compila para su cliente, así que una clave por
fork sale casi gratis, y la propiedad que da es la que importa: una clave sacada del
binario de un cliente expone el modelo de ese cliente y de ningún otro.

**No se guarda como una constante de 32 bytes**, porque un blob así se encuentra con un
volcado de strings incluso en código compilado. Se deriva en tiempo de ejecución con
HKDF-SHA256 sobre fragmentos separados más el salt del fork. Eso **sube el costo, no
cierra la puerta**: alguien con oficio la recupera igual; lo que evita es el caso fácil,
que es el que va a pasar.

### Cómo entra al build

Un módulo que **genera el build** en `system/inference/models/_model_key.py`, que Nuitka
compila junto con el resto. Está en el `.gitignore` y **nunca se commitea**, igual que la
clave privada de la firma. Su forma es:

```python
KEY_LABEL = "acme-linea3-1"      # sólo para el log; no es un secreto
SALT = b"...16 bytes..."
FRAGMENTS = (b"...", b"...", b"...")
```

Sin ese módulo el desarrollo no cambia: los pesos en claro cargan igual y los cifrados
fallan con el motivo. Un equipo de desarrollo no tiene por qué manejar la clave de nadie.

### Custodia y rotación

La clave de cada fork vive en el repositorio de firma, en la fila de ese cliente, junto a
su licencia emitida. Ahí ya hay un lugar con la disciplina correcta.

Si una clave se filtra se expone el modelo de ese cliente y de ninguno más. La respuesta
es: clave nueva, recifrar los pesos, rebuild y reentrega. **No hay nada que revocar a
distancia**, y conviene saberlo antes de necesitarlo.

## Cargar desde memoria: la condición de que todo esto sirva

`AbstractModel._read_weights()` devuelve **bytes**, y la implementación concreta carga
desde ahí. Escribir el plano a un archivo temporal para que el framework lo lea tiraría a
la basura casi todo el beneficio, así que **no se hace**.

| Framework | Cómo se carga desde memoria |
|---|---|
| **TensorRT** | `runtime.deserialize_cuda_engine(data)` |
| **PyTorch** | `torch.load(io.BytesIO(data), map_location=...)` |
| **ONNX Runtime** | `onnxruntime.InferenceSession(data)` |
| **Ultralytics `YOLO(path)`** | quiere una ruta: hay que construir desde el checkpoint a mano |
| **OpenCV DNN** | según la versión; hay que verificarlo en el equipo |

**Antes de escribir el modelo del fork hay que confirmarlo contra el framework real y en
la máquina real**, y para eso está la prueba manual de más abajo. Si el framework elegido
no puede, la decisión no es inventar una tercera vía: es elegir entre descifrar a un
archivo temporal —protección mucho menor, honesta de declarar— o un directorio en memoria
(`/dev/shm` en Linux; en Windows no hay equivalente limpio).

Ejemplo de un `load()` que carga desde memoria:

```python
def load(self):
    if self.is_loaded:
        return
    weights = self._read_weights(self._get_model_path())
    if not weights:
        return                      # el motivo ya está en `error`, como con _verify_task()
    self._session = onnxruntime.InferenceSession(weights)
    self._set_loaded()
```

## Qué se ve cuando algo anda mal

Un modelo que no puede abrir sus pesos **no carga**, queda en estado `error` con el
motivo, y su pipeline marca cada resultado como no confiable. El motivo sale por el log y
por el estado del modelo, y distingue entre estas cuatro cosas:

| Lo que se lee | Qué pasó | Qué hacer |
|---|---|---|
| «este build no trae la clave para abrirlos» | Pesos cifrados en un build sin clave: casi siempre es correr desde fuentes | Usar los pesos en claro, o el build entregado |
| «o están cifrados con otra clave, o el archivo fue alterado» | Los pesos son de otro cliente, o el archivo se corrompió al copiarlo | Volver a copiar; si insiste, pedir el archivo cifrado con la clave de este equipo |
| «está cortado» | La copia quedó a medias | Volver a copiar |
| «formato de versión N» | El archivo lo cifró una herramienta más nueva que el programa | El equipo necesita una versión más nueva del software |

No hay ningún hash que comparar ni manifiesto que mantener: si el archivo abre, es
auténtico y está íntegro; si le cambiaron un bit, no abre. Eso lo da el cifrado
autenticado (AES-256-GCM) y es la razón por la que este diseño reemplazó al «hash del
modelo» que planteaba el roadmap.

## Probarlo sin hardware

    .venv\Scripts\python.exe manual_test\model_protection\check_protected_weights.py

Cifra unos pesos, los abre en memoria y muestra el contenedor, el costo de abrirlos —el
pico de memoria es lo que decide si un modelo grande se descifra de una sola vez—, las
cuatro fallas de arriba y el camino completo de la app. Con `--weights <archivo>`
apuntando al modelo de verdad, además **contesta si ese framework carga desde memoria en
esa máquina**, que es la pregunta que decide si todo esto sirve. Los escenarios están
comentados adentro del script y de su `config.yaml`.

Los tests automáticos del formato y de la lectura están en
`test/inference/models/test_encrypted_weights.py`, `test_model_key.py` y
`test_abstract_model.py`, y corren sin GPU y sin clave.

## Verificar lo que produjo el repositorio de firma

Lo de arriba prueba que el formato anda; **esto prueba que las dos puntas producen y
esperan los mismos bytes**, que es lo que hay que comprobar antes de la primera entrega y
cada vez que se toque la herramienta de cifrado.

1. En el repositorio de firma, para el cliente que corresponda:

       python -m licensing model-keygen --client ACME --project-id LINEA_3
       python -m licensing model-keymodule --key-label acme-linea3-1 --out <ruta>
       python -m licensing protect-model --key-label acme-linea3-1 --weights best.engine \
           --task detection --class-names tornillo tuerca arandela --out best.engine.enc

   El `--out` del segundo comando es `system/inference/models/_model_key.py` de este repo:
   es el módulo que compila el build, y dejarlo ahí es lo que hace que la prueba corra con
   la clave de verdad.

2. Acá, sin cifrar nada:

       .venv\Scripts\python.exe manual_test\model_protection\check_protected_weights.py ^
           --enc <ruta>\best.engine.enc

   Abre el archivo con la clave del build, muestra la metadata que el firmante puso
   adentro y corre el camino completo de la app. Con `--key-hex <clave>` se puede probar
   sin instalar el módulo, pero entonces se está probando el formato y no el build.

3. Qué tiene que decir el reporte:

   - **Que abrió.** Si no abre y la clave es la que corresponde, las dos puntas no están
     de acuerdo en el formato: comparar contra el docstring de `encrypted_weights.py`,
     que es la especificación normativa.
   - **La metadata es la que se pidió** —tarea, nombres de clase, umbral—. Que abra no
     dice que se haya cifrado el modelo correcto; esto sí. Si el reporte avisa que hay
     claves que el producto ignora, o sobran o están mal escritas.
   - **El nombre de la clave** que aparece en el log es el del cliente que corresponde.
   - **Con `--enc` apuntando al modelo de verdad**, el bloque de framework carga los bytes
     descifrados con TensorRT, PyTorch u ONNX Runtime. Ahí queda probada la cadena entera:
     cifrado allá, descifrado acá, cargado por el framework, sin tocar el disco. Para eso
     el framework tiene que estar instalado **en el venv del proyecto**, que es con el que
     hay que invocar el script.
   - **Con unos pesos que no son de detección, el modelo de la prueba queda en error y
     está bien.** El que corre la prueba es el mock, que es un detector, así que un
     clasificador dispara la guarda de tarea; el reporte lo marca como ESPERADO y la
     corrida no falla. Eso prueba la guarda, no el cifrado — que ya quedó probado dos
     bloques más arriba, cuando el archivo abrió y se leyó su metadata.
   - **El umbral sale del config si no se protegió.** `protect-model` sin
     `--min-confidence-pct` deja ese número afuera del archivo, y el reporte lo dice:
     «del config». Si tiene que viajar protegido, se pasa al cifrar.

4. **Se borra el `_model_key.py` después del build.** No se commitea nunca; el
   `.gitignore` lo cubre, pero el archivo igual no tiene por qué quedar dando vueltas.

## El formato, para quien tenga que escribirlo

Está especificado byte por byte en el docstring de
`system/inference/models/encrypted_weights.py`, que es el único archivo del repo que lo
conoce, y repetido en el encargo del repositorio de firma. Resumido:

    magic 8 · version 1 · nonce 12 · ciphertext (con el tag de 16 al final)

El encabezado entra como dato autenticado del propio GCM, así que tampoco se puede editar.
Adentro del texto cifrado van el largo de la metadata (4 bytes), la metadata en JSON y los
pesos. **El nonce es al azar y no se reusa nunca con la misma clave**: reusarlo rompe la
confidencialidad de los dos archivos, y es el error clásico de estos esquemas.
