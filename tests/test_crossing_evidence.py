import tempfile
import unittest
from pathlib import Path

import numpy as np

import centro_control
import detector_empresarial


class CrossingEvidenceTest(unittest.TestCase):
    def test_crossing_image_is_saved_indexed_and_resolved(self):
        original_detector_base = detector_empresarial.BASE_DIR
        original_error_log = detector_empresarial.ERROR_LOG_PATH
        original_control_base = centro_control.BASE_DIR
        original_db_path = centro_control.DB_PATH

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            evidence_root = root / "data" / "evidencias"
            db_path = root / "data" / "detecciones.db"
            db_path.parent.mkdir(parents=True)

            detector_empresarial.BASE_DIR = root
            detector_empresarial.ERROR_LOG_PATH = root / "data" / "errores.log"
            centro_control.BASE_DIR = root
            centro_control.DB_PATH = db_path
            try:
                manager = detector_empresarial.EvidenceManager(evidence_root)
                store = detector_empresarial.EventStore(db_path, manager)
                image = np.zeros((120, 160, 3), dtype=np.uint8)
                evidence_path = manager.save_image(
                    image,
                    "2026-07-29 15:30:00",
                    "RTSP prueba",
                    "cruces_linea",
                    {"person": 1},
                    0.91,
                )
                crossing_id = store.insert_crossing(
                    {
                        "crossed_at": "2026-07-29 15:30:00",
                        "source": "RTSP prueba",
                        "track_id": 7,
                        "class_name": "person",
                        "direction": "A → B",
                        "confidence": 0.91,
                        "evidence_path": evidence_path,
                        "model_name": "test.pt",
                    }
                )

                self.assertTrue(evidence_path)
                self.assertFalse(Path(evidence_path).is_absolute())
                resolved = centro_control._evidence_path_by_id(
                    "crossings", crossing_id
                )
                self.assertIsNotNone(resolved)
                self.assertTrue(resolved.is_file())
                items = centro_control._evidence_data("crossings")
                self.assertEqual(1, len(items))
                self.assertEqual(crossing_id, items[0]["id"])
                self.assertIn("thumbnail=1", items[0]["thumbnail_url"])
                thumbnail = centro_control._thumbnail_bytes(
                    str(resolved), resolved.stat().st_mtime_ns
                )
                self.assertGreater(len(thumbnail), 100)
                self.assertLess(len(thumbnail), resolved.stat().st_size)
                self.assertEqual((1, 1), centro_control._evidence_counts("crossings"))
            finally:
                detector_empresarial.BASE_DIR = original_detector_base
                detector_empresarial.ERROR_LOG_PATH = original_error_log
                centro_control.BASE_DIR = original_control_base
                centro_control.DB_PATH = original_db_path


if __name__ == "__main__":
    unittest.main()
