"""
Catálogo de fabricantes y modelos de cámara soportados.

Tabla de datos, no maquinaria: se amplía por proyecto según el stock, y no
importa ningún driver — la UI puede leerla sin arrastrar los SDK de cámara.

Para agregar un fabricante o modelo, editar CAMERA_CATALOG directamente:
  - La clave es el id estable que se guarda en `brand` de config.yaml. Renombrarla
    invalida los configs ya escritos, así que se elige una vez.
  - "label" es el texto que muestra la UI; se puede cambiar sin romper nada.
  - "driver" debe ser uno de REGISTERED_DRIVERS (camera_factory.py).
  - "models" es la lista de modelos que se ofrecen para ese fabricante, y de ahí
    sale el valor de `model` en config.yaml.
"""

CAMERA_CATALOG: dict[str, dict] = {
    "basler": {
        "label": "Basler",
        "driver": "basler_gige",
        "models": [
            "a2A1920-51gcBAS",
        ],
    },
    "sentech": {
        "label": "Sentech",
        "driver": "st_gige",
        "models": [
            "STC-MCA503POE-HS",
        ],
    },
    "rtsp": {
        "label": "IP genérica (RTSP)",
        "driver": "rtsp",
        "models": [
            "generic",
        ],
    },
    "mock": {
        "label": "Simulado (Mock)",
        "driver": "mock",
        "models": ["mock"],
    },
}
