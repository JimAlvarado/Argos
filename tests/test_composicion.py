"""Guarda de composicion: ninguna pieza puede desaparecer en un refactor.

Al partir una clase en mixins, el riesgo real no es que algo falle ruidosamente
sino que un metodo se pierda en silencio y solo reviente cuando el operador
pulse ese boton. Estas pruebas fijan la superficie publica esperada.
"""
import unittest

import detector_empresarial as det
from core.pipeline.validation import validated_line_points, validated_zone_points

# Metodos que deben existir tras componer los mixins. Si un refactor futuro
# borra uno, esta lista lo detecta antes de que el operador lo descubra.
METODOS_INTERFAZ = [
    # ui/layout.py
    "_build_ui", "_build_header", "_build_metrics", "_build_workspace",
    "_build_controls", "_build_video_panel", "_build_activity_panel",
    "_show_source_fields", "_apply_brand", "open_class_selector",
    "select_people_only", "_confidence_changed",
    # ui/geometry.py
    "begin_line_drawing", "clear_line", "begin_zone_drawing",
    "finish_zone_drawing", "clear_zone", "_on_video_click", "_on_video_drag",
    "_on_video_release", "_refresh_line_overlay", "_refresh_zone_overlay",
    # ui/alarms.py
    "_danger_sound_changed", "_browse_danger_mp3", "_restart_danger_alarm",
    "_set_danger_alarm", "_start_danger_mp3", "_stop_danger_mp3",
    # ui/models.py
    "load_model", "_model_loaded", "_model_failed", "_validate_model_file",
    "_browse_model", "_browse_video",
    # se quedan en DetectorApp
    "start_detection", "stop_detection", "_poll_queues", "_collect_config",
    "save_manual_snapshot",
]

METODOS_WORKER = [
    "_fallback_track_ids", "_update_preview_tracks", "_draw_persistent_tracks",
    "_process_crossings", "_process_zone_alerts",
    "_detection_mosaic", "_draw_zone_overlay", "_draw_counting_overlay",
    "run", "stop", "update_line", "update_zone", "reset_crossing_counts",
]


class ComposicionTest(unittest.TestCase):
    def test_la_interfaz_conserva_todos_sus_metodos(self):
        faltan = [m for m in METODOS_INTERFAZ if not hasattr(det.DetectorApp, m)]
        self.assertEqual([], faltan, f"metodos perdidos en DetectorApp: {faltan}")

    def test_el_worker_conserva_todos_sus_metodos(self):
        faltan = [m for m in METODOS_WORKER if not hasattr(det.DetectionWorker, m)]
        self.assertEqual([], faltan, f"metodos perdidos en DetectionWorker: {faltan}")

    def test_el_orden_de_composicion_es_el_esperado(self):
        mro = [c.__name__ for c in det.DetectorApp.__mro__]
        for mixin in ("LayoutMixin", "GeometryMixin", "AlarmsMixin", "ModelMixin"):
            self.assertIn(mixin, mro)
        mro_worker = [c.__name__ for c in det.DetectionWorker.__mro__]
        for mixin in ("TrackingMixin", "CrossingMixin", "ZoneMixin", "OverlayMixin"):
            self.assertIn(mixin, mro_worker)

    def test_ningun_mixin_pisa_un_metodo_de_otro(self):
        # Dos mixins que definan el mismo nombre producirian que uno gane por
        # orden de herencia y el otro se ignore en silencio.
        from ui import AlarmsMixin, GeometryMixin, LayoutMixin, ModelMixin
        from core.pipeline import CrossingMixin, OverlayMixin, TrackingMixin, ZoneMixin

        for familia in (
            (LayoutMixin, GeometryMixin, AlarmsMixin, ModelMixin),
            (TrackingMixin, CrossingMixin, ZoneMixin, OverlayMixin),
        ):
            vistos: dict[str, str] = {}
            for mixin in familia:
                propios = {n for n in vars(mixin) if not n.startswith("__")}
                for nombre in propios:
                    duplicado = vistos.get(nombre)
                    self.assertIsNone(
                        duplicado,
                        f"{nombre} esta en {mixin.__name__} y en {duplicado}",
                    )
                    vistos[nombre] = mixin.__name__


class TituloDeVentanaTest(unittest.TestCase):
    """Cada modulo debe anunciarse por lo que es.

    Regresion (19-ago-2026): el detector de PERSONAS titulaba su ventana
    "Detección de objetos". Con un modulo a la vez daba igual, pero la fase 2
    abre varias ventanas a la vez y el operador no podria distinguirlas. Es la
    misma clase de costura que el contrato de latidos: nombres que no coinciden
    entre el lanzador y el modulo.
    """

    @staticmethod
    def _titulo_de(archivo: str) -> str:
        from pathlib import Path
        import re

        fuente = (Path(__file__).resolve().parents[1] / archivo).read_text(
            encoding="utf-8")
        encontrados = re.findall(r"self\.title\(f?\"([^\"]+)\"\)", fuente)
        # El primero es el de la ventana principal; los demas son dialogos.
        return encontrados[0] if encontrados else ""

    def test_el_detector_de_personas_no_dice_objetos(self):
        titulo = self._titulo_de("detector_empresarial.py").lower()
        self.assertIn("personas", titulo)
        self.assertNotIn("objetos", titulo)

    def test_el_detector_de_objetos_si_dice_objetos(self):
        titulo = self._titulo_de("detector_objetos.py").lower()
        self.assertIn("objetos", titulo)
        self.assertNotIn("personas", titulo)


class ValidacionGeometricaTest(unittest.TestCase):
    """Los validadores salieron de DetectionWorker: ahora se prueban solos."""

    def test_conserva_una_linea_valida(self):
        puntos = [[0.1, 0.5], [0.9, 0.5]]
        self.assertEqual(puntos, validated_line_points(puntos))

    def test_repone_una_zona_invalida(self):
        repuesta = validated_zone_points([[0.1, 0.1]])
        self.assertGreaterEqual(len(repuesta), 3, "una zona necesita 3 vertices")

    def test_no_dependen_de_la_interfaz(self):
        import sys

        self.assertNotIn("ui", sys.modules.get("core.pipeline.validation").__dict__)


if __name__ == "__main__":
    unittest.main()
