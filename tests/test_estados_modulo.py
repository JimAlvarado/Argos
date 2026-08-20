"""Pruebas del modulo de estados: la senal, las estaciones y el ciclo completo.

El ciclo se prueba con video sintetico, no con analisis del codigo: el fallo que
motivo estas pruebas (comparar contra una referencia fija en vez de contra el
cuadro anterior) leia perfectamente bien y solo se vio corriendo el modulo.
"""
import queue
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

import detector_estados as de
from core.evidence import EvidenceManager
from core.storage import EventStore


class SenalRosadoTest(unittest.TestCase):
    """R menos G en el vano. Es la senal validada del mantenedor."""

    @staticmethod
    def _parche(bgr: tuple[int, int, int], tamano=(20, 30)) -> np.ndarray:
        recorte = np.zeros((tamano[0], tamano[1], 3), dtype=np.uint8)
        recorte[:, :] = bgr
        return recorte

    def test_el_gris_da_cero(self):
        # En gris R == G por definicion; es la linea base de la nave.
        for nivel in (0, 60, 128, 200, 255):
            self.assertAlmostEqual(
                0.0, de.senal_rosado(self._parche((nivel, nivel, nivel))),
                places=5)

    def test_el_magenta_da_positivo(self):
        # El resplandor del metal fundido: mucho rojo, poco verde.
        valor = de.senal_rosado(self._parche((180, 40, 230)))
        self.assertAlmostEqual(190.0, valor, places=5)

    def test_verde_sobre_rojo_da_NEGATIVO_y_no_da_la_vuelta(self):
        """Regresion del desbordamiento de uint8.

        Con `rojo - verde` en uint8, G>R da la vuelta y devuelve ~250 en vez de
        un negativo: seria un "abierto" permanente y falso. El casteo a int16
        es obligatorio, no una preferencia de estilo.
        """
        valor = de.senal_rosado(self._parche((40, 200, 30)))
        self.assertAlmostEqual(-170.0, valor, places=5)
        self.assertLess(valor, 0, "jamas debe salir positivo por desbordar")

    def test_un_recorte_vacio_no_revienta(self):
        # Una region degenerada por los deslizadores no puede tumbar el ciclo.
        self.assertEqual(0.0, de.senal_rosado(np.zeros((0, 0, 3), np.uint8)))


class EstacionesTest(unittest.TestCase):
    def test_solo_el_mantenedor_esta_calibrado(self):
        # Estado real al 19-ago-2026. Si alguien marca otra como calibrada sin
        # medirla, esta prueba lo obliga a justificarlo aqui.
        self.assertTrue(de.ESTACIONES["mantenedor"]["calibrada"])
        self.assertFalse(de.ESTACIONES["tolva"]["calibrada"])
        self.assertFalse(de.ESTACIONES["horno"]["calibrada"])

    def test_una_estacion_calibrada_trae_sus_dos_estados_medidos(self):
        # Sin los DOS extremos medidos no hay umbral posible: es la razon por la
        # que el horno sigue bloqueado (falta muestra de apagado).
        for nombre, ajustes in de.ESTACIONES.items():
            if not ajustes["calibrada"]:
                continue
            with self.subTest(estacion=nombre):
                self.assertIsNotNone(ajustes["inactivo_medido"])
                self.assertIsNotNone(ajustes["activo_medido"])
                self.assertIsNotNone(ajustes["senal"])

    def test_una_estacion_sin_calibrar_no_finge_tener_umbrales(self):
        for nombre, ajustes in de.ESTACIONES.items():
            if ajustes["calibrada"]:
                continue
            with self.subTest(estacion=nombre):
                self.assertIsNone(ajustes["inactivo_medido"])
                self.assertIsNone(ajustes["activo_medido"])

    def test_las_regiones_son_relativas_y_caben_en_el_cuadro(self):
        # Relativas para que el modulo no se rompa si cambia la resolucion o el
        # perfil RTSP de la camara.
        for nombre, ajustes in de.ESTACIONES.items():
            with self.subTest(estacion=nombre):
                r = ajustes["region"]
                self.assertGreater(r["w"], 0)
                self.assertGreater(r["h"], 0)
                self.assertLessEqual(r["x"] + r["w"], 1.0)
                self.assertLessEqual(r["y"] + r["h"], 1.0)

    def test_el_mantenedor_conserva_los_valores_medidos_en_video(self):
        # Si alguien los cambia sin remedir, el conteo de tiempo se corrompe en
        # silencio. Medido el 19-ago sobre PTZ Mantenedor Sur.
        ajustes = de.ESTACIONES["mantenedor"]
        self.assertAlmostEqual(-3.77, ajustes["inactivo_medido"], places=2)
        self.assertAlmostEqual(20.74, ajustes["activo_medido"], places=2)


