"""Pruebas del conteo de objetos con archivos de video y fuente de conteo.

Regresiones de los reportes de operacion (14-ago-2026):

- Al probar con un archivo de video, el modulo lo consumia a maxima velocidad
  (un video de 30 s terminaba en menos de 2) y no daba tiempo de detenerlo.
- Regla de operacion: UNA sola fuente de conteo a la vez. Con modelo elegido
  cuenta el modelo; sin modelo, la vision clasica. Nunca las dos juntas.
"""
import queue
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

import cv2
import numpy as np

import detector_objetos
from core.evidence import EvidenceManager
from core.storage import EventStore


def escribir_video(ruta: Path, cuadros: int, fps: int = 30,
                   tamano: tuple[int, int] = (64, 64)) -> None:
    """Video negro: la vision clasica no detecta nada en el."""
    escritor = cv2.VideoWriter(
        str(ruta), cv2.VideoWriter_fourcc(*"mp4v"), fps, tamano)
    assert escritor.isOpened(), f"OpenCV no pudo escribir {ruta.suffix}"
    negro = np.zeros((tamano[1], tamano[0], 3), dtype=np.uint8)
    for _ in range(cuadros):
        escritor.write(negro)
    escritor.release()


def video_con_pieza(ruta: Path, fps: int = 30) -> None:
    """Video donde la vision clasica cuenta exactamente UNA pieza.

    40 cuadros de banda vacia para aprender el fondo y luego una barra
    brillante y alargada (proporcion 3:1) que cruza la linea de conteo al
    centro. La banda lleva "eslabones" tenues en movimiento: sin ellos, los
    cuadros serian identicos y el congelador de fondo (que ignora la banda
    detenida) los tomaria por una pausa y jamas aprenderia el fondo.
    """
    tamano = (256, 512)
    escritor = cv2.VideoWriter(
        str(ruta), cv2.VideoWriter_fourcc(*"mp4v"), fps, tamano)
    assert escritor.isOpened()

    def base(cuadro_numero: int) -> np.ndarray:
        cuadro = np.zeros((tamano[1], tamano[0], 3), dtype=np.uint8)
        # eslabones tenues (brillo 60 < brillo_minimo) que avanzan 3 px/cuadro
        for y in range((cuadro_numero * 3) % 20, tamano[1], 20):
            cuadro[y:y + 3, :] = 60
        return cuadro

    for n in range(40):
        escritor.write(base(n))
    for i, y in enumerate(range(190, 268, 2)):
        cuadro = base(40 + i)
        cv2.rectangle(cuadro, (60, y), (240, y + 60), (255, 255, 255), -1)
        escritor.write(cuadro)
    escritor.release()


REGION_COMPLETA = {"x": 0, "y": 0, "w": 64, "h": 64}
REGION_PIEZA = {"x": 0, "y": 0, "w": 256, "h": 512}


class _BaseDeVideoTest(unittest.TestCase):
    """Prepara worker, cola y almacenamiento temporales. No define pruebas."""

    def setUp(self):
        self._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        raiz = Path(self._temporal.name)
        self.salida: queue.Queue = queue.Queue()
        self.db = raiz / "detecciones.db"
        self.store = EventStore(self.db, EvidenceManager(raiz / "evidencias"))
        self.evidencias = EvidenceManager(raiz / "evidencias")
        self.raiz = raiz

    def tearDown(self):
        self._temporal.cleanup()

    def _worker(self, video: Path, clase=detector_objetos.ContadorWorker,
                solo_vista=True, modelo_ruta=None, region=REGION_COMPLETA):
        return clase(
            str(video), region, 0.5, False, self.salida,
            self.store, self.evidencias, video.name, solo_vista=solo_vista,
            modelo_ruta=modelo_ruta)

    def _esperar_fin(self, timeout: float) -> list[dict]:
        """Drena la salida hasta el mensaje de fin. Falla si no llega."""
        recibido = []
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            try:
                dato = self.salida.get(timeout=0.2)
            except queue.Empty:
                continue
            recibido.append(dato)
            if dato.get("fin") or dato.get("error"):
                return recibido
        self.fail("el worker nunca reporto el fin del video")

    def _vistas(self, recibido: list[dict]) -> list[dict]:
        return [d for d in recibido if "total" in d]


