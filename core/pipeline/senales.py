"""Senales escalares por estacion y su calibracion medida.

Vive aqui y no en `detector_estados.py` porque tambien lo necesita
`tools/calibrar_estado.py`, y una herramienta de consola no debe arrastrar
Tkinter ni customtkinter solo para leer un umbral.

Los valores de calibracion son MEDICIONES sobre video real, no supuestos. Una
estacion sin `calibrada: True` no se puede medir: falta uno de los dos extremos
y sin ambos no hay umbral posible.
"""
from __future__ import annotations

import cv2
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


class MovimientoEnRegion:
    """Cuanto se movio la region respecto de la muestra anterior.

    Devuelve el PORCENTAJE de pixeles cuyo nivel de gris cambio mas de
    `UMBRAL_CAMBIO`. Es la senal de la tolva: alli no interesa un color ni un
    nivel, sino si algo se esta moviendo (el cargador frontal descargando
    chatarra en la artesa).

    **Por que contar pixeles y no promediar la diferencia.** Medido el
    20-ago-2026 sobre los 10 min del video del 20-jul, region de la artesa,
    2 Hz, con los tramos verificados contra imagen:

        senal                quieta        cargando       separacion
        diferencia media     1.1 a 1.8     3.4 a 12.6     3-7x
        % pixeles > 25       0.05 a 0.30   2.3 a 14.7     83x

    La diferencia media nunca baja de 1.1 con la escena quieta porque el RUIDO
    del sensor mueve un poco TODOS los pixeles, y ese piso se come la senal. Un
    umbral por pixel descarta ese ruido: con la escena quieta el 100 % de las
    muestras quedan por debajo del umbral de salida de la histeresis.

    **Y resiste los faros**, que era la forma mas probable de falso positivo en
    una nave de noche: el cargador entra con luces y el brillo medio de la
    region sube unos 10 niveles, pero un cambio global de 10 niveles esta por
    debajo del umbral de 25 y no cuenta como movimiento.

    **Tiene MEMORIA**, a diferencia de las otras senales, que son funciones
    puras. Por eso es una clase, y por eso en `ESTACIONES` y en `SENALES` se
    guarda la CLASE y no una instancia: cada proceso construye la suya y no
    comparte el cuadro previo con nadie. Quien la consuma debe instanciarla.

    El valor depende del INTERVALO entre muestras: a 2 Hz compara cuadros
    separados 0.5 s. Cambiar `HZ_ANALISIS` cambia lo que la senal significa y
    obliga a recalibrar.
    """

    # Niveles de gris que un pixel debe cambiar para contar como movimiento.
    # 25 sale de la medicion de arriba: descarta el ruido del sensor y el
    # cambio global de los faros, y aun asi deja pasar la chatarra cayendo.
    UMBRAL_CAMBIO = 25

    def __init__(self) -> None:
        self._previo: np.ndarray | None = None

    def __call__(self, recorte: np.ndarray) -> float:
        if recorte.size == 0:
            # Region degenerada por los deslizadores: se olvida lo anterior para
            # no comparar contra un encuadre que ya no existe.
            self._previo = None
            return 0.0
        gris = cv2.cvtColor(recorte, cv2.COLOR_BGR2GRAY)
        previo, self._previo = self._previo, gris
        if previo is None or previo.shape != gris.shape:
            # Primera muestra, o el operador movio la region con la medicion
            # corriendo. `absdiff` con formas distintas revienta, y aunque no
            # reventara, comparar dos encuadres distintos daria un movimiento
            # inventado justo al ajustar el recuadro.
            return 0.0
        diferencia = cv2.absdiff(gris, previo)
        return float((diferencia > self.UMBRAL_CAMBIO).mean() * 100.0)


SENALES = {
    "rosado": senal_rosado,
    "brillo": senal_brillo,
    "ocupacion": senal_ocupacion_clara,
    "movimiento": MovimientoEnRegion,
}


def construir_senal(senal):
    """Devuelve algo llamable a partir de lo que declara una estacion.

    Las senales sin estado son funciones y se usan tal cual; las que tienen
    memoria se declaran como CLASE y hay que instanciarlas una vez por proceso.
    Vive aqui para que el modulo y el calibrador no repitan el mismo `isinstance`
    y no se les olvide a la vez.
    """
    return senal() if isinstance(senal, type) else senal


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
        # Se mide MOVIMIENTO, no nivel: lo que interesa es cuando se esta
        # cargando y cuanto dura, no cuanta chatarra hay. El nivel se descarto
        # como senal principal porque depende de la forma del monton y de la
        # sombra, mientras que el movimiento se verifico 4/4 contra imagen.
        "nombre_activo": "cargando",
        "nombre_inactivo": "quieta",
        # La CLASE, no una instancia: tiene memoria del cuadro previo y cada
        # proceso necesita la suya. `construir_senal` la instancia.
        "senal": MovimientoEnRegion,
        "origen": "camara:movimiento",
        # Medido el 20-ago-2026 sobre el video del 20-jul (10 min, 4K, 2 Hz),
        # promediando SOLO las muestras de tramos verificados contra imagen:
        # 370 muestras quietas (media 0.11, sd 0.09, max 0.61) y 480 cargando
        # (media 9.41, sd 6.67, max 26.39). Separacion de 83x, y el 100 % de las
        # muestras quietas cae por debajo del umbral de salida.
        "inactivo_medido": 0.11,
        "activo_medido": 9.41,
        # El interior de la artesa: donde cae la chatarra y entra la cuchara.
        # Verificada dibujada sobre el cuadro antes de medir; excluye la cabina
        # del cargador y el suelo para no contar su paso como carga.
        "region": {"x": 0.19, "y": 0.13, "w": 0.58, "h": 0.30},
        "calibrada": True,
        "descripcion": "Movimiento en la artesa: carga de chatarra",
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
