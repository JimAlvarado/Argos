"""Estados con duracion: presencia, permanencia y sus transiciones.

Es la fase 2 del proyecto. Hasta ahora el sistema registraba **hechos
instantaneos**: una deteccion, un cruce de linea, una alerta de zona. Las
camaras de tolva, horno y mantenedor no preguntan "que objeto hay" sino
**"en que estado esta esto y desde hace cuanto"**, que es un problema distinto
y es el MISMO en las tres. Por eso vive aqui una sola vez, igual que
`classic.py` es la unica implementacion de la deteccion clasica.

El motor no sabe de camaras. Consume una **senal escalar** y no le importa de
donde sale: hoy de un recorte de video, mañana de un tag de PLC. Esa costura
esta puesta a proposito desde el primer dia porque agregarla despues obligaria
a revisar cada modulo que ya la use.

Tres decisiones y su razon, todas medidas sobre video real:

- **Histeresis (dos umbrales, no uno).** Con un solo umbral, una senal que
  oscila alrededor del corte genera decenas de transiciones falsas. El modulo
  de lingotes inflo el conteo un 193% hasta que se le puso antiparpadeo; es la
  misma leccion.
- **Permanencia minima.** Un estado debe sostenerse antes de aceptarse. No es
  por ruido de la senal (medida sobre el mantenedor del 19-ago, la transicion
  es abrupta: R-G pasa de -3.8 a +20.7), sino por **oclusiones**: en esa misma
  escena hay un montacargas estacionado y personal caminando frente al vano.
  Alguien cruzando no debe contar como que la puerta se cerro.
- **El intervalo empieza en el CRUCE, no en la confirmacion.** Si se anotara la
  hora de confirmacion, toda duracion saldria corta justo por la permanencia.
  Con 3 s de permanencia y aperturas de minutos el error seria chico, pero en
  aperturas breves seria enorme y sistematico.

Deliberadamente binario. El horno necesita "encendido/apagado" y
"girando/quieto": son dos maquinas independientes compuestas, no una maquina de
cuatro estados. Mantenerlo binario deja la logica demostrable.
"""
from __future__ import annotations

from dataclasses import dataclass

# Segundos que un estado debe sostenerse para aceptarse. Sobre el mantenedor
# (muestreo a 2 Hz) son 6 muestras consecutivas, suficiente para descartar a
# una persona cruzando el vano sin retrasar de forma apreciable una apertura
# que dura minutos.
PERMANENCIA_POR_DEFECTO = 3.0

# Separacion entre los dos umbrales, como fraccion del recorrido medido entre
# el estado inactivo y el activo. Con 0.25 los umbrales caen al 37.5% y 62.5%
# del recorrido: banda ancha para no vibrar y aun asi lejos de ambos extremos.
SEPARACION_POR_DEFECTO = 0.25


@dataclass(frozen=True)
class Histeresis:
    """Los dos umbrales de la senal.

    `entra` es el valor que hay que superar para pasar a activo; `sale` es el
    valor por debajo del cual se vuelve a inactivo. Entre los dos, el estado se
    queda como esta: eso es lo que evita el parpadeo.
    """

    entra: float
    sale: float

    def __post_init__(self):
        if not self.entra > self.sale:
            # Con entra == sale no hay histeresis, solo un umbral disfrazado, y
            # una senal apoyada justo ahi transiciona en cada muestra.
            raise ValueError(
                "La histeresis necesita entra > sale; recibido "
                f"entra={self.entra}, sale={self.sale}."
            )

    @classmethod
    def desde_estados_medidos(
        cls, inactivo: float, activo: float,
        separacion: float = SEPARACION_POR_DEFECTO,
    ) -> "Histeresis":
        """Deriva los umbrales de los DOS estados medidos en video.

        Es el metodo correcto y no el que se uso en el primer analisis. Alli los
        umbrales salieron de percentiles del rango observado, y eso depende de
        cuanto duro cada estado: como la puerta estuvo abierta el 84% del video,
        el umbral de apertura cayo DENTRO de la distribucion de "abierta" y
        funciono por suerte. El punto medio entre dos estados medidos no tiene
        ese sesgo.

        Ejemplo real (mantenedor, 19-ago-2026): cerrada R-G = -3.77, abierta
        R-G = +20.74 -> entra 14.6, sale 5.4, con los dos estados medidos
        holgadamente fuera de la banda.
        """
        if activo == inactivo:
            raise ValueError(
                "Los dos estados medidos son iguales; no hay senal que separar."
            )
        if not 0.0 < separacion < 1.0:
            raise ValueError(
                f"La separacion debe estar entre 0 y 1; recibido {separacion}."
            )
        # Se ordena para aceptar tambien senales que BAJAN al activarse.
        bajo, alto = sorted((float(inactivo), float(activo)))
        recorrido = alto - bajo
        medio = bajo + recorrido / 2
        margen = recorrido * separacion / 2
        return cls(entra=medio + margen, sale=medio - margen)


@dataclass(frozen=True)
class Intervalo:
    """Un estado que ya termino, con su duracion.

    `parcial` significa que la duracion es una **cota inferior**, no el valor
    real: o no se observo el inicio (el modulo arranco con el estado ya en
    curso) o no se observo el fin (el modulo se detuvo). Marcarlo evita que un
    promedio de duraciones se contamine con intervalos truncados.

    `con_hueco` significa que durante el intervalo hubo muestras sin dato
    confiable. En el mantenedor eso pasa si la camara PTZ se reposiciona: el
    recorte deja de apuntar al vano y cualquier lectura seria inventada.
    """

    estado: str
    inicio: float
    fin: float
    duracion: float
    parcial: bool = False
    con_hueco: bool = False
    valor_medio: float | None = None