class VideoComoFuenteTest(_BaseDeVideoTest):
    def test_el_video_se_reproduce_a_su_ritmo_real(self):
        """Sin marcar el paso, 2 s de video se consumian en centesimas."""
        video = self.raiz / "dos_segundos.mp4"
        escribir_video(video, cuadros=60, fps=30)
        worker = self._worker(video)
        inicio = time.monotonic()
        worker.start()
        recibido = self._esperar_fin(timeout=15)
        transcurrido = time.monotonic() - inicio
        self.assertTrue(recibido[-1].get("fin"))
        self.assertGreaterEqual(
            transcurrido, 1.2,
            f"60 cuadros a 30 fps deben tomar ~2 s, tomaron {transcurrido:.2f}")

    def test_detener_responde_de_inmediato_durante_el_video(self):
        """El operador debe poder detener la vista antes de que el video acabe."""
        video = self.raiz / "treinta_segundos.mp4"
        escribir_video(video, cuadros=900, fps=30)
        worker = self._worker(video)
        worker.start()
        # Espera la primera vista: garantiza que el ciclo ya esta corriendo.
        limite = time.monotonic() + 10
        while time.monotonic() < limite:
            try:
                if self.salida.get(timeout=0.5).get("vista") is not None:
                    break
            except queue.Empty:
                continue
        worker.detener.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive(),
                         "detener debe parar el ciclo en menos de 2 s")

    def test_un_video_mov_tambien_termina_solo(self):
        """El fin de archivo no depende de la extension (.mov fallaba)."""
        video = self.raiz / "corto.mov"
        escribir_video(video, cuadros=10, fps=30)
        worker = self._worker(video)
        worker.start()
        recibido = self._esperar_fin(timeout=10)
        self.assertTrue(recibido[-1].get("fin"))

    def _esperar_vista(self, condicion, timeout: float):
        """Espera una vista que cumpla la condicion. Falla si no llega."""
        limite = time.monotonic() + timeout
        while time.monotonic() < limite:
            try:
                dato = self.salida.get(timeout=0.2)
            except queue.Empty:
                continue
            if dato.get("vista") is not None and condicion(dato["vista"]):
                return dato
        self.fail("nunca llego la vista esperada")

    def test_la_region_se_ajusta_con_el_conteo_activo(self):
        """Cambiar la region en vivo no detiene el ciclo y se aplica sola."""
        video = self.raiz / "largo.mp4"
        escribir_video(video, cuadros=900, fps=30)
        worker = self._worker(video)
        worker.start()
        # Region inicial 64x64 -> vista procesada de 32 px de ancho.
        self._esperar_vista(lambda v: v.shape[1] == 32, timeout=10)
        worker.actualizar_region({"x": 0, "y": 0, "w": 48, "h": 48})
        self._esperar_vista(lambda v: v.shape[1] == 24, timeout=10)
        self.assertTrue(worker.is_alive(),
                        "el ajuste en vivo no debe tumbar el ciclo")
        worker.detener.set()
        worker.join(timeout=2)

    def test_una_region_fuera_del_cuadro_no_tumba_el_ciclo(self):
        """La region se recorta al cuadro real en vez de lanzar un error."""
        video = self.raiz / "region_grande.mp4"
        escribir_video(video, cuadros=60, fps=30)
        # Region 4K sobre un video de 64x64: antes esto era RuntimeError.
        worker = self._worker(video, region={"x": 1600, "y": 1140,
                                             "w": 560, "h": 1020})
        worker.start()
        recibido = self._esperar_fin(timeout=15)
        self.assertTrue(recibido[-1].get("fin"),
                        "debe terminar por fin de video, no por error")

    def test_el_umbral_de_confianza_llega_a_la_inferencia(self):
        """El deslizador Confianza gobierna el umbral real del modelo."""
        video = self.raiz / "conf.mp4"
        escribir_video(video, cuadros=1, fps=30)
        worker = self._worker(video, modelo_ruta="modelo_falso.pt")
        worker.confianza = 0.55
        capturado = {}

        class _ModeloFalso:
            # **argumentos y no una firma fija: la inferencia tambien declara
            # device y quantize, y esta prueba solo vigila el umbral.
            def predict(self, chico, conf, **argumentos):
                capturado["conf"] = conf
                return []

        worker._detectar_con_modelo(_ModeloFalso(), None)
        self.assertEqual(0.55, capturado["conf"])


