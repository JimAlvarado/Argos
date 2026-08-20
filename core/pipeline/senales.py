"""Senales escalares por estacion y su calibracion medida.

Vive aqui y no en `detector_estados.py` porque tambien lo necesita
`tools/calibrar_estado.py`, y una herramienta de consola no debe arrastrar
Tkinter ni customtkinter solo para leer un umbral.

Los valores de calibracion son MEDICIONES sobre video real, no supuestos. Una
estacion sin `calibrada: True` no se puede medir: falta uno de los dos extremos
y sin ambos no hay umbral posible.
"""
from __future__ import annotations

import numpy as np


def senal_rosado(recorte: np.ndarray) -> float:
    """Cuanto "rosado" hay en el recorte: promedio de R menos G.

    Es el discriminador del mantenedor y NO se usa el brillo, aunque tambien
    separe. Medido el 19-ago-2026 en el vano de la puerta:

        cerrada  brillo 70.8   R-G  -3.77
        abierta  brillo 98.1   R-G +20.74

    El brillo separa peor y es fragil: una lampara, un reflejo o el cambio de
    turno lo mueven. El resplandor del metal fundido es lo unico magenta en una
    nave gris, y en gris R-G vale ~0 por definicion. Por eso resiste cambios de
    iluminacion, que era el riesgo principal de una senal luminosa.

    El casteo a int16 es obligatorio: en uint8, R-G con G>R da la vuelta y
    devuelve ~250 en vez de un negativo. Seria un falso "abierta" permanente.
    """
    if recorte.size == 0:
        return 0.0
    canales = recorte.astype(np.int16)
    return float((canales[:, :, 2] - canales[:, :, 1]).mean())


def senal_brillo(recorte: np.ndarray) -> float:
    """Luminancia media del recorte.

    Fue la candidata para la llama del horno y quedo DESCARTADA con medicion el
    20-ago-2026, no por criterio: sobre 96 min de video el minimo del horno
    encendido (+90.98) coincide con el del apagado (+90.25), y el maximo del
    apagado de DIA (+135.63) supera la mediana del encendido de noche
    (+112.85). Los rangos se solapan por completo porque la region incluye las
    ventanas del fondo: un umbral de brillo mide la hora del dia, y el horno
    habria aparecido encendido cada mañana estando frio.

    Se conserva porque sigue siendo la candidata razonable para una senal
    luminosa en una escena SIN luz natural, pero no se usa en ninguna estacion.
    """
    if recorte.size == 0:
        return 0.0
    return float(recorte.astype(np.int16).mean())


def senal_ocupacion_clara(recorte: np.ndarray) -> float:
    """Fraccion de pixeles claros: candidata para el nivel de la tolva.

    La chatarra se ve clara sobre el interior oscuro de la artesa, el mismo tipo
    de contraste que ya se explota con el lingote caliente. SIN calibrar: hace
    falta video de una tolva vacia y de una llena para fijar los dos extremos.
    """
    if recorte.size == 0:
        return 0.0
    gris = recorte.astype(np.int16).mean(axis=2)
    return float((gris > 140).mean() * 100.0)


SENALES = {
    "rosado": senal_rosado,
    "brillo": senal_brillo,
    "ocupacion": senal_ocupacion_clara,
}


