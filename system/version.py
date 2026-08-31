"""
Versión del programa.

Módulo de infraestructura: no importa nada, ni del repo ni de afuera.

**Va en código y no en `config.yaml` a propósito.** El config declara lo que cambia entre
instalaciones —esta cinta mide 1200 mm, esta cámara está en esta IP—; la versión cambia
cuando cambia el código, y es la misma en las diez instalaciones que corren este commit.
Ponerla en el config dejaría que una planta dijera que corre la 2.0 mientras corre la 1.3,
que es exactamente lo que uno no quiere saber tarde: cuando el operador lee un número por
teléfono para reportar un problema, ese número tiene que venir del programa que está
corriendo.

Cómo se sube. Se edita `APP_VERSION` en el mismo commit que cierra el cambio, con
`MAJOR.MINOR.PATCH`:

  - **PATCH**: correcciones que no cambian lo que el equipo publica hacia afuera.
  - **MINOR**: funcionalidad nueva, o claves nuevas en `config.yaml` con default.
  - **MAJOR**: algo que rompe hacia afuera —el mapa de registros, una clave de config
    obligatoria, el formato del JSON del dataset—, porque es lo que obliga al integrador
    o al que reentrena a cambiar algo de su lado.

Cada fork lleva su propia versión: son programas distintos, aunque compartan la plomería.
"""

APP_VERSION = "0.2.0"
