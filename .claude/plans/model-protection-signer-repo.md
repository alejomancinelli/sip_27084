# Encargo: cifrado de modelos en el repositorio de firma

**Este documento se copia al repositorio `licensing` y se ejecuta ahí.** Está escrito
para que otra instancia lo tome sin ver el repo del producto: el formato está
especificado byte por byte más abajo, no por referencia.

Complementa a `licensing-signer-repo.md`, que es el encargo del repositorio completo. Lo
que sigue **agrega** una segunda capacidad a esa misma herramienta: además de firmar
licencias, custodia las claves con las que se cifran los modelos y produce los archivos
de pesos protegidos.

El lado que descifra ya existe y está en el repo del producto, en
`system/inference/models/encrypted_weights.py` y `model_key.py`. **Este repositorio no
puede cambiar el formato**: lo consume un binario que ya está instalado en una planta.

---

## 1. Qué problema resuelve, en una línea

Los pesos del modelo son lo más caro de reproducir de todo lo que se entrega —el dataset
etiquetado y las semanas de entrenamiento no se rehacen con ayuda— y hoy viajan como un
archivo suelto que se copia a un pendrive en segundos. **Esta herramienta los cifra**, con
una clave por cliente que vive acá y en ningún otro lado.

No sustituye a la licencia y la licencia no lo sustituye: el cifrado no frena la
instalación copiada entera, y la licencia no frena la extracción del archivo de pesos.
Son dos mecanismos independientes y hacen falta los dos.

---

## 2. Reglas duras, antes de escribir una línea

1. **Ninguna clave de modelo se commitea.** Van en `keys/models/`, cubierto por el
   `.gitignore` desde el primer commit, igual que la clave privada de firma.
2. **Una clave por cliente-proyecto**, no una para todos. Una clave sacada del binario de
   un cliente tiene que exponer el modelo de ese cliente y de ningún otro.
3. **El módulo de clave que consume el build nunca entra a git**, ni al de esta
   herramienta ni al del producto. Se genera, se usa en el build, y se borra.
4. **El nonce es al azar y no se reusa jamás con la misma clave.** Reusarlo con AES-GCM
   rompe la confidencialidad de los dos archivos, y es el error clásico de estos
   esquemas. La herramienta genera el nonce adentro de la función de cifrado y no lo
   recibe por parámetro, justamente para que no se pueda pasar dos veces el mismo.
5. **Toda protección queda registrada.** Un `.enc` en la máquina de un cliente que no está
   en el registro es un archivo que nadie va a poder reproducir cuando el cliente llame.
6. **El formato es un contrato.** El byte de versión existe para poder cambiarlo; hasta
   entonces, lo que sale de acá tiene que abrir en un binario ya instalado.

---

## 3. El formato del contenedor — especificación normativa

### 3.1 Disposición del archivo

```
offset  bytes  campo
0       8      magic       b"IEAWGTS\x00"  (49 45 41 57 47 54 53 00)
8       1      version     1
9       12     nonce       12 bytes al azar, distintos en cada archivo
21      resto  ciphertext  AES-256-GCM, con el tag de 16 bytes pegado al final
```

- **AAD**: los 21 bytes del encabezado —magic, versión y nonce— entran como *additional
  authenticated data* del propio GCM. Editar cualquiera de ellos rompe el tag.
- El tag va **al final del texto cifrado**, que es lo que devuelve `AESGCM.encrypt()` de
  la librería `cryptography` sin hacer nada especial.

### 3.2 Qué se cifra

El texto plano que entra al GCM es la concatenación de tres cosas, en este orden:

```
4 bytes big-endian   largo en bytes del JSON de metadata (0 si no hay)
ese JSON en UTF-8    la metadata del modelo
resto                los pesos, tal cual salieron del framework
```

La metadata queda cifrada junto con los pesos, y ése es el punto: sin eso, los nombres de
clase y el umbral seguirían en el `config.yaml` del equipo, que es un archivo de texto al
lado del ejecutable.

**Tope de metadata: 1 MB.** Son nombres y umbrales, no un modelo.

### 3.3 JSON de la metadata

Canónico, por la misma razón que el payload de la licencia: dos protecciones del mismo
modelo se tienen que poder comparar con un diff.

```python
json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
```

Claves que el producto entiende, todas opcionales — son las mismas de la sección
`inference.models.<slot>` de su `config.yaml`, y **lo que venga acá le gana al config**:

