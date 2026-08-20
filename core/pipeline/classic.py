"""Deteccion y conteo por vision clasica, sin modelo entrenado.

Es la **unica** implementacion del detector: la usan tanto el capturador de
dataset como el modulo de deteccion de objetos. Si viviera duplicada, ambos
derivarian y el dataset dejaria de corresponder con lo que ve produccion.

El material se reconoce por tres condiciones simultaneas: destaca sobre el
fondo, es claro, y tiene **forma alargada**. Esa tercera condicion es la que
descarta el vapor, que tambien es brillante y tambien se mueve, pero es difuso.

Todos los umbrales salen de mediciones sobre la camara real, no de valores
supuestos. Estan documentados en `Ajustes`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np


# El procesamiento corre a la MITAD de la region de interes (280x1020 -> 280x510).
# A esa escala el lingote mide 100x25 px, de sobra para vision clasica, y el
# costo en CPU baja cuatro veces. Los ajustes de abajo estan calibrados a esa
# escala contra un conteo manual verificado (15 piezas en 60 s).
ESCALA_PROCESO = 0.5


@dataclass
class Ajustes:
    """Parametros del detector, medidos sobre la camara de la lingotera."""

    # El lingote supera el fondo de la banda en mas de 40 niveles (medido: 51).
    diferencia_minima: int = 40
    # Sale caliente y reflejante; la banda vacia ronda 19.
    brillo_minimo: int = 110
    # A media escala el lingote ocupa ~100x25 px: 2,500. Se admite desde 350
    # por si entra parcialmente al cuadro.
    area_minima: int = 350
    # Relacion largo/ancho. El lingote ronda 4:1; el vapor es redondeado.
    alargamiento_minimo: float = 2.0
    # Franjas que no son banda: arriba la maquina, abajo la rejilla del piso
    # (valores a media escala).
    margen_superior: int = 100
    margen_inferior: int = 80


def detectar(gris: np.ndarray, fondo: np.ndarray,
             ajustes: Ajustes | None = None) -> list[tuple[int, int, int, int]]:
    """Devuelve las cajas (x, y, w, h) del material presente en el recorte."""
    a = ajustes or Ajustes()
    alto, _ = gris.shape
    dif = cv2.absdiff(gris, fondo)
    mascara = ((dif > a.diferencia_minima) & (gris > a.brillo_minimo)).astype(np.uint8)
    mascara[: a.margen_superior] = 0
    mascara[alto - a.margen_inferior:] = 0
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mascara = cv2.morphologyEx(mascara, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))

    total, _, stats, _ = cv2.connectedComponentsWithStats(mascara, 8)
    cajas = []
    for k in range(1, total):
        x, y, w, h, area = stats[k]
        if area < a.area_minima:
            continue
        if max(w, h) / max(min(w, h), 1) < a.alargamiento_minimo:
            continue
        cajas.append((int(x), int(y), int(w), int(h)))
    return cajas


class ModeloDeFondo:
    """Fondo de la banda por mediana movil.

    Se usa mediana y no promedio porque el material que pasa es un valor
    atipico: la mediana lo ignora y conserva la banda vacia.

    Con la banda DETENIDA el fondo no se alimenta: en el colado del
    14-ago-2026 hubo 70 minutos de pausas con piezas quietas a la vista, la
    mediana se las aprendio como fondo y al reanudar eran invisibles.
    """

    # Umbral de "banda detenida": la banda en movimiento cambia el cuadro
    # mucho mas que esto; dos cuadros consecutivos de banda parada solo
    # difieren por ruido del sensor (~0-1 niveles de gris en promedio).
    CAMBIO_MINIMO = 0.35

    def __init__(self, memoria: int = 60, cada: int = 5):
        self.memoria = memoria
        self.cada = cada
        self._muestras: list[np.ndarray] = []
        self._anterior: np.ndarray | None = None
        self._contador = 0
        self.fondo: np.ndarray | None = None

    def actualizar(self, gris: np.ndarray) -> np.ndarray:
        self._contador += 1
        detenida = (self._anterior is not None
                    and float(np.mean(cv2.absdiff(gris, self._anterior)))
                    < self.CAMBIO_MINIMO)
        self._anterior = gris.copy()
        if self.fondo is None or (self._contador % self.cada == 0
                                  and not detenida):
            self._muestras.append(gris.copy())
            if len(self._muestras) > self.memoria:
                self._muestras.pop(0)
            self.fondo = np.median(np.stack(self._muestras), axis=0).astype(np.uint8)
        return self.fondo

    @property
    def listo(self) -> bool:
        return len(self._muestras) >= 5


@dataclass
class Pista:
    """Un objeto seguido a lo largo de varios cuadros."""

    identificador: int
    x: float
    y: float
    visto: int = 1
    ausente: int = 0
    contado: bool = False
    entro_completo: bool = False
    # Donde nacio la identidad: las piezas reales entran por ARRIBA de la
    # region, asi que una pista nacida junto a la linea (o debajo) solo puede
    # ser la re-deteccion de una pieza que ya paso.
    origen_y: float = 0.0
    historial: list[tuple[float, float]] = field(default_factory=list)

    def velocidad_y(self) -> float:
        """Avance medido en px por actualizacion (0 si aun no hay datos)."""
        if len(self.historial) < 2:
            return 0.0
        puntos = self.historial[-5:]
        avance = (puntos[-1][1] - puntos[0][1]) / (len(puntos) - 1)
        return max(0.0, avance)


class ContadorDeObjetos:
    """Sigue objetos y cuenta los que cruzan la linea.

    Incorpora las tres lecciones medidas sobre el video real:

    1. **Antiparpadeo.** Sin tolerancia a huecos, la mascara parte un lingote en
       dos y el conteo se infla al 193% (medido: 29 en vez de 15). Se toleran
       hasta `ausencias_toleradas` cuadros sin ver el objeto.
    2. **Pieza completa.** Solo se cuenta lo que entro entero al cuadro; una
       pieza cortada por el borde no cuenta. Es la regla acordada con operacion.
    3. **Sentido unico.** Se cuenta al cruzar la linea en el sentido de avance,
       asi cada pieza se cuenta una vez y siempre en el mismo punto.
    """

    # La posicion de la linea resulto ser el parametro mas sensible: barrida
    # contra el conteo manual, y=180 daba 8 piezas y y=270 daba las 15 exactas.
    # Demasiado arriba la pieza aun no entro completa; demasiado abajo ya sale.
    LINEA_RECOMENDADA = 270   # sobre un recorte de 510 px de alto

    def __init__(self, linea_y: int, alto: int, ancho: int,
                 distancia_maxima: float = 70.0,
                 ausencias_toleradas: int = 20,
                 margen_borde: int = 4,
                 margen_origen: int | None = None,
                 ventana_gemela: int = 22):
        self.linea_y = linea_y
        self.alto = alto
        self.ancho = ancho
        self.distancia_maxima = distancia_maxima
        self.ausencias_toleradas = ausencias_toleradas
        self.margen_borde = margen_borde
        # La banda es de un carril: una caja a mas de esto del centro de la
        # pista es otra cosa (una caja parcial desplaza el centro a lo mas
        # un cuarto del lingote de ~100 px a media escala).
        self.tolerancia_lateral = 60.0
        # Candado 1 del doble conteo: una pista nacida cerca de la linea que
        # cruza POCO DESPUES de un conteo es la pieza recien contada
        # renacida, no una pieza nueva. El margen es generoso (10% del
        # recorte: la re-deteccion mas lejana medida nacio a 13 px de la
        # linea y un margen del 3% la dejo pasar por 0.5 px) porque quien
        # protege a las piezas reales es la condicion TEMPORAL: una pieza
        # legitima cruza 68+ actualizaciones despues del conteo anterior
        # (hueco minimo medido), y las detectadas tarde junto a la linea
        # cuentan porque no hay conteo reciente.
        self.margen_origen = (max(8, int(alto * 0.10))
                              if margen_origen is None else margen_origen)
        # Tras cruzar, la pieza cae a la charola y el detector la sigue
        # partiendo en cajas junto a la linea: el re-cruce mas tardio medido
        # ocurrio 18 actualizaciones despues del conteo (modelo, video de
        # dia). El hueco real minimo entre piezas es de 68 actualizaciones
        # (medido en ambos videos y ambos modos): 30 queda arriba del peor
        # duplicado observado y debajo de la mitad del hueco real.
        self.ventana_redeteccion = 30
        # Candado 2: dos conteos casi simultaneos son la misma pieza partida
        # en dos cajas. Con el modelo v2 (15-ago) las gemelas cruzan hasta 16
        # actualizaciones despues de la pieza real; la pieza REAL mas proxima
        # llega a las 35 (ciclo minimo 2.8 s a 12.5 fps). 22 queda arriba del
        # peor duplicado observado y abajo del minimo fisico.
        self.ventana_gemela = ventana_gemela
        # Candado 3: un cruce logrado con un salto mayor a dos alturas de
        # pieza (>60 px a media escala) en una pista nacida junto a la linea
        # y poco vista es ruido del detector, no una pieza. La pieza ocluida
        # legitima nace ARRIBA y viene rastreada (candado no aplica).
        self.salto_maximo_cruce = 60.0
        self.vistas_minimas_salto = 8
        self._pistas: dict[int, Pista] = {}
        self._siguiente = 1
        self._tick = 0
        self._ultimo_conteo_tick: int | None = None
        self.total = 0
        self.ultimo_conteo: str = ""
        # Ids de las pistas contadas en la ULTIMA actualizacion: la interfaz
        # los muestra en el registro de ultimos conteos.
        self.contadas_recientes: list[int] = []

    @property
    def pistas(self) -> list[Pista]:
        return list(self._pistas.values())

    def _completa(self, caja) -> bool:
        x, y, w, h = caja
        m = self.margen_borde
        return (x > m and y > m
                and x + w < self.ancho - m and y + h < self.alto - m)

    def actualizar(self, cajas: list[tuple[int, int, int, int]]) -> int:
        """Procesa las cajas de un cuadro. Devuelve cuantas piezas se contaron."""
        self._tick += 1
        self.contadas_recientes = []
        centros = [(x + w / 2.0, y + h / 2.0) for x, y, w, h in cajas]
        completas = [self._completa(c) for c in cajas]
        asignadas: set[int] = set()
        contadas = 0

        for pista in self._pistas.values():
            # Corredor de busqueda. Mientras una pieza esta oculta (el tubo
            # tapa la linea en el encuadre actual) sigue avanzando y puede
            # frenar al caer: hacia ADELANTE el corredor crece con la
            # velocidad observada por el tiempo ausente (tope 4x), pero a lo
            # ANCHO se queda en el carril. Un radio inflado en todas
            # direcciones dejaba que una pista visible robara detecciones de
            # la charola y contara de mas (medido: 16 en vez de 15).
            frente = min(self.distancia_maxima * 4,
                         self.distancia_maxima
                         + pista.ausente * pista.velocidad_y())
            mejor, distancia_minima = None, float("inf")
            for i, (cx, cy) in enumerate(centros):
                if i in asignadas:
                    continue
                # Solo se asocia hacia adelante: el material no retrocede.
                if cy < pista.y - 15:
                    continue
                if cy - pista.y > frente:
                    continue
                if abs(cx - pista.x) > self.tolerancia_lateral:
                    continue
                d = abs(cx - pista.x) + abs(cy - pista.y)
                if d < distancia_minima:
                    mejor, distancia_minima = i, d
            if mejor is None:
                pista.ausente += 1
                continue
            asignadas.add(mejor)
            cx, cy = centros[mejor]
            anterior = pista.y
            pista.x, pista.y = cx, cy
            pista.visto += 1
            pista.ausente = 0
            pista.historial.append((cx, cy))
            if completas[mejor]:
                pista.entro_completo = True
            if (not pista.contado and pista.entro_completo
                    and anterior < self.linea_y <= cy):
                # La pista ya cruzo: no vuelve a evaluarse aunque el conteo
                # se descarte por los candados de abajo.
                pista.contado = True
                if self._conteo_valido(pista, salto_cruce=cy - anterior):
                    self.total += 1
                    contadas += 1
                    self._ultimo_conteo_tick = self._tick
                    self.contadas_recientes.append(pista.identificador)

        for i, (cx, cy) in enumerate(centros):
            if i in asignadas:
                continue
            self._pistas[self._siguiente] = Pista(
                self._siguiente, cx, cy,
                entro_completo=completas[i], origen_y=cy,
                historial=[(cx, cy)])
            self._siguiente += 1

        for identificador, pista in list(self._pistas.items()):
            limite = self.ausencias_toleradas
            if (pista.entro_completo and not pista.contado
                    and pista.y < self.linea_y):
                # Pieza armada que aun no cruza: si desaparece junto a la
                # linea es una oclusion (el tubo del encuadre actual), no una
                # pieza que se fue. Se le espera el triple antes de darla por
                # perdida; sin esto, el cruce ocurrido bajo el tubo se perdia.
                limite *= 3
            if pista.ausente > limite:
                del self._pistas[identificador]

        return contadas

    def _conteo_valido(self, pista: Pista, salto_cruce: float = 0.0) -> bool:
        """Candados contra el doble conteo de una misma pieza (14/15-ago)."""
        transcurrido = (None if self._ultimo_conteo_tick is None
                        else self._tick - self._ultimo_conteo_tick)
        # Identidad nacida pegada a la linea que cruza poco despues de un
        # conteo: es la pieza recien contada renacida por una caja parcial.
        if (pista.origen_y >= self.linea_y - self.margen_origen
                and transcurrido is not None
                and transcurrido <= self.ventana_redeteccion):
            return False
        # Cruce casi simultaneo a otro conteo: la misma pieza partida en dos.
        if transcurrido is not None and transcurrido <= self.ventana_gemela:
            return False
        # Cruce por salto grande en una pista recien nacida junto a la linea:
        # ruido del detector. La pieza ocluida legitima nace arriba o viene
        # rastreada desde hace muchas actualizaciones.
        if (salto_cruce > self.salto_maximo_cruce
                and pista.origen_y >= self.linea_y - self.margen_origen
                and pista.visto < self.vistas_minimas_salto):
            return False
        return True

    def reiniciar(self) -> None:
        self._pistas.clear()
        self._tick = 0
        self._ultimo_conteo_tick = None
        self.total = 0
        self.ultimo_conteo = ""
        self.contadas_recientes = []