class _WorkerConModeloFalso(detector_objetos.ContadorWorker):
    """Simula un modelo cuyo objeto avanza y cruza la linea una sola vez."""

    def _cargar_modelo(self):
        self._cuadros_del_modelo = 0
        return object()      # basta con que no sea None para activar el modelo

    def _detectar_con_modelo(self, modelo, chico):
        self._cuadros_del_modelo += 1
        # Caja completa (dentro de los margenes) que baja 1 px por cuadro y
        # cruza la linea (y=16 en un recorte de 32 px) una unica vez.
        y = min(5 + self._cuadros_del_modelo, 20)
        return [(10, y, 8, 6)]


class _WorkerConModeloCiego(detector_objetos.ContadorWorker):
    """Simula un modelo cargado que nunca ve nada."""

    def _cargar_modelo(self):
        return object()

    def _detectar_con_modelo(self, modelo, chico):
        return []


class FuenteUnicaDeConteoTest(_BaseDeVideoTest):
    """Regla de operacion: cuenta el modelo O la clasica, nunca ambas."""

    def test_sin_modelo_cuenta_la_vision_clasica(self):
        # Control: comprueba que el video sintetico SI es contable, para que
        # la prueba de exclusividad de abajo no pase en falso.
        video = self.raiz / "pieza.mp4"
        video_con_pieza(video)
        worker = self._worker(video, solo_vista=False, region=REGION_PIEZA)
        worker.start()
        vistas = self._vistas(self._esperar_fin(timeout=20))
        self.assertEqual("clasica", vistas[-1]["metodo"])
        self.assertEqual(1, vistas[-1]["total"],
                         "la clasica debe contar la pieza del video sintetico")

    def test_con_modelo_la_clasica_no_cuenta(self):
        # El mismo video contable, pero con un modelo que no ve nada: si el
        # total fuera 1, las dos fuentes estarian contando a la vez.
        video = self.raiz / "pieza_ignorada.mp4"
        video_con_pieza(video)
        worker = self._worker(video, clase=_WorkerConModeloCiego,
                              solo_vista=False, modelo_ruta="modelo_falso.pt",
                              region=REGION_PIEZA)
        worker.start()
        vistas = self._vistas(self._esperar_fin(timeout=20))
        self.assertEqual("modelo", vistas[-1]["metodo"])
        self.assertEqual(
            0, vistas[-1]["total"],
            "con modelo elegido la vision clasica no debe contar nada")

    def test_el_modelo_cuenta_y_queda_auditado_en_la_base(self):
        video = self.raiz / "banda_vacia.mp4"
        escribir_video(video, cuadros=60, fps=30)
        worker = self._worker(video, clase=_WorkerConModeloFalso,
                              solo_vista=False, modelo_ruta="modelo_falso.pt")
        worker.start()
        vistas = self._vistas(self._esperar_fin(timeout=15))
        self.assertEqual("modelo", vistas[-1]["metodo"])
        self.assertEqual(1, vistas[-1]["total"],
                         "el objeto simulado cruzo la linea una vez")
        worker.join(timeout=5)
        with closing(sqlite3.connect(self.db)) as conexion:
            fuentes = [fila[0] for fila in conexion.execute(
                "SELECT model_name FROM detections")]
        self.assertEqual(["modelo_falso.pt"], fuentes,
                         "cada conteo debe quedar auditado con su fuente")

    def test_en_solo_vista_el_modelo_no_cuenta(self):
        video = self.raiz / "vista.mp4"
        escribir_video(video, cuadros=30, fps=30)
        worker = self._worker(video, clase=_WorkerConModeloFalso,
                              solo_vista=True, modelo_ruta="modelo_falso.pt")
        worker.start()
        vistas = self._vistas(self._esperar_fin(timeout=10))
        self.assertEqual(0, vistas[-1]["total"],
                         "PROBAR CAMARA jamas debe sumar conteos")