| Clave | Qué es |
|---|---|
| `task` | `classification`, `detection`, `segmentation` o `keypoints`. El producto la confronta con la tarea que declara la clase del modelo y **no carga** si no coinciden |
| `class_names` | lista de nombres, indexada por la clase que devuelve el framework |
| `min_confidence_pct` | umbral de descarte por detección, 0–100 |
| `max_side_px` | lado mayor al que reducir antes de inferir; 0 = nativo |
| `params` | extras propios del framework, tal cual |

**`path` y `type` se ignoran del lado del producto**: un archivo que declarara desde
dónde se carga y con qué clase sería un archivo diciendo cómo abrirse. La herramienta
tiene que **rechazarlas** en vez de escribirlas, para que nadie las ponga esperando que
hagan algo.

### 3.4 El algoritmo, en cinco líneas

```python
nonce  = os.urandom(12)
header = b"IEAWGTS\x00" + bytes([1]) + nonce
plain  = len(meta_bytes).to_bytes(4, "big") + meta_bytes + weights
blob   = header + AESGCM(key).encrypt(nonce, plain, header)
```

---

## 4. La clave del fork

### 4.1 Qué es

**32 bytes** para AES-256, que **no se guardan como 32 bytes** en el binario del producto:
se derivan en tiempo de ejecución con HKDF-SHA256 sobre material partido en fragmentos.
Un blob de 32 bytes se encuentra con un volcado de strings incluso en código compilado; la
derivación no cierra la puerta —alguien con oficio la recupera igual— pero evita el caso
fácil, que es el que va a pasar.

La derivación, que **tiene que dar exactamente lo mismo de los dos lados**:

```python
key = HKDF(
    algorithm=hashes.SHA256(),
    length=32,
    salt=SALT,                                  # los bytes del salt del fork
    info=b"cv-template/model-weights/v1",       # literal, no se cambia
).derive(b"".join(FRAGMENTS))                   # los fragmentos concatenados en orden
```

`info` es una constante del producto y cambiarla invalida todos los archivos ya cifrados.

### 4.2 Cómo se genera

- **Material**: 48 bytes de `os.urandom`, partidos en **3 fragmentos de 16**.
- **Salt**: 16 bytes de `os.urandom`, propios del fork.
- **Etiqueta** (`KEY_LABEL`): `<cliente>-<project_id>-<contador>` en minúsculas, por
  ejemplo `acme-linea3-1`. El contador es lo que permite rotar. **No es un secreto**: el
  producto la escribe en el log para que por teléfono se pueda saber si el equipo tiene
  la clave que le corresponde.

El contador se comporta como el `key_id` de la firma: **no se repite nunca**, porque queda
escrito en el registro y en el log del equipo.

### 4.3 Dónde vive

`keys/models/<key_label>.json`, en el `.gitignore`, con la misma disciplina que la clave
privada de firma —cifrado con passphrase si el repo ya lo hace para la privada, y si no,
al menos en el mismo directorio protegido—:

```json
{
  "key_label": "acme-linea3-1",
  "created_at": "2026-09-10T12:00:00Z",
  "client": "ACME",
  "project_id": "LINEA_3",
  "salt_b64": "…",
  "fragments_b64": ["…", "…", "…"]
}
```

### 4.4 Cómo entra al build del producto

La herramienta **emite el módulo generado** que Nuitka compila. El producto lo espera en
`system/inference/models/_model_key.py`, gitignoreado del otro lado, con esta forma exacta:

```python
KEY_LABEL = "acme-linea3-1"
SALT = b"..."
FRAGMENTS = (b"...", b"...", b"...")
```

El módulo se escribe donde diga `--out`, se usa en el build y **se borra**. No se
commitea, no se manda por mail, no queda en el escritorio de nadie.

### 4.5 Rotación

Si una clave se filtra, se expone el modelo de ese cliente y de ninguno más. La respuesta
es: clave nueva con el contador siguiente, recifrar los pesos, rebuild y reentrega. **No
hay nada que revocar a distancia**, y a diferencia de la rotación de la clave de firma, no
hay dos etapas: el binario nuevo trae una sola clave y abre sólo los archivos nuevos.

---

## 5. Qué construir

### 5.1 Archivos nuevos

```
licensing/
  model_crypto.py     el contenedor: pack() y unpack(), la §3 y nada más
  model_keys.py       generar, guardar y cargar la clave de un fork; derivar los 32 bytes
  cli.py              +3 subcomandos (ver 5.2)
test/
  test_model_crypto.py
  test_model_keys.py
  test_model_round_trip.py    ver §7: es el test que importa
keys/models/          vacío y en el .gitignore
models.csv            el registro de claves y de archivos protegidos
```

