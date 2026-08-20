"""Prueba que core/ funciona sin interfaz grafica.

Este archivo no importa detector_empresarial. Si pasa, significa que el nucleo
quedo libre de tkinter/customtkinter y puede probarse en cualquier maquina,
incluido un servidor sin pantalla.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core import paths
from core.evidence import EvidenceManager
from core.storage import EventStore
from core.utils import format_timestamp_12h


class CoreSinInterfazTest(unittest.TestCase):
    def test_core_no_arrastra_la_interfaz(self):
        # Se verifica en un proceso limpio: dentro de la suite completa otro
        # test ya pudo haber cargado la interfaz y el resultado seria falso.
        codigo = (
            "import sys; import core.storage, core.evidence, core.camera, "
            "core.config, core.packets, core.utils; "
            "print(sorted(m for m in sys.modules if m in ('tkinter', 'customtkinter')))"
        )
        salida = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(0, salida.returncode, salida.stderr)
        self.assertEqual(
            "[]",
            salida.stdout.strip(),
            "core no debe arrastrar la interfaz: rompe la separacion de capas",
        )

    def test_guarda_evidencia_y_registra_cruce(self):
        base_original = paths.BASE_DIR
        log_original = paths.ERROR_LOG_PATH
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            db_path = root / "data" / "detecciones.db"
            db_path.parent.mkdir(parents=True)
            paths.BASE_DIR = root
            paths.ERROR_LOG_PATH = root / "data" / "errores.log"
            try:
                manager = EvidenceManager(root / "data" / "evidencias")
                store = EventStore(db_path, manager)
                imagen = np.zeros((120, 160, 3), dtype=np.uint8)
                ruta = manager.save_image(
                    imagen,
                    "2026-07-29 15:30:00",
                    "RTSP prueba",
                    "cruces_linea",
                    {"person": 1},
                    0.91,
                )
                self.assertTrue(ruta)
                self.assertFalse(Path(ruta).is_absolute())

                crossing_id = store.insert_crossing(
                    {
                        "crossed_at": "2026-07-29 15:30:00",
                        "source": "RTSP prueba",
                        "track_id": 7,
                        "class_name": "person",
                        "direction": "A → B",
                        "confidence": 0.91,
                        "evidence_path": ruta,
                        "model_name": "test.pt",
                    }
                )
                self.assertIsInstance(crossing_id, int)
                self.assertTrue((root / ruta).is_file())
            finally:
                paths.BASE_DIR = base_original
                paths.ERROR_LOG_PATH = log_original

    def test_formato_de_hora(self):
        self.assertEqual(
            "2026-07-29  03:30:00 PM",
            format_timestamp_12h("2026-07-29 15:30:00"),
        )


if __name__ == "__main__":
    unittest.main()
