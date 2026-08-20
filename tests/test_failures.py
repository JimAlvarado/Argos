"""Pruebas del registro de fallas."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import failures, paths


class RegistroDeFallasTest(unittest.TestCase):
    def setUp(self):
        self._data_original = paths.DATA_DIR
        self._log_original = failures.LOG_DIR
        self._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        raiz = Path(self._temporal.name)
        paths.DATA_DIR = raiz / "data"
        failures.LOG_DIR = paths.DATA_DIR / "logs"

    def tearDown(self):
        paths.DATA_DIR = self._data_original
        failures.LOG_DIR = self._log_original
        self._temporal.cleanup()

    def _contenido(self) -> str:
        archivos = list(failures.LOG_DIR.glob("fallas_*.log"))
        self.assertEqual(1, len(archivos), "debe existir un solo archivo del dia")
        return archivos[0].read_text(encoding="utf-8")

    def test_registra_mensaje_excepcion_y_contexto(self):
        try:
            raise ValueError("camara sin senal")
        except ValueError as error:
            failures.record("camara", "no se pudo abrir", exc=error, url="rtsp://x")

        texto = self._contenido()
        self.assertIn("no se pudo abrir", texto)
        self.assertIn("ValueError: camara sin senal", texto)
        self.assertIn("rtsp://x", texto)
        self.assertIn("Traceback", texto)

    def test_capture_registra_y_relanza(self):
        with self.assertRaises(ZeroDivisionError):
            with failures.capture("inferencia"):
                1 / 0
        self.assertIn("ZeroDivisionError", self._contenido())

    def test_capture_puede_no_relanzar(self):
        with failures.capture("evidencia", relanzar=False):
            raise OSError("disco lleno")
        self.assertIn("disco lleno", self._contenido())

    def test_registrar_nunca_lanza(self):
        # Una ruta invalida no debe tumbar a quien llama.
        failures.LOG_DIR = Path("\x00ruta invalida")
        failures.record("prueba", "esto no debe explotar")

    def test_lee_las_mas_recientes(self):
        failures.record("uno", "falla antigua")
        failures.record("dos", "falla nueva")
        recientes = failures.leer_recientes(2)
        self.assertEqual(2, len(recientes))
        self.assertIn("falla nueva", recientes[0])

    def test_atrapa_caida_dentro_de_un_hilo(self):
        # Hoy un hilo que revienta desaparece en silencio. Esta es la prueba de
        # que con el registro instalado la falla queda escrita.
        codigo = (
            "import threading, sys\n"
            "from core import failures, paths\n"
            "from pathlib import Path\n"
            "paths.DATA_DIR = Path(sys.argv[1])\n"
            "failures.LOG_DIR = paths.DATA_DIR / 'logs'\n"
            "failures.configure('prueba_hilo')\n"
            "def revienta(): raise RuntimeError('worker caido')\n"
            "h = threading.Thread(target=revienta, name='worker'); h.start(); h.join()\n"
        )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporal:
            salida = subprocess.run(
                [sys.executable, "-c", codigo, temporal],
                cwd=Path(__file__).resolve().parent.parent,
                capture_output=True,
                text=True,
                timeout=120,
            )
            self.assertEqual(0, salida.returncode, salida.stderr)
            registros = list((Path(temporal) / "logs").glob("fallas_*.log"))
            self.assertEqual(1, len(registros), "el hilo caido debio dejar registro")
            texto = registros[0].read_text(encoding="utf-8")
            self.assertIn("RuntimeError: worker caido", texto)
            self.assertIn("worker", texto)


if __name__ == "__main__":
    unittest.main()