class MaquinaDeEstado:
    """Convierte una senal escalar en estados con duracion.

    El tiempo se recibe siempre desde fuera (`momento`, en segundos). La maquina
    nunca consulta el reloj: asi las pruebas son deterministas sin simular nada,
    y la misma maquina sirve a 2 Hz (una puerta) y a 25 Hz (otras senales) sin
    cambiar de comportamiento. Un antiparpadeo contado en MUESTRAS habria dado
    duraciones distintas segun la camara.
    """

    def __init__(
        self,
        histeresis: Histeresis,
        permanencia: float = PERMANENCIA_POR_DEFECTO,
        nombre_activo: str = "activo",
        nombre_inactivo: str = "inactivo",
        activo_inicial: bool = False,
        momento_inicial: float = 0.0,
    ):
        if permanencia < 0:
            raise ValueError(
                f"La permanencia no puede ser negativa; recibido {permanencia}."
            )
        self.histeresis = histeresis
        self.permanencia = float(permanencia)
        self.nombre_activo = nombre_activo
        self.nombre_inactivo = nombre_inactivo

        self._activo = bool(activo_inicial)
        self._desde = float(momento_inicial)
        # El primer intervalo nace parcial: la maquina no vio como empezo.
        self._parcial = True
        self._con_hueco = False
        self._suma = 0.0
        self._muestras = 0

        # Cambio en observacion: instante del cruce y acumulados de las
        # muestras que ya pertenecen al estado nuevo. Se guardan aparte para no
        # ensuciar el promedio del intervalo que todavia no ha terminado.
        self._candidato_desde: float | None = None
        self._suma_candidato = 0.0
        self._muestras_candidato = 0

    @property
    def estado(self) -> str:
        """Nombre del estado actual."""
        return self.nombre_activo if self._activo else self.nombre_inactivo

    @property
    def activo(self) -> bool:
        return self._activo

    @property
    def desde(self) -> float:
        """Momento en que empezo el estado actual."""
        return self._desde

    def duracion_actual(self, momento: float) -> float:
        """Cuanto lleva el estado actual. Para el cronometro en pantalla."""
        return max(0.0, float(momento) - self._desde)

    def _objetivo(self, valor: float) -> bool:
        """Que dice la senal AHORA, aplicando la histeresis.

        Estando activo hace falta caer por debajo de `sale` para desactivarse;
        estando inactivo hace falta superar `entra`. En la banda intermedia se
        conserva el estado, que es justamente el antiparpadeo.
        """
        if self._activo:
            return valor > self.histeresis.sale
        return valor > self.histeresis.entra

    def actualizar(
        self, valor: float | None, momento: float
    ) -> Intervalo | None:
        """Procesa una muestra. Devuelve el intervalo que termino, si termino.

        `valor=None` significa **sin dato confiable** (la camara se movio, el
        cuadro no se pudo leer). En ese caso no se evalua ninguna transicion y
        el intervalo en curso queda marcado con hueco: es preferible admitir que
        no se sabe antes que registrar una duracion inventada.
        """
        momento = float(momento)
        if valor is None:
            self._con_hueco = True
            # Un cambio a medio confirmar no sobrevive a la falta de datos: no
            # se puede afirmar que el estado se sostuvo si no hubo con que verlo.
            self._descartar_candidato()
            return None

        valor = float(valor)
        objetivo = self._objetivo(valor)

        if objetivo == self._activo:
            # La senal confirma el estado vigente; se cancela cualquier cambio
            # en observacion y sus muestras vuelven al intervalo en curso.
            self._descartar_candidato()
            self._suma += valor
            self._muestras += 1
            return None

        # La senal discrepa del estado vigente.
        if self._candidato_desde is None:
            self._candidato_desde = momento
        self._suma_candidato += valor
        self._muestras_candidato += 1

        if momento - self._candidato_desde < self.permanencia:
            return None

        # Cambio confirmado. El intervalo que termina lo hace en el instante del
        # cruce, no ahora, y el nuevo empieza exactamente ahi: la linea de
        # tiempo queda continua, sin huecos ni solapes.
        cruce = self._candidato_desde
        terminado = Intervalo(
            estado=self.estado,
            inicio=self._desde,
            fin=cruce,
            duracion=max(0.0, cruce - self._desde),
            parcial=self._parcial,
            con_hueco=self._con_hueco,
            valor_medio=(self._suma / self._muestras) if self._muestras else None,
        )
        self._activo = objetivo
        self._desde = cruce
        self._parcial = False
        self._con_hueco = False
        self._suma = self._suma_candidato
        self._muestras = self._muestras_candidato
        self._candidato_desde = None
        self._suma_candidato = 0.0
        self._muestras_candidato = 0
        return terminado

    def cerrar(self, momento: float) -> Intervalo | None:
        """Entrega el intervalo en curso al detener el modulo.

        Sin esto, la ultima apertura del turno no se registraria nunca. Sale
        marcado como parcial porque su fin lo impuso el paro del modulo y no el
        proceso: su duracion es una cota inferior.
        """
        momento = float(momento)
        if momento <= self._desde and self._muestras == 0:
            # Nunca se observo nada de este intervalo; no hay nada que reportar.
            return None
        return Intervalo(
            estado=self.estado,
            inicio=self._desde,
            fin=momento,
            duracion=max(0.0, momento - self._desde),
            parcial=True,
            con_hueco=self._con_hueco,
            valor_medio=(self._suma / self._muestras) if self._muestras else None,
        )

    def _descartar_candidato(self) -> None:
        """Cancela un cambio en observacion y recupera sus muestras."""
        if self._candidato_desde is None:
            return
        self._suma += self._suma_candidato
        self._muestras += self._muestras_candidato
        self._candidato_desde = None
        self._suma_candidato = 0.0
        self._muestras_candidato = 0
