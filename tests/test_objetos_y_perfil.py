"""Pruebas del conteo de objetos distintos y del perfil automatico."""
import tempfile
import unittest
from pathlib import Path

import centro_control
from core import profiles
from core.evidence import EvidenceManager
from core.storage import EventStore


def evento(marcas, total, momento="2026-08-09 20:21:00"):
    return {
        "detected_at": momento,
        "source": "RTSP 172.22.5.100",
        "total": total,
        "classes": {"person": total},
        "max_confidence": 0.84,
        "evidence_path": "",
        "model_name": "yolov8n.pt",
        "track_ids": marcas,
    }


class ConteoDeObjetosTest(unittest.TestCase):
    def setUp(self):
        self._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        raiz = Path(self._temporal.name)
        self.db = raiz / "detecciones.db"
        self.store = EventStore(self.db, EvidenceManager(raiz / "evidencias"))
        self._db_original = centro_control.DB_PATH
        centro_control.DB_PATH = self.db

    def tearDown(self):
        centro_control.DB_PATH = self._db_original
        self._temporal.cleanup()

    def _objetos_de_hoy(self, dia="2026-08-09"):
        import sqlite3
        from contextlib import closing

        # closing() es obligatorio: `with conexion` solo maneja la transaccion
        # y en Python 3.14 el archivo queda retenido hasta que pasa el
        # recolector, lo que rompe la limpieza del directorio temporal.
        with closing(sqlite3.connect(self.db)) as conexion:
            return centro_control._distinct_objects(conexion, dia)

    def test_las_mismas_dos_personas_cuentan_como_dos(self):
        # Reproduce la captura reportada: 8 eventos, siempre las mismas dos
        # personas. Antes el dashboard mostraba 16 o mas "objetos".
        for _ in range(8):
            self.store.insert(evento(["ses1:1", "ses1:2"], 2))
        self.assertEqual(2, self._objetos_de_hoy())

    def test_una_caja_duplicada_no_inventa_un_objeto(self):
        # Un cuadro con 3 detecciones pero solo 2 identidades reales.
        self.store.insert(evento(["ses1:1", "ses1:2"], 2))
        self.store.insert(evento(["ses1:1", "ses1:2"], 3))
        self.assertEqual(2, self._objetos_de_hoy())

    def test_una_persona_nueva_si_suma(self):
        self.store.insert(evento(["ses1:1", "ses1:2"], 2))
        self.store.insert(evento(["ses1:1", "ses1:2", "ses1:9"], 3))
        self.assertEqual(3, self._objetos_de_hoy())

    def test_dos_sesiones_no_se_confunden(self):
        # Sin el prefijo de sesion, el objeto 1 de dos arranques distintos se
        # contaria como uno solo.
        self.store.insert(evento(["ses1:1"], 1))
        self.store.insert(evento(["ses2:1"], 1))
        self.assertEqual(2, self._objetos_de_hoy())

    def test_registros_antiguos_no_rompen_el_dashboard(self):
        self.store.insert(evento([], 2))
        self.assertIsNone(
            self._objetos_de_hoy(),
            "sin identidades debe devolver None para usar el respaldo",
        )


class PerfilAutomaticoTest(unittest.TestCase):
    def test_el_perfil_de_cpu_prioriza_velocidad(self):
        perfil = profiles.recommended_profile(gpu=False)
        self.assertEqual(640, perfil["image_size"])
        self.assertEqual(15, perfil["target_fps"])

    def test_el_perfil_de_gpu_prioriza_alcance(self):
        perfil = profiles.recommended_profile(gpu=True)
        self.assertEqual(960, perfil["image_size"])
        self.assertEqual(30, perfil["target_fps"])

    def test_el_nms_suprime_cajas_duplicadas(self):
        # 0.60 dejaba pasar duplicados sobre la misma persona.
        for gpu in (False, True):
            self.assertLess(profiles.recommended_profile(gpu)["iou"], 0.60)

    def test_solo_toca_las_cuatro_claves_del_perfil(self):
        config = {"confidence": 0.9, "source_type": "Cámara local", "ip": "10.0.0.1"}
        profiles.apply_profile(config, gpu=False)
        self.assertEqual("10.0.0.1", config["ip"], "no debe tocar otros ajustes")
        self.assertEqual(0.30, config["confidence"])

    def test_el_arranque_aplica_el_perfil(self):
        from core.config import load_config

        config = load_config()
        # El perfil esperado depende del equipo recordado: en la laptop G15
        # con RTX 3050 se aplica el de GPU y en la PC de planta el de CPU.
        # Fijarlo a CPU ataba la prueba a una sola maquina y fallaba en la
        # otra sin que hubiera nada roto.
        esperado = profiles.recommended_profile(
            gpu=config.get("last_device") == "gpu")
        for clave, valor in esperado.items():
            self.assertEqual(valor, config[clave], f"perfil en {clave}")

    def test_se_puede_desactivar_el_automatico(self):
        from core.config import DEFAULT_CONFIG

        self.assertTrue(DEFAULT_CONFIG["auto_profile"])
        self.assertIn("last_device", DEFAULT_CONFIG)


if __name__ == "__main__":
    unittest.main()