RAMPA_SEGUNDOS = 2.0


def video_de_puerta(ruta: Path, fps: int = 10, tamano=(480, 270),
                    abre_en: float = 4.0, cierra_en: float = 12.0,
                    duracion: float = 16.0) -> None:
    """Nave con estructura y un vano que se pone magenta al abrirse.

    Imita lo que importa del mantenedor real, y dos detalles NO son adorno:

    - **La escena tiene estructura** (pilares, viga, linea de piso). Un fondo
      casi uniforme es patologico para la correlacion de fase: sin bordes
      repartidos no hay pico donde anclarse y cualquier mancha nueva mueve el
      resultado. Una nave real esta llena de textura, y de hecho el video real
      del 19-ago da 0.06 px de desplazamiento maximo.
    - **El resplandor entra y sale con rampa** de un par de segundos, como una
      puerta de verdad. Una aparicion instantanea entre dos cuadros no existe
      en la planta.

    La camara NO se mueve nunca: cualquier "camara movida" es un falso positivo.
    """
    escritor = cv2.VideoWriter(
        str(ruta), cv2.VideoWriter_fourcc(*"mp4v"), fps, tamano)
    assert escritor.isOpened(), "OpenCV no pudo escribir el video de prueba"
    ancho, alto = tamano
    # El vano coincide con la region relativa que se le pasa al vigilante.
    x0, y0 = int(0.32 * ancho), int(0.42 * alto)
    x1, y1 = int(0.68 * ancho), int(0.58 * alto)

    def escenario() -> np.ndarray:
        fondo = np.full((alto, ancho, 3), 105, dtype=np.uint8)
        # Viga superior y piso: bordes horizontales largos.
        fondo[: int(0.12 * alto)] = 55
        fondo[int(0.80 * alto):] = 145
        # Pilares repartidos: bordes verticales a lo ancho del cuadro.
        for fraccion in (0.06, 0.20, 0.80, 0.94):
            x = int(fraccion * ancho)
            fondo[:, x: x + max(2, ancho // 60)] = 60
        # Cuerpo del horno, gris medio, alrededor del vano.
        cv2.rectangle(fondo, (int(0.26 * ancho), int(0.30 * alto)),
                      (int(0.74 * ancho), int(0.78 * alto)), (95, 95, 95), -1)
        return fondo

    base = escenario()
    for numero in range(int(duracion * fps)):
        t = numero / fps
        cuadro = base.copy()
        # Ruido leve del sensor.
        cuadro = cv2.add(cuadro, np.random.default_rng(numero).integers(
            0, 5, cuadro.shape, dtype=np.uint8))
        if abre_en - RAMPA_SEGUNDOS < t < cierra_en + RAMPA_SEGUNDOS:
            if t < abre_en:
                alfa = (t - (abre_en - RAMPA_SEGUNDOS)) / RAMPA_SEGUNDOS
            elif t < cierra_en:
                alfa = 1.0
            else:
                alfa = 1.0 - (t - cierra_en) / RAMPA_SEGUNDOS
            alfa = max(0.0, min(1.0, alfa))
            if alfa > 0:
                capa = cuadro.copy()
                # BGR: mucho rojo, poco verde -> R-G muy positivo.
                cv2.rectangle(capa, (x0, y0), (x1, y1), (190, 40, 235), -1)
                cuadro = cv2.addWeighted(capa, alfa, cuadro, 1 - alfa, 0)
        escritor.write(cuadro)
    escritor.release()


REGION_VANO = {"x": 0.32, "y": 0.42, "w": 0.36, "h": 0.16}


class CicloCompletoTest(unittest.TestCase):
    """Corre el vigilante real sobre video sintetico."""

    def setUp(self):
        self._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.raiz = Path(self._temporal.name)
        self.store = EventStore(
            self.raiz / "eventos.db", EvidenceManager(self.raiz / "evidencias"))
        self.cola: queue.Queue = queue.Queue()

    def tearDown(self):
        self._temporal.cleanup()

    def _correr(self, ruta: Path) -> tuple[list[dict], list[dict]]:
        vigilante = de.VigilanteDeEstado(
            "mantenedor", str(ruta), ruta.name, REGION_VANO, self.cola,
            self.store, ritmo_real=False)
        vigilante.start()
        intervalos, vistas = [], []
        while True:
            dato = self.cola.get(timeout=60)
            if dato.get("fin"):
                break
            if dato.get("error"):
                self.fail(f"el ciclo fallo: {dato['error']}")
            if dato.get("intervalo"):
                intervalos.append(dato["intervalo"])
            elif not dato.get("aviso"):
                vistas.append(dato)
        vigilante.join(timeout=15)
        return intervalos, vistas

    def test_mide_la_apertura_con_su_inicio_duracion_y_cierre(self):
        """La duracion debe contener la meseta y caber en la ventana del brillo.

        No se afirma un numero exacto a proposito. El resplandor entra con
        rampa y es tan intenso que supera el umbral con un 3% de intensidad, asi
        que el cruce cae DENTRO de la rampa y no en su final. Fijar un valor
        exacto seria atar la prueba a la forma de la rampa del video sintetico,
        no a lo que el modulo debe garantizar: que la apertura medida cubra todo
        el tiempo con la puerta claramente abierta y no se pase de la ventana en
        que hubo algo de resplandor.
        """
        ruta = self.raiz / "puerta.mp4"
        video_de_puerta(ruta, abre_en=4.0, cierra_en=12.0, duracion=20.0)
        intervalos, _ = self._correr(ruta)

        aperturas = [i for i in intervalos if i["estado"] == "abierto"]
        self.assertEqual(1, len(aperturas), f"intervalos: {intervalos}")
        apertura = aperturas[0]
        meseta = 12.0 - 4.0
        ventana_con_brillo = meseta + 2 * RAMPA_SEGUNDOS
        self.assertGreaterEqual(apertura["duracion_s"], meseta)
        self.assertLessEqual(apertura["duracion_s"], ventana_con_brillo + 1.0)
        self.assertLess(apertura["inicio"], apertura["fin"])
        self.assertFalse(apertura["parcial"], "inicio y fin se observaron")
        self.assertFalse(apertura["con_hueco"])

    def test_la_camara_quieta_no_se_reporta_como_movida(self):
        """Regresion del falso positivo de PTZ.

        Comparando contra una referencia fija del arranque, la aparicion del
        resplandor hacia saltar el desplazamiento a decenas o cientos de
        pixeles con la camara inmovil: el modulo marcaba hueco todo el video y
        NO detectaba ninguna apertura. La comparacion debe ser contra el cuadro
        anterior.
        """
        ruta = self.raiz / "quieta.mp4"
        video_de_puerta(ruta, abre_en=4.0, cierra_en=12.0, duracion=20.0)
        intervalos, vistas = self._correr(ruta)

        maximo = max(v["desplazamiento"] for v in vistas)
        self.assertLess(
            maximo, de.DESPLAZAMIENTO_MAXIMO,
            f"camara inmovil reportada como movida ({maximo:.1f} px)")
        self.assertFalse(any(v["camara_movida"] for v in vistas))
        self.assertFalse(
            any(i["con_hueco"] for i in intervalos),
            "ningun intervalo deberia tener hueco con la camara quieta")

    def test_registra_en_la_base_lo_que_vera_el_tablero(self):
        ruta = self.raiz / "base.mp4"
        video_de_puerta(ruta, abre_en=4.0, cierra_en=12.0, duracion=20.0)
        self._correr(ruta)
        resumen = self.store.resumen_de_estados("mantenedor", "abierto")
        self.assertEqual(1, resumen["veces"])
        self.assertGreaterEqual(resumen["duracion_total"], 8.0)
        self.assertLessEqual(resumen["duracion_total"],
                             8.0 + 2 * RAMPA_SEGUNDOS + 1.0)
        self.assertEqual(0, resumen["parciales"])

    def test_en_solo_vista_no_escribe_nada(self):
        ruta = self.raiz / "vista.mp4"
        video_de_puerta(ruta, abre_en=4.0, cierra_en=12.0, duracion=20.0)
        vigilante = de.VigilanteDeEstado(
            "mantenedor", str(ruta), ruta.name, REGION_VANO, self.cola,
            self.store, solo_vista=True, ritmo_real=False)
        vigilante.start()
        while True:
            dato = self.cola.get(timeout=60)
            if dato.get("fin"):
                break
        vigilante.join(timeout=15)
        resumen = self.store.resumen_de_estados("mantenedor", "abierto")
        self.assertEqual(0, resumen["veces"], "solo vista no debe registrar")

    def test_una_puerta_que_nunca_abre_no_inventa_aperturas(self):
        ruta = self.raiz / "cerrada.mp4"
        video_de_puerta(ruta, abre_en=99.0, cierra_en=99.0, duracion=10.0)
        intervalos, _ = self._correr(ruta)
        self.assertEqual(
            [], [i for i in intervalos if i["estado"] == "abierto"])

    def test_el_ultimo_intervalo_no_se_pierde_al_terminar(self):
        # La puerta sigue abierta cuando acaba el video: debe quedar registrado
        # como parcial, no desaparecer.
        ruta = self.raiz / "abierta_al_final.mp4"
        video_de_puerta(ruta, abre_en=3.0, cierra_en=99.0, duracion=10.0)
        intervalos, _ = self._correr(ruta)
        aperturas = [i for i in intervalos if i["estado"] == "abierto"]
        self.assertEqual(1, len(aperturas))
        self.assertTrue(aperturas[0]["parcial"],
                        "su fin lo impuso el fin del video")


class CalibradorTest(unittest.TestCase):
    """La separacion en dos estados es lo unico no trivial del calibrador."""

    @staticmethod
    def _separar(valores):
        from tools.calibrar_estado import _dos_estados

        return _dos_estados(np.asarray(valores, dtype=float))

    def test_recupera_los_dos_niveles(self):
        senal = [-4.0] * 200 + [21.0] * 200
        inactivo, activo = self._separar(senal)
        self.assertAlmostEqual(-4.0, inactivo, delta=0.5)
        self.assertAlmostEqual(21.0, activo, delta=0.5)

    def test_no_se_sesga_por_cuanto_duro_cada_estado(self):
        """La propiedad que motivo usar medias de grupo y no percentiles.

        En el video real la puerta estuvo abierta el 84% del tiempo. Con
        percentiles del rango observado el umbral caia dentro de la
        distribucion de "abierta"; con medias de grupo el reparto no importa.
        """
        for proporcion in (0.05, 0.2, 0.5, 0.84, 0.97):
            with self.subTest(proporcion=proporcion):
                n = 1000
                abiertas = int(n * proporcion)
                senal = [21.0] * abiertas + [-4.0] * (n - abiertas)
                inactivo, activo = self._separar(senal)
                self.assertAlmostEqual(-4.0, inactivo, delta=0.5)
                self.assertAlmostEqual(21.0, activo, delta=0.5)

    def test_tolera_ruido_sobre_los_dos_niveles(self):
        generador = np.random.default_rng(7)
        senal = np.concatenate([
            generador.normal(-4.0, 1.0, 400),
            generador.normal(21.0, 2.0, 600),
        ])
        inactivo, activo = self._separar(senal)
        self.assertAlmostEqual(-4.0, inactivo, delta=1.0)
        self.assertAlmostEqual(21.0, activo, delta=1.0)

    def test_con_un_solo_estado_no_revienta(self):
        # Un video donde nunca pasa nada: debe devolver algo utilizable para
        # que el calibrador pueda avisar, no lanzar una excepcion.
        inactivo, activo = self._separar([5.0] * 100)
        self.assertLessEqual(inactivo, activo)


class ContratosDelModuloTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fuente = (Path(__file__).resolve().parents[1]
                      / "detector_estados.py").read_text(encoding="utf-8")

    def test_late_con_el_nombre_recibido(self):
        # Si el nombre del lanzador y el del modulo difieren, el supervisor lo
        # mata en bucle. Es el contrato mas caro del proyecto.
        self.assertIn('os.environ.get("ARZYZ_MODULE_ID"', self.fuente)
        self.assertIn("HeartbeatWriter(MODULO)", self.fuente)

    def test_no_toca_atributos_internos_de_los_widgets(self):
        self.assertNotIn(".value_label.configure(", self.fuente,
                         "usa MetricCard.set()")

    def test_el_video_usa_ctkimage(self):
        # Con ImageTk la vista sale borrosa en pantallas con escalado.
        self.assertIn("ctk.CTkImage(", self.fuente)
        self.assertNotIn("ImageTk.PhotoImage", self.fuente)

    def test_no_abre_la_fuente_por_su_cuenta(self):
        # La lista de rutas candidatas se prueba una por una; pasarla a OpenCV
        # falla siempre. Se usa el abridor compartido de core/camera.py.
        self.assertIn("abrir_fuente(", self.fuente)


if __name__ == "__main__":
    unittest.main()
