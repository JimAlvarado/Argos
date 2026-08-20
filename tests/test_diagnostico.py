"""Pruebas del rastreador coherente y de la herramienta de diagnostico."""
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core import paths, profiles
from core.evidence import EvidenceManager
from core.storage import EventStore

RAIZ = Path(__file__).resolve().parent.parent


def umbrales_del_rastreador() -> dict:
    texto = (RAIZ / "config" / "bytetrack_arzyz.yaml").read_text(encoding="utf-8")
    return {
        clave: float(valor)
        for clave, valor in re.findall(r"^(\w+):\s*([\d.]+)\s*$", texto, re.M)
    }


class RastreadorCoherenteTest(unittest.TestCase):
    """El error original: los umbrales quedaban por debajo del filtro del
    detector, asi que nunca actuaban y cada caja creaba una identidad nueva."""

    def setUp(self):
        self.umbrales = umbrales_del_rastreador()

    def test_crear_identidad_exige_mas_que_la_confianza_operativa(self):
        for gpu in (False, True):
            confianza = profiles.recommended_profile(gpu)["confidence"]
            self.assertGreater(
                self.umbrales["new_track_thresh"], confianza,
                "una deteccion marginal no debe poder dar de alta un objeto nuevo",
            )

    def test_conservar_es_mas_facil_que_crear(self):
        self.assertLess(
            self.umbrales["track_high_thresh"], self.umbrales["new_track_thresh"],
            "mantener una identidad debe costar menos que crear una",
        )

    def test_el_umbral_bajo_queda_por_debajo_del_alto(self):
        self.assertLess(
            self.umbrales["track_low_thresh"], self.umbrales["track_high_thresh"]
        )

    def test_la_memoria_cubre_varios_segundos(self):
        fps = profiles.recommended_profile(gpu=False)["target_fps"]
        segundos = self.umbrales["track_buffer"] / fps
        self.assertGreaterEqual(
            segundos, 4.0,
            "una identidad debe sobrevivir a que la persona quede tapada",
        )


class DiagnosticoTest(unittest.TestCase):
    def setUp(self):
        self._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        raiz = Path(self._temporal.name)
        self._originales = (paths.DATA_DIR, paths.DB_PATH, paths.EVIDENCE_DIR)
        paths.DATA_DIR = raiz / "data"
        paths.DB_PATH = paths.DATA_DIR / "detecciones.db"
        paths.EVIDENCE_DIR = paths.DATA_DIR / "evidencias"
        paths.DATA_DIR.mkdir(parents=True)

    def tearDown(self):
        paths.DATA_DIR, paths.DB_PATH, paths.EVIDENCE_DIR = self._originales
        self._temporal.cleanup()

    def _sembrar(self, eventos, identidades_por_evento):
        store = EventStore(paths.DB_PATH, EvidenceManager(paths.EVIDENCE_DIR))
        for indice in range(eventos):
            store.insert({
                "detected_at": f"2026-08-09 20:{40 + indice // 60:02d}:{indice % 60:02d}",
                "source": "RTSP prueba",
                "total": len(identidades_por_evento(indice)),
                "classes": {"person": len(identidades_por_evento(indice))},
                "max_confidence": 0.8,
                "evidence_path": "",
                "model_name": "yolov8n.pt",
                "track_ids": identidades_por_evento(indice),
            })

    def test_detecta_rotacion_de_identidad(self):
        from tools import diagnostico

        # Tres personas reales, pero identidades nuevas cada tres eventos.
        self._sembrar(30, lambda i: [f"s:{(i // 3) * 3 + j}" for j in range(3)])
        archivo, datos = diagnostico.generar()
        self.assertTrue(archivo.is_file())
        self.assertEqual(3, datos["objetos_concurrentes"])
        self.assertGreaterEqual(datos["rotacion_identidad"], 3)
        self.assertTrue(
            any("Rotacion de identidad" in b for b in datos["banderas"]),
            "debe avisar del cambio constante de identificadores",
        )

    def test_no_alarma_con_seguimiento_estable(self):
        from tools import diagnostico

        self._sembrar(30, lambda i: ["s:1", "s:2", "s:3"])
        _, datos = diagnostico.generar()
        self.assertEqual(1.0, datos["rotacion_identidad"])
        self.assertFalse(any("Rotacion" in b for b in datos["banderas"]))

    def test_el_reporte_no_incluye_secretos(self):
        from tools import diagnostico

        self._sembrar(3, lambda i: ["s:1"])
        archivo, _ = diagnostico.generar()
        texto = archivo.read_text(encoding="utf-8")
        self.assertNotIn("password", texto.lower().replace("(oculto)", ""))

    def test_genera_json_ademas_del_texto(self):
        from tools import diagnostico

        self._sembrar(3, lambda i: ["s:1"])
        archivo, _ = diagnostico.generar()
        gemelo = archivo.with_suffix(".json")
        self.assertTrue(gemelo.is_file())
        json.loads(gemelo.read_text(encoding="utf-8"))

    def test_funciona_sin_base_de_datos(self):
        from tools import diagnostico

        archivo, _ = diagnostico.generar()
        self.assertTrue(archivo.is_file(), "no debe fallar en una instalacion nueva")