# Region de interes en coordenadas RELATIVAS (0-1), no en pixeles: la camara del
# mantenedor entrega 2560x1440 pero el modulo no debe romperse si cambia de
# resolucion o de perfil RTSP. Medida sobre el video del 19-ago: el vano ocupa
# x 1180-1935, y 620-790 de 2560x1440.
ESTACIONES = {
    "mantenedor": {
        "titulo": "Mantenedor",
        "nombre_activo": "abierto",
        "nombre_inactivo": "cerrado",
        "senal": senal_rosado,
        "origen": "camara:rosado",
        # Los dos estados medidos en video, no umbrales inventados.
        "inactivo_medido": -3.77,
        "activo_medido": 20.74,
        "region": {"x": 0.461, "y": 0.431, "w": 0.295, "h": 0.118},
        "calibrada": True,
        "descripcion": "Aperturas de la puerta y tiempo abierto",
    },
    "tolva": {
        "titulo": "Tolva",
        "nombre_activo": "cargada",
        "nombre_inactivo": "vacia",
        "senal": None,
        "origen": "camara:ocupacion",
        "inactivo_medido": None,
        "activo_medido": None,
        "region": {"x": 0.26, "y": 0.26, "w": 0.52, "h": 0.16},
        "calibrada": False,
        "descripcion": "Llenado y envio de carga al horno",
    },
    "horno": {
        "titulo": "Horno rotatorio",
        "nombre_activo": "encendido",
        "nombre_inactivo": "apagado",
        "senal": None,
        "origen": "camara:llama",
        # NO se activa, y ya no es por falta de video: los tres videos del
        # 19 y 20-ago (96 min en total) demostraron que "encendido/apagado" NO
        # es medible con esta camara. Medido sobre la region de abajo:
        #
        #   apagado / boca oculta      R-G  -2.41 a +0.39   area  0.2 %
        #   quemador en vacio          R-G  +0.91 (sd 0.61) area  2.3 %
        #   fundiendo con la boca vista R-G +15.11 a +23.72 area  8-27 %
        #   colada (vaciado)           R-G  +28.82          area 25.4 %
        #
        # El problema no es el umbral: es que la senal mide si SE VE el interior
        # incandescente, y eso lo decide la POSICION DEL TAMBOR, no el horno.
        # Verificado en imagen: en v2 la senal cae de +11.65 a +0.39 entre
        # 03:20:59 y 03:21:39 porque el tambor gira y tapa la boca, con el horno
        # caliente. Simulado con MaquinaDeEstado (apagado -0.5, encendido +19),
        # el video del 20-ago reporta 19m 29s de "apagado" sobre un horno que la
        # imagen muestra encendido las dos horas: 97 % del tiempo mal asignado,
        # y sin parpadeo ninguno. El registro se veria impecable y estaria mal.
        #
        # "Encendido" tiene que venir del PLC, que ya gobierna el horno; la
        # costura esta puesta (`origen` en la tabla estados). Lo que SI tiene
        # firma optica robusta es la COLADA: brillo +120 contra ~95 de fondo,
        # R-G +28.8 y 25 % de area, sostenido unos 2 min. Es otra estacion, no
        # esta.
        #
        # El brillo quedo descartado con medicion, no por criterio: el minimo
        # del horno encendido (+90.98) es igual al del apagado (+90.25) y el
        # maximo del apagado de dia (+135.63) supera la mediana del encendido
        # (+112.85). Un umbral de brillo mide la hora del dia.
        "inactivo_medido": None,
        "activo_medido": None,
        "region": {"x": 0.45, "y": 0.15, "w": 0.20, "h": 0.40},
        "calibrada": False,
        "descripcion": "Encendido, carga recibida y giro",
    },
}


def recortar(cuadro: np.ndarray, region: dict) -> tuple[np.ndarray, tuple]:
    """Recorta la region RELATIVA al tamano real del cuadro.

    Se recorta contra el cuadro real en vez de confiar en los valores: una
    region degenerada por los deslizadores no puede tumbar el ciclo.
    """
    alto, ancho = cuadro.shape[:2]
    x0 = max(0, min(int(region["x"] * ancho), ancho - 2))
    y0 = max(0, min(int(region["y"] * alto), alto - 2))
    x1 = max(x0 + 1, min(int((region["x"] + region["w"]) * ancho), ancho))
    y1 = max(y0 + 1, min(int((region["y"] + region["h"]) * alto), alto))
    return cuadro[y0:y1, x0:x1], (x0, y0, x1, y1)
