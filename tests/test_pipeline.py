"""Pruebas del pipeline sin camara ni ventana.

Antes del paso 2 esto era imposible: la logica de cruces y zonas vivia dentro de
DetectionWorker, que exige camara, modelo y interfaz. Ahora cada mixin se puede
ejercitar con un objeto minimo que solo tenga el estado necesario.
"""
import queue
import subprocess
import sys
import threading
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

from core.pipeline import CrossingMixin, OverlayMixin, TrackingMixin, ZoneMixin


class WorkerFalso(TrackingMixin, CrossingMixin, ZoneMixin, OverlayMixin):
    """Objeto minimo con el estado que usan los mixins. No abre nada."""

    def __init__(self):
        self._state_lock = threading.RLock()
        self.config = {"class_confidence_overrides": {}}
        self.source_name = "prueba"
        self.store = None
        self.evidence = None
        self.event_queue = queue.Queue()
        self.alert_queue = queue.Queue()
        self.frame_number = 0
        # Linea de conteo
        self.line_enabled = True
        self.line_points = [(0.0, 0.5), (1.0, 0.5)]
        self.crossing_total = 0
        self.crossing_ab = 0
        self.crossing_ba = 0
        self.crossing_by_class = Counter()
        self.last_crossing = ""
        self.track_states = {}
        self.current_effective_track_ids = {}
        # Zona
        self.zone_enabled = False
        self.high_danger_zone = False
        self.zone_points = [(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]
        self.zone_alert_cooldown = 4.0
        self.zone_track_states = {}
        # Tracking de respaldo
        self.fallback_tracks = {}
        self.next_fallback_id = 1
        self.preview_tracks = {}
        self.display_tracks = {}


class PipelineAisladoTest(unittest.TestCase):
    def setUp(self):
        self.worker = WorkerFalso()

    def test_los_mixins_se_componen_sin_abrir_nada(self):
        # Si esto instancia, el pipeline quedo libre de camara, modelo e interfaz.
        for metodo in (
            "_process_crossings",
            "_process_zone_alerts",
            "_fallback_track_ids",
            "_draw_counting_overlay",
            "_draw_zone_overlay",
            "_detection_mosaic",
        ):
            self.assertTrue(
                callable(getattr(self.worker, metodo, None)),
                f"{metodo} debe estar disponible en el worker compuesto",
            )

    def test_dibuja_la_linea_de_conteo_sobre_el_cuadro(self):
        cuadro = np.zeros((240, 320, 3), dtype=np.uint8)
        self.worker.crossing_total = 7
        self.worker._draw_counting_overlay(cuadro)
        self.assertTrue(cuadro.any(), "la linea de conteo debio dibujarse")

    def test_la_zona_no_se_dibuja_si_esta_apagada(self):
        cuadro = np.zeros((240, 320, 3), dtype=np.uint8)
        self.worker.zone_enabled = False
        self.worker._draw_zone_overlay(cuadro)
        self.assertFalse(cuadro.any(), "con la zona apagada no debe pintarse nada")

    def test_la_zona_se_dibuja_si_esta_encendida(self):
        cuadro = np.zeros((240, 320, 3), dtype=np.uint8)
        self.worker.zone_enabled = True
        self.worker._draw_zone_overlay(cuadro)
        self.assertTrue(cuadro.any(), "con la zona encendida debe pintarse")

    def test_asigna_identificadores_de_respaldo_estables(self):
        # Un objeto casi en la misma posicion en dos cuadros seguidos debe
        # conservar su identificador; es lo que evita contarlo dos veces.
        nombres = {0: "person"}
        cajas = np.array([[100.0, 100.0, 140.0, 180.0]])
        clases = np.array([0])
        self.worker.frame_number = 1
        primero = self.worker._fallback_track_ids(cajas, clases, nombres, 320, 240)
        self.worker.frame_number = 2
        movidas = np.array([[103.0, 101.0, 143.0, 181.0]])
        segundo = self.worker._fallback_track_ids(movidas, clases, nombres, 320, 240)
        self.assertEqual(
            list(primero), list(segundo),
            "el mismo objeto debe conservar su id entre cuadros consecutivos",
        )

    def test_pipeline_no_arrastra_la_interfaz(self):
        codigo = (
            "import sys; import core.pipeline; "
            "print(sorted(m for m in sys.modules "
            "if m in ('tkinter', 'customtkinter', 'ultralytics', 'torch')))"
        )
        salida = subprocess.run(
            [sys.executable, "-c", codigo],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(0, salida.returncode, salida.stderr)
        self.assertEqual("[]", salida.stdout.strip())


if __name__ == "__main__":
    unittest.main()
