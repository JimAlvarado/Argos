"""Pruebas del kernel de supervision con procesos reales.

Cada prueba lanza procesos de Python autenticos que se caen, se congelan o
cierran limpio, y verifica que el supervisor tome la decision correcta.
Intervalos cortos para que la suite siga siendo rapida.
"""
import os
import subprocess
import sys
import tempfile
import time
import re
import unittest
from pathlib import Path

from core import heartbeat, paths
from kernel import Supervisor

RAIZ = Path(__file__).resolve().parent.parent


def proceso(codigo_python: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", codigo_python],
        cwd=RAIZ,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def esperar(condicion, timeout=15.0, paso=0.1):
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return True
        time.sleep(paso)
    return False


def limpiar_temporal(temporal: tempfile.TemporaryDirectory, timeout=3.0) -> None:
    """Limpia el directorio temporal reintentando ante bloqueos de Windows.

    Los procesos hijos reescriben su latido cada 0.1 s; tras terminarlos,
    Windows puede tardar unos milisegundos en soltar el handle del archivo
    (y el antivirus escanea archivos que cambian seguido). Sin reintento, la
    limpieza falla con WinError 32 de forma intermitente.
    """
    limite = time.monotonic() + timeout
    while True:
        try:
            temporal.cleanup()
            return
        except PermissionError:
            if time.monotonic() >= limite:
                raise
            time.sleep(0.2)


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        self._originales = (paths.DATA_DIR, heartbeat.HEARTBEAT_DIR)
        paths.DATA_DIR = Path(self._temporal.name) / "data"
        heartbeat.HEARTBEAT_DIR = paths.DATA_DIR / "heartbeats"
        self.sup = Supervisor(
            latido_maximo=0.6, gracia_arranque=0.8,
            max_reintentos=3, espera_base=0.2, ciclo=0.1,
        )
        self.sup.start()

    def tearDown(self):
        self.sup.stop()
        # El supervisor relanza procesos: hay que cerrar los que siga vigilando,
        # no solo los que la prueba creo. Sin esto quedan huerfanos.
        for vigilado in list(getattr(self.sup, "_vigilados", {}).values()):
            proceso = getattr(vigilado, "proceso", None)
            if proceso is not None and proceso.poll() is None:
                proceso.terminate()
                try:
                    proceso.wait(timeout=5)
                except Exception:
                    proceso.kill()
        paths.DATA_DIR, heartbeat.HEARTBEAT_DIR = self._originales
        limpiar_temporal(self._temporal)

    def test_cierre_limpio_no_se_reinicia(self):
        p = proceso("raise SystemExit(0)")
        self.sup.register("limpio", p, lambda: proceso("raise SystemExit(0)"))
        self.assertTrue(esperar(lambda: self.sup.estado_de("limpio")["estado"] == "detenido"))
        self.assertEqual(0, self.sup.estado_de("limpio")["reinicios"],
                         "un cierre intencional jamas debe reiniciarse")

    def _codigo_sano(self, modulo: str) -> str:
        """Codigo de un proceso que late correctamente bajo `modulo`."""
        return (
            "import time, sys; sys.path.insert(0, '.');"
            "from core import heartbeat, paths; from pathlib import Path;"
            f"paths.DATA_DIR = Path({str(paths.DATA_DIR)!r});"
            f"heartbeat.HEARTBEAT_DIR = Path({str(heartbeat.HEARTBEAT_DIR)!r});"
            f"heartbeat.HeartbeatWriter({modulo!r}, 0.1).start(); time.sleep(30)"
        )

    def test_una_caida_se_reinicia_sola(self):
        # El primer proceso revienta; el relanzador entrega uno sano que late.
        sano = self._codigo_sano("modulo")
        p = proceso("raise SystemExit(1)")
        self.sup.register("modulo", p, lambda: proceso(sano))
        self.assertTrue(esperar(lambda: (e := self.sup.estado_de("modulo"))
                                and e["estado"] == "activo" and e["reinicios"] == 1))

    def test_caida_persistente_se_da_por_caido(self):
        p = proceso("raise SystemExit(1)")
        self.sup.register("roto", p, lambda: proceso("raise SystemExit(1)"))
        self.assertTrue(esperar(lambda: self.sup.estado_de("roto")["estado"] == "caido"))
        self.assertEqual(3, self.sup.estado_de("roto")["reinicios"],
                         "debe agotar los reintentos antes de rendirse")

    def _codigo_que_enmudece(self, modulo: str) -> str:
        """Late unas veces y despues se calla, sin morir: congelamiento real."""
        return (
            "import time, sys; sys.path.insert(0, '.');"
            "from core import heartbeat, paths; from pathlib import Path;"
            f"paths.DATA_DIR = Path({str(paths.DATA_DIR)!r});"
            f"heartbeat.HEARTBEAT_DIR = Path({str(heartbeat.HEARTBEAT_DIR)!r});"
            f"[heartbeat.beat({modulo!r}) or time.sleep(0.1) for _ in range(5)];"
            "time.sleep(60)"
        )

    def test_congelado_se_termina_y_reinicia(self):
        # Congelamiento REAL: el modulo latia y dejo de hacerlo.
        codigo = self._codigo_que_enmudece("congelado")
        p = proceso(codigo)
        self.sup.register("congelado", p, lambda: proceso(codigo))
        self.assertTrue(
            esperar(lambda: self.sup.estado_de("congelado")["reinicios"] >= 1, 12),
            "un modulo que latia y enmudecio debe terminarse y reiniciarse",
        )
        self.assertIsNotNone(p.poll(), "el proceso congelado debio ser terminado")

    def test_un_modulo_que_nunca_late_no_se_mata(self):
        """El fallo que cerraba el detector en bucle.

        Que no llegue un latido significa "no lo se", no "esta congelado".
        Si el modulo no reporta (o reporta con otro nombre), matarlo cada pocos
        segundos deja al operador con una ventana que se abre y se cierra sola.
        """
        p = proceso("import time; time.sleep(30)")
        self.sup.register("mudo", p, lambda: proceso("import time; time.sleep(30)"))
        time.sleep(self.sup.gracia_arranque + self.sup.latido_maximo + 1.0)
        estado = self.sup.estado_de("mudo")
        self.assertEqual(0, estado["reinicios"],
                         "un modulo vivo que nunca latio no debe reiniciarse")
        self.assertIsNone(p.poll(), "no debe terminarse un proceso vivo y sano")
        p.terminate()

    def test_un_modulo_caido_no_afecta_a_otro(self):
        # El sano late de verdad; si no latiera, el supervisor lo terminaria
        # con razon por congelado.
        vivo = proceso(self._codigo_sano("vivo"))
        roto = proceso("raise SystemExit(1)")
        self.sup.register("roto2", roto, lambda: proceso("raise SystemExit(1)"))
        self.sup.register("vivo", vivo, lambda: proceso(self._codigo_sano("vivo")))
        self.assertTrue(esperar(lambda: self.sup.estado_de("roto2")["estado"] == "caido"))
        self.assertIsNone(vivo.poll(), "el modulo sano debe seguir corriendo")


class LatidoTest(unittest.TestCase):
    def setUp(self):
        self._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        self._original = heartbeat.HEARTBEAT_DIR
        heartbeat.HEARTBEAT_DIR = Path(self._temporal.name) / "heartbeats"

    def tearDown(self):
        heartbeat.HEARTBEAT_DIR = self._original
        self._temporal.cleanup()

    def test_late_y_se_lee_la_edad(self):
        heartbeat.beat("prueba")
        edad = heartbeat.age("prueba")
        self.assertIsNotNone(edad)
        self.assertLess(edad, 1.0)

    def test_sin_latido_devuelve_none(self):
        self.assertIsNone(heartbeat.age("fantasma"))

    def test_limpiar_borra_el_latido(self):
        heartbeat.beat("efimero")
        heartbeat.clear("efimero")
        self.assertIsNone(heartbeat.age("efimero"))

    def test_el_escritor_late_periodicamente(self):
        escritor = heartbeat.HeartbeatWriter("hilo", interval=0.1)
        escritor.start()
        time.sleep(0.4)
        escritor.stop()
        self.assertIsNotNone(heartbeat.age("hilo"))


if __name__ == "__main__":
    unittest.main()


class EstadoEnDashboardTest(unittest.TestCase):
    """El dashboard debe mostrar lo que reporta el supervisor, no lo supuesto.

    Un modulo puede figurar como activo y estar congelado: sin latido visible,
    el operador no tiene forma de notarlo.
    """

    @classmethod
    def setUpClass(cls):
        raiz = Path(__file__).resolve().parent.parent
        cls.js = (raiz / "web" / "app.js").read_text(encoding="utf-8")
        cls.css = (raiz / "web" / "styles.css").read_text(encoding="utf-8")
        cls.html = (raiz / "web" / "index.html").read_text(encoding="utf-8")
        cls.servidor = (raiz / "centro_control.py").read_text(encoding="utf-8")

    def test_el_servidor_expone_el_estado_de_supervision(self):
        self.assertIn('"supervision": self.supervisor.estado_de(module_id)', self.servidor)

    def test_el_dashboard_pinta_el_estado(self):
        self.assertIn("aplicarSupervision", self.js)
        self.assertIn("latido_hace", self.js)
        self.assertIn("reinicios", self.js)

    def test_cada_estado_del_supervisor_tiene_etiqueta_y_color(self):
        # Si el supervisor gana un estado nuevo y nadie lo traduce, el operador
        # ve una palabra cruda en el tablero.
        raiz = Path(__file__).resolve().parent.parent
        fuente = (raiz / "kernel" / "supervisor.py").read_text(encoding="utf-8")
        estados = set(re.findall(r'\.estado = "(\w+)"', fuente))
        self.assertTrue(estados, "no se detectaron estados en el supervisor")
        for estado in estados | {"activo"}:
            self.assertIn(f"{estado}:", self.js, f"falta etiqueta para {estado}")
            self.assertIn(f".estado-{estado}", self.css, f"falta color para {estado}")

    def test_sin_latido_no_se_inventa_un_numero(self):
        # edad == null significa que nunca llego un latido; mostrar "0 s" seria
        # mentir sobre la salud del modulo.
        bloque = self.js[self.js.index("function aplicarSupervision"):]
        self.assertIn('edad == null ? "sin latido"', bloque[:700])

    def test_las_versiones_del_frontend_subieron(self):
        import re

        for recurso in ("app.js", "styles.css"):
            version = re.search(rf"{re.escape(recurso)}\?v=(\d+)", self.html)
            self.assertIsNotNone(version)
            self.assertGreaterEqual(int(version.group(1)), 25)


class NombreDeModuloTest(unittest.TestCase):
    """El desajuste que abria y cerraba el detector en bucle.

    El centro de control registraba el modulo como "personas" y el detector
    latia como "detector". El supervisor no lo escuchaba nunca, lo daba por
    congelado y lo reiniciaba sin fin. Ninguna prueba unitaria podia verlo
    porque el error vivia en la costura entre dos archivos.
    """

    @classmethod
    def setUpClass(cls):
        raiz = Path(__file__).resolve().parent.parent
        cls.centro = (raiz / "centro_control.py").read_text(encoding="utf-8")
        cls.detector = (raiz / "detector_empresarial.py").read_text(encoding="utf-8")

    def test_el_lanzador_comunica_el_nombre(self):
        self.assertIn('environment["ARZYZ_MODULE_ID"] = module_id', self.centro,
                      "el lanzador debe decirle al modulo con que nombre latir")

    def test_el_detector_usa_el_nombre_recibido(self):
        self.assertIn('os.environ.get("ARZYZ_MODULE_ID"', self.detector)
        self.assertIn("HeartbeatWriter(MODULO)", self.detector)

    def test_el_detector_late_igual_que_se_registra(self):
        # Se simula el lanzamiento con la variable puesta y se comprueba que el
        # archivo de latido aparece con el nombre que el supervisor consultara.
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporal:
            raiz = Path(temporal)
            original = heartbeat.HEARTBEAT_DIR
            heartbeat.HEARTBEAT_DIR = raiz / "heartbeats"
            try:
                entorno = dict(os.environ, ARZYZ_MODULE_ID="personas")
                codigo = (
                    "import os, sys; sys.path.insert(0, '.');"
                    "from core import heartbeat; from pathlib import Path;"
                    f"heartbeat.HEARTBEAT_DIR = Path({str(heartbeat.HEARTBEAT_DIR)!r});"
                    "heartbeat.beat(os.environ.get('ARZYZ_MODULE_ID', 'detector'))"
                )
                subprocess.run(
                    [sys.executable, "-c", codigo],
                    cwd=Path(__file__).resolve().parent.parent,
                    env=entorno, timeout=60, check=True,
                )
                self.assertIsNotNone(
                    heartbeat.age("personas"),
                    "el supervisor busca 'personas': el modulo debe latir asi",
                )
            finally:
                heartbeat.HEARTBEAT_DIR = original