class CajasDeResultadoTest(unittest.TestCase):
    """La conversion de la salida de Ultralytics no debe requerir torch."""

    class _Cajas:
        def __init__(self, xyxy):
            self.xyxy = xyxy

    class _Resultado:
        def __init__(self, xyxy):
            self.boxes = CajasDeResultadoTest._Cajas(xyxy)

    def test_convierte_esquinas_en_cajas_enteras(self):
        resultado = self._Resultado([[10.4, 20.9, 50.2, 60.7]])
        self.assertEqual([(10, 20, 39, 39)],
                         detector_objetos.cajas_de_resultado([resultado]))

    def test_tolera_resultados_vacios_o_sin_cajas(self):
        self.assertEqual([], detector_objetos.cajas_de_resultado(None))
        self.assertEqual([], detector_objetos.cajas_de_resultado([]))
        sin_boxes = type("SinBoxes", (), {"boxes": None})()
        self.assertEqual([], detector_objetos.cajas_de_resultado([sin_boxes]))

    def test_extrae_la_confianza_cuando_el_resultado_la_trae(self):
        resultado = self._Resultado([[0.0, 0.0, 10.0, 10.0]])
        resultado.boxes.conf = [0.13]
        detecciones = detector_objetos.detecciones_de_resultado([resultado])
        self.assertEqual([((0, 0, 10, 10), 0.13)], detecciones)

    def test_sin_confianza_la_deteccion_va_con_none(self):
        resultado = self._Resultado([[0.0, 0.0, 10.0, 10.0]])
        detecciones = detector_objetos.detecciones_de_resultado([resultado])
        self.assertEqual([((0, 0, 10, 10), None)], detecciones)


class EtiquetaDeCajaTest(unittest.TestCase):
    """El rotulo de cada caja: la palabra lingote, como pidio operacion."""

    def test_sin_confianza_dice_lingote(self):
        self.assertEqual("lingote", detector_objetos.etiqueta_de_caja())

    def test_con_confianza_la_muestra_como_el_detector_de_personas(self):
        self.assertEqual("lingote 0.13",
                         detector_objetos.etiqueta_de_caja(0.13))


class OverlayTest(_BaseDeVideoTest):
    """La vista dibujada no debe fallar y debe marcar cajas y linea."""

    def _worker_sin_arrancar(self):
        video = self.raiz / "nulo.mp4"
        escribir_video(video, cuadros=1, fps=30)
        return self._worker(video)

    def test_dibuja_cajas_linea_y_contador_sin_fallar(self):
        from core.pipeline.classic import ContadorDeObjetos

        worker = self._worker_sin_arrancar()
        lienzo = np.zeros((120, 160, 3), dtype=np.uint8)
        contador = ContadorDeObjetos(60, 120, 160)
        vista = worker._dibujar(lienzo, [(20, 20, 60, 18)], 60, contador)
        self.assertEqual(lienzo.shape, vista.shape)
        self.assertGreater(int(vista.sum()), 0, "algo debio dibujarse")

    def test_dibuja_con_modelo_y_confianzas(self):
        from core.pipeline.classic import ContadorDeObjetos

        worker = self._worker_sin_arrancar()
        worker._confianzas = [0.87]
        lienzo = np.zeros((120, 160, 3), dtype=np.uint8)
        contador = ContadorDeObjetos(60, 120, 160)
        vista = worker._dibujar(lienzo, [(20, 20, 60, 18)], 60, contador,
                                con_modelo=True)
        self.assertEqual(lienzo.shape, vista.shape)
        self.assertGreater(int(vista.sum()), 0)

    def test_las_esquinas_azules_no_pintan_los_lados_completos(self):
        """Solo los cuatro angulos, no el rectangulo entero (pedido)."""
        worker = self._worker_sin_arrancar()
        lienzo = np.zeros((200, 200, 3), dtype=np.uint8)
        worker._esquinas(lienzo, 40, 40, 120, 80, (255, 96, 32))
        # Las esquinas pintan; el centro de cada lado queda vacio.
        self.assertGreater(int(lienzo[40, 40:60].sum()), 0,
                           "la esquina superior izquierda debe pintarse")
        self.assertEqual(0, int(lienzo[40, 95:105].sum()),
                         "el centro del lado superior debe quedar libre")
        self.assertEqual(0, int(lienzo[75:85, 40].sum()),
                         "el centro del lado izquierdo debe quedar libre")


if __name__ == "__main__":
    unittest.main()
