---
name: pruebas
description: Cómo se escriben, se corren y se arreglan los tests de este proyecto, y qué hacer cuando el lint o un test fallan. Usar SIEMPRE que se escriba, modifique o revise un test, cuando se toque código que tiene tests, cuando falle `ci/check.py` o uno de los hooks de git, o antes de dar por terminada una tarea que cambió código Python.
---

# Pruebas

Cómo se corre la suite y cómo se comporta uno cuando falla. Los nombres de los tests
siguen la skill `nomenclatura`, y los docstrings la skill `comentarios`; lo que va acá
es lo que no está en ninguna de las dos.

## Comandos

Siempre con el intérprete del venv, y siempre desde `ci/check.py`: es el mismo camino
que corren los hooks, así que lo que pasa acá pasa en el commit y en el push.

    .venv\Scripts\python.exe ci/check.py                 # lint + suite: lo que corre el pre-push
    .venv\Scripts\python.exe ci/check.py lint            # Ruff + índice de tests al día
    .venv\Scripts\python.exe ci/check.py test -- <args>  # pytest con lo que se le pase
    .venv\Scripts\python.exe ci/suite_index.py           # regenera docs/tests.md

- Después de cambiar código Python, correr `ci/check.py` entero antes de decir que la
  tarea está terminada. Tarda unos 15 s.
- Al terminar, informar **qué se corrió** y el resultado tal cual: cantidad de tests que
  pasaron y fallaron, y lo que se salteó con el motivo.
- Agregar, renombrar o borrar un test cambia `docs/tests.md`: se regenera con
  `ci/suite_index.py` y va en el mismo commit. El hook de commit lo exige.

## Dónde va un test

- `test/` espeja el árbol del código: `system/video/http_server.py` se prueba en
  `test/video/test_http_server.py`. Cada carpeta lleva su `__init__.py` vacío.
- Un archivo de test arranca **sólo** el módulo que prueba: sin Qt con ventana, sin
  cámara, sin PLC, sin red hacia afuera y sin GPU.
- Lo que necesita hardware o una pantalla no es un test de la suite: va a
  `manual_test/<tema>/`, con su `config.yaml` al lado.
- Los scripts que no son paquete (`build/`, `ci/`) se cargan por ruta con
  `importlib.util.spec_from_file_location`. Si el módulo declara un `@dataclass`, se
  registra en `sys.modules` antes de `exec_module`, con `monkeypatch.setitem`.

## Cómo se escribe

- **Los dobles viven en el propio archivo de test**, como clases privadas con el nombre
  de lo que reemplazan (`_MockConfig`, `_FakeDriver`, `_Nodemap`). No hay fixtures
  compartidas de dobles: un test se tiene que poder leer sin abrir otro archivo.
- Un comportamiento por test, y el nombre lo dice en una frase:
  `test_a_slow_camera_connects_on_the_next_attempt`. Los tests de un mismo tema se
  agrupan en una clase `Test<Tema>`.
- Archivos en `tmp_path`, variables de entorno y atributos de módulo con `monkeypatch`.
  Nada queda cambiado al salir del test: la suite corre en orden aleatorio
  (pytest-randomly) y en paralelo (pytest-xdist), y un test que deja estado rompe a otro
  que no tiene nada que ver.
- Floats e imágenes con tolerancia: `pytest.approx`, `np.testing.assert_allclose`. Nunca
  `==` sobre un float o un array que salió de una cuenta.
- **Nada de `time.sleep` para esperar a un hilo.** Se espera la condición con un
  `_wait_until(condition, timeout_s)` que hace polling y devuelve si se cumplió; el
  timeout es el techo, no lo que tarda. Un sleep fijo es lento cuando sobra y flaky
  cuando falta.
- Un puerto de red se pide con `0` y se lee el que asignó el sistema: dos workers de
  xdist no pueden pelear por el mismo.
- La aleatoriedad la siembra pytest-randomly en cada test; un test que genera datos al
  azar igual tiene que pasar con cualquier semilla.
- Todo arreglo de un bug lleva un test que falla sin el arreglo y pasa con él.

## Cuando algo falla

1. Leer la salida completa. No adivinar por el nombre del test.
2. Reproducir sólo ese test:
   `ci/check.py test -- "test/video/test_http_server.py::TestLifecycle::test_x" -p no:randomly`.
3. Si sólo falla en la suite completa, es orden o estado compartido: repetir con la
   semilla que imprimió la corrida (`--randomly-seed=N`) y buscar qué test dejó algo
   cambiado.
4. Decidir si está mal el **código** o el **test**. Por defecto lo que se arregla es el
   código: un test que falla está diciendo algo, y se lo escucha antes de tocarlo.
5. **Un test no se modifica para que pase.** Si el código de producción cambió a propósito
   y el test quedó describiendo un comportamiento que ya no corresponde, se explica en la
   conversación —qué afirmaba el test, qué cambió en el código y por qué la expectativa
   vieja ya no vale— y se **espera la confirmación** de la persona antes de editarlo. Vale
   también para un doble que ya no se parece al real. Sin confirmación el test queda como
   está y el fallo se informa.
6. Volver a correr ese test y después `ci/check.py` entero.
7. Si después de un intento razonable no aparece la causa, parar y contar qué se probó.
   Un fallo sin explicar se informa, no se esconde.

Cada test que falló lleva un resumen corto en la respuesta, aunque ya esté arreglado:

- **qué falló**: el test y lo que afirmaba;
- **por qué**: la causa en el código, no el síntoma;
- **qué se cambió**: en qué archivo y qué, para que pase.

## Lo que no se hace para que quede en verde

- Editar un test que falla sin haber explicado por qué y recibido la confirmación.
- Borrar, comentar, `skip` o `xfail` un test que falla.
- Aflojar una tolerancia, debilitar un assert o atrapar `Exception` para que pase.
- Agregar un `sleep` o reintentos para tapar un test que a veces falla.
- `git commit --no-verify` o `git push --no-verify`, o tocar `ci/check.py`, los hooks o
  `pyproject.toml` para que dejen de ver el problema.
- Un `# noqa` sin una línea que diga por qué la regla no aplica ahí.
- Cambiar código de producción sólo para que pase un test mal escrito sin decirlo.