Las convenciones de código son las del §6.2 del encargo principal: comentarios y
docstrings en español, identificadores en inglés, `_` sólo para lo interno del módulo o de
la clase, todos los parámetros anotados.

### 5.2 Los comandos

```
python -m licensing model-keygen --client ACME --project-id LINEA_3
    Genera la clave del fork y la guarda en keys/models/<label>.json. Imprime la
    etiqueta y NADA MÁS: ni el salt, ni los fragmentos, ni la clave derivada.
    Se niega a pisar una clave existente sin --force, y --force exige que el contador
    de la etiqueta sea mayor que el de la que reemplaza.

python -m licensing model-keymodule --key-label acme-linea3-1 --out <ruta>/_model_key.py
    Escribe el módulo que compila el build. Avisa en la salida que ese archivo no se
    commitea y que se borra después del build.

python -m licensing protect-model --key-label acme-linea3-1 \
    --weights best.engine \
    --task detection \
    --class-names tornillo tuerca arandela \
    --min-confidence-pct 40 \
    --max-side-px 1280 \
    --param key=value            (repetible; van a `params`)
    --out best.engine.enc
    Cifra los pesos con la metadata y AGREGA LA FILA AL REGISTRO. Sin registro no hay
    protección: es una sola operación.
    Verifica el resultado antes de escribirlo: descifra lo que acaba de cifrar y
    compara byte a byte con la entrada. Un archivo que no abre es peor que ninguno,
    porque el error aparece en la planta.
    Rechaza `path` y `type` en la metadata (§3.3), y rechaza cifrar un archivo que ya
    empieza con el magic: cifrar dos veces produce algo que abre y no sirve.

python -m licensing inspect-model archivo.enc [--key-label acme-linea3-1]
    Sin clave: muestra magic, versión, nonce, tamaños y sha256 del archivo. Con clave:
    además abre el contenedor y muestra la metadata en claro y el tamaño de los pesos.
    NUNCA escribe los pesos descifrados a disco: es una inspección, no una extracción.
    Es lo que se corre cuando un cliente llama diciendo que el modelo no le carga.

python -m licensing model-list [--client ACME] [--key-label acme-linea3-1]
    Lee el registro: qué se protegió, cuándo, con qué clave y con qué metadata.
```

### 5.3 El registro

`models.csv`, versionado en git —el historial **es** la auditoría—, con una fila por
protección:

```
key_label,protected_at,client,project_id,weights_name,weights_sha256,enc_sha256,
size_bytes,task,class_names,min_confidence_pct,app_version,notes
```

- `weights_sha256` es del archivo **en claro** y `enc_sha256` del **cifrado**. El primero
  contesta «¿es éste el modelo que entrenamos?» y el segundo «¿el archivo que está en la
  planta es el que mandamos?». Las dos preguntas aparecen en soporte y son distintas.
- `class_names` va como lista separada por `;`, para que el CSV siga siendo un CSV.
- La generación de una clave también deja fila, con `weights_name` vacío: hace falta saber
  cuándo se emitió cada clave aunque todavía no haya cifrado nada.

Un CSV y no una base de datos, por lo mismo que `registry.csv`: se lee con cualquier cosa
y sobrevive a que la herramienta no se toque durante dos años.

---

## 6. Errores que esta herramienta tiene que hacer imposibles

| Error | Cómo se evita |
|---|---|
| Reusar un nonce | El nonce se genera adentro de `pack()` y no se puede pasar por parámetro |
| Entregar un `.enc` que no abre | `protect-model` descifra y compara antes de escribir |
| Cifrar dos veces el mismo archivo | Se rechaza una entrada que ya empieza con el magic |
| Cifrar con la clave de otro cliente | La etiqueta lleva cliente y proyecto, y el registro los deja ver |
| Perder la clave de un cliente entregado | `keys/models/` se respalda con la misma rutina que la clave privada; sin ella hay que rebuildear y reentregar |
| Que la clave termine en git | `.gitignore` desde el primer commit, y ningún comando la imprime |
| Que el módulo generado quede en el repo del producto | El comando lo dice en su propia salida, y del otro lado está en el `.gitignore` |
| Metadata que el producto ignora en silencio | Se validan las claves conocidas y se rechazan `path` y `type` |

---

## 7. El test que importa: round-trip contra el descifrador real

Igual que el §8 del encargo principal. Un test que **importa el módulo del producto** y
verifica que lo que sale de acá abre allá:

```python
from system.inference.models.encrypted_weights import unpack   # el del producto

def test_lo_que_se_cifra_lo_abre_el_producto():
    key = model_keys.derive_key(material)
    blob = model_crypto.pack(WEIGHTS, key, {"task": "detection",
                                            "class_names": ["a", "b"]})
    bundle = unpack(blob, key)
    assert bundle.weights == WEIGHTS
    assert bundle.metadata["class_names"] == ["a", "b"]
```

Se corre con el repo del producto en el `sys.path`; si no está disponible, el test se
saltea con un motivo explícito en vez de pasar en falso. **Un cambio en el formato que
rompa este test es un cambio que deja plantas sin modelo.**

### La verificación de aceptación, que corre del otro lado

El test de arriba usa el descifrador del producto como librería. La prueba que cierra el
círculo la corre el producto con **el archivo de verdad**, y ya está escrita:

    1. model-keygen para el cliente
    2. model-keymodule --out <producto>/system/inference/models/_model_key.py
    3. protect-model --weights <el modelo> --out best.engine.enc
    4. en el repo del producto:
       .venv/Scripts/python.exe manual_test/model_protection/check_protected_weights.py \
           --enc <ruta>/best.engine.enc

Ese script abre el contenedor con la clave que trae el módulo generado —no con una
simulada—, muestra la metadata que puso el firmante y corre el modelo con ella. **Hasta
que eso dé verde, esta herramienta no está terminada**, por más que sus propios tests
pasen: los suyos prueban que es consistente consigo misma.

Los otros tests, sin el producto a la vista:

- Round-trip propio: cifrar y descifrar devuelve los bytes originales y la metadata.
- Dos cifrados del mismo archivo con la misma clave dan **nonce distinto**.
- Los pesos en claro **no aparecen** adentro del contenedor.
- Clave equivocada, un bit cambiado y archivo cortado: los tres fallan, cada uno con su
  motivo.
- La derivación es determinista, y cambiar el salt, un fragmento o el orden de los
  fragmentos da una clave distinta.
- `model-keygen` dos veces con la misma etiqueta no pisa nada sin `--force`.

---

## 8. El procedimiento operativo, que va en el README

**Cliente nuevo:**

1. `model-keygen` para el cliente-proyecto.
2. `model-keymodule --out <checkout del fork>/system/inference/models/_model_key.py`.
3. Se compila el fork con Nuitka. **Se borra el módulo generado.**
4. `protect-model` con los pesos y su metadata.
5. Se entrega el binario y el `.enc`.

**Reentrenamiento** (el caso frecuente, sobre todo al principio de un proyecto):

1. `protect-model` con la misma `--key-label` y los pesos nuevos.
2. Se copia el `.enc` al equipo, reemplazando el anterior, y se reinicia la aplicación.

**Sin reemitir licencia y sin recompilar.** Ése es el motivo por el que el hash del modelo
no va adentro de la licencia aunque el formato tenga el campo: con el hash adentro, cada
reentrenamiento obligaría a reemitir la licencia de la planta.

---

## 9. Fuera de alcance, y por qué

- **Cifrar de a bloques.** Un modelo de varios cientos de MB descifrado a un buffer
  duplica el pico de memoria por un instante. Se midió y por ahora no molesta —AES-GCM con
  aceleración por hardware va a ~1 GB/s y el pico es el tamaño del modelo—, así que el
  formato se deja simple. Si algún día importa, se sube el byte de versión.
- **Ofuscar más la clave del binario.** Lo que hay sube el costo del caso fácil; contra
  alguien con oficio de ingeniería inversa no hay esquema del lado del cliente que
  alcance, y fingir lo contrario es peor que declararlo.
- **Revocar a distancia.** No hay canal: son equipos sin internet en planta.
- **Cifrar el resto de la entrega** (config, mapa de registros). No son caros de
  reproducir y el cliente los tiene que poder leer.

---

## 10. Criterio de terminado

- `model-keygen`, `model-keymodule`, `protect-model`, `inspect-model` y `model-list`
  andando, con `--help` que se entienda sin este documento.
- El test de round-trip contra el módulo real del producto, en verde.
- La verificación de aceptación del §7 corrida de punta a punta con un modelo de verdad:
  `check_protected_weights.py --enc <archivo>` en el repo del producto, abriendo con el
  `_model_key.py` que emitió esta herramienta.
- `keys/models/` y el módulo generado cubiertos por el `.gitignore` **desde el primer
  commit**, verificado con `git check-ignore`.
- `models.csv` con la fila de la primera clave emitida.
- El README con el procedimiento del §8 y con quién custodia las claves.
- Ningún comando imprime salt, fragmentos ni clave derivada. Verificado leyendo la salida,
  no suponiéndolo.