if __name__ == "__main__":
    unittest.main()


class BotonDelDashboardTest(unittest.TestCase):
    """El reporte debe poder generarse desde el dashboard, sin linea de comandos."""

    @classmethod
    def setUpClass(cls):
        cls.servidor = (RAIZ / "centro_control.py").read_text(encoding="utf-8")
        cls.html = (RAIZ / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")

    def test_el_boton_existe_en_el_dashboard(self):
        self.assertIn('id="generar-diagnostico"', self.html)
        self.assertIn('$("#generar-diagnostico")', self.js)

    def test_hay_endpoint_para_generar(self):
        self.assertIn('"/api/diagnostico"', self.servidor)
        self.assertIn("_generar_diagnostico", self.servidor)

    def test_el_reporte_se_lee_dentro_del_dashboard(self):
        self.assertIn('"/api/diagnostico/archivo"', self.servidor)
        # "inline" y no "attachment": el archivo no debe salir de la carpeta
        # del proyecto hacia Descargas ni ninguna otra ruta del sistema.
        self.assertIn("inline; filename=", self.servidor)
        self.assertNotIn("attachment; filename=", self.servidor)
        self.assertIn('id="reporte-modal"', self.html)
        self.assertIn('$("#reporte-texto")', self.js)

    def test_la_ruta_informada_es_relativa_al_proyecto(self):
        # Nunca debe mostrarse una ruta absoluta con el usuario de Windows.
        self.assertIn("archivo.relative_to(BASE_DIR)", self.servidor)

    def test_el_reporte_se_puede_copiar(self):
        self.assertIn('id="reporte-copiar"', self.html)
        self.assertIn("clipboard.writeText", self.js)

    def test_la_descarga_no_permite_salir_de_la_carpeta(self):
        # Sin esta comprobacion, name=../../config.json serviria cualquier
        # archivo del proyecto por HTTP.
        bloque = self.servidor[self.servidor.index("def _descargar_diagnostico"):]
        self.assertIn("destino.parent != carpeta", bloque[:900])
        self.assertIn(".resolve()", bloque[:900])

    def test_las_versiones_del_frontend_subieron(self):
        for recurso in ("app.js", "styles.css"):
            version = re.search(rf"{re.escape(recurso)}\?v=(\d+)", self.html)
            self.assertIsNotNone(version, f"{recurso} debe estar versionado")
            self.assertGreaterEqual(int(version.group(1)), 24)


class CapturadorTest(unittest.TestCase):
    """El capturador debe proponer cajas validas y no inventar objetos."""

    def setUp(self):
        import numpy as np
        from tools import capturador

        self.cap = capturador
        self.np = np
        # Banda vacia: fondo oscuro con textura
        self.fondo = (np.random.default_rng(7).integers(20, 45, (1020, 560))
                      ).astype(np.uint8)

    def test_banda_vacia_no_produce_cajas(self):
        gris = self.fondo.copy()
        self.assertEqual([], self.cap.detectar(gris, self.fondo))

    def test_detecta_un_lingote(self):
        gris = self.fondo.copy()
        gris[500:550, 150:355] = 200          # barra alargada y brillante
        cajas = self.cap.detectar(gris, self.fondo)
        self.assertEqual(1, len(cajas))
        x, y, w, h = cajas[0]
        self.assertGreater(w, h, "el lingote es mas ancho que alto")

    def test_ignora_manchas_redondas_como_el_vapor(self):
        import cv2

        gris = self.fondo.copy()
        cv2.circle(gris, (280, 500), 60, 220, -1)   # nube: brillante pero difusa
        self.assertEqual([], self.cap.detectar(gris, self.fondo),
                         "una mancha redonda no es material")

    def test_ignora_la_zona_de_la_maquina_y_la_rejilla(self):
        for fila in (60, 960):
            gris = self.fondo.copy()
            gris[fila:fila + 40, 150:355] = 210
            self.assertEqual([], self.cap.detectar(gris, self.fondo),
                             f"la fila {fila} esta fuera de la banda")

    def test_avisa_si_cambio_el_encuadre(self):
        # La camara puede reposicionarse desde su configuracion: si la region
        # deja de caer sobre la banda hay que avisar, no guardar basura.
        negro = self.np.zeros((1020, 560), dtype=self.np.uint8)
        self.assertIsNotNone(self.cap.comprobar_encuadre(negro))
        plano = self.np.full((1020, 560), 90, dtype=self.np.uint8)
        self.assertIsNotNone(self.cap.comprobar_encuadre(plano))
        # Estructura equivalente a la banda real (desviacion medida: 41 de dia)
        banda = self.np.random.default_rng(3).integers(
            10, 190, (1020, 560)).astype(self.np.uint8)
        self.assertIsNone(self.cap.comprobar_encuadre(banda))


class ReporteReforzadoTest(unittest.TestCase):
    """Secciones que se agregaron tras un congelamiento real en planta.

    El modulo se congelo 11 s y el supervisor lo reinicio. El reporte registro
    el sintoma pero no la causa: el proyecto vivia dentro de OneDrive y las
    evidencias crecian 611 MB por hora.
    """

    def setUp(self):
        self._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        raiz = Path(self._temporal.name)
        self._orig = (paths.DATA_DIR, paths.DB_PATH, paths.EVIDENCE_DIR)
        paths.DATA_DIR = raiz / "data"
        paths.DB_PATH = paths.DATA_DIR / "d.db"
        paths.EVIDENCE_DIR = paths.DATA_DIR / "evidencias"
        paths.DATA_DIR.mkdir(parents=True)

    def tearDown(self):
        paths.DATA_DIR, paths.DB_PATH, paths.EVIDENCE_DIR = self._orig
        self._temporal.cleanup()

    def _sembrar_dos_modulos(self):
        store = EventStore(paths.DB_PATH, EvidenceManager(paths.EVIDENCE_DIR))
        for i in range(20):                    # objetos: sin identidades
            store.insert({
                "detected_at": f"2026-08-12 13:29:{i:02d}", "source": "banda.mp4",
                "total": 1, "classes": {"lingote": 1}, "max_confidence": 1.0,
                "evidence_path": "", "model_name": "vision-clasica",
                "track_ids": [],
            })
        for i in range(6):                     # personas: identidades que rotan
            store.insert({
                "detected_at": f"2026-08-12 13:30:{i:02d}", "source": "RTSP 10.0.0.1",
                "total": 1, "classes": {"person": 1}, "max_confidence": 0.8,
                "evidence_path": "", "model_name": "yolov8n.pt",
                "track_ids": [f"ses:{i}"],
            })

    def test_la_rotacion_se_atribuye_solo_a_quien_guarda_identidades(self):
        from tools import diagnostico

        self._sembrar_dos_modulos()
        archivo, datos = diagnostico.generar()
        texto = archivo.read_text(encoding="utf-8")
        rotacion = [b for b in datos["banderas"] if "Rotacion" in b]
        self.assertTrue(rotacion, "debe detectar la rotacion")
        self.assertIn("RTSP 10.0.0.1", rotacion[0],
                      "debe nombrar la fuente responsable")
        self.assertNotIn("banda.mp4", rotacion[0],
                         "el modulo de objetos no usa identidades")
        self.assertIn("Fuentes analizadas", texto)

    def test_separa_la_actividad_por_fuente_y_modelo(self):
        from tools import diagnostico

        self._sembrar_dos_modulos()
        archivo, datos = diagnostico.generar()
        texto = archivo.read_text(encoding="utf-8")
        self.assertIn("ACTIVIDAD POR FUENTE Y MODELO", texto)
        self.assertIn("vision-clasica", texto)
        self.assertIn("yolov8n.pt", texto)
        self.assertEqual(2, len(datos["fuentes"]))

    def test_avisa_de_carpetas_sincronizadas(self):
        from tools import diagnostico

        original = diagnostico.RAIZ
        try:
            diagnostico.RAIZ = Path(r"C:\Users\x\OneDrive - Arzyz\proyecto")
            reporte = diagnostico.Reporte()
            diagnostico._almacenamiento(reporte, {})
            avisos = [b for b in reporte.banderas if "OneDrive" in b]
            self.assertTrue(avisos, "debe avisar de OneDrive")
            self.assertIn("congelar", avisos[0])
        finally:
            diagnostico.RAIZ = original

    def test_proyecta_el_crecimiento_de_evidencias(self):
        from tools import diagnostico

        reporte = diagnostico.Reporte()
        # 68 imagenes de 43.7 MB: el caso real medido en planta
        diagnostico._almacenamiento(reporte, {"evidencias": 68, "evidencias_mb": 43.7})
        texto = str(reporte)
        self.assertIn("Tamano medio por evidencia", texto)
        self.assertIn("Crecimiento estimado", texto)
        self.assertTrue(any("por turno" in b for b in reporte.banderas),
                        "debe avisar del crecimiento por turno")

    def test_cuenta_las_intervenciones_del_supervisor(self):
        from core import failures
        from tools import diagnostico

        failures.LOG_DIR = paths.DATA_DIR / "logs"
        failures.configure("centro_control")
        failures.record("supervisor", "objetos congelado (latido hace 11s); se termina",
                        nivel="WARNING")
        failures.record("supervisor", "objetos reiniciado automaticamente (intento 1)",
                        nivel="WARNING")
        reporte = diagnostico.Reporte()
        diagnostico._salud_de_modulos(reporte, {})
        texto = str(reporte)
        self.assertIn("Intervenciones del supervisor", texto)
        self.assertIn("congelamiento", texto)
        self.assertTrue(any("reiniciar modulos" in b for b in reporte.banderas))
