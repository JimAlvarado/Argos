"""Pruebas de la deduplicacion de evidencias.

El caso que motiva esto: dos personas quietas frente a una camara RTSP generaban
una captura cada 3 segundos, indefinidamente.
"""
import unittest

from core.pipeline import SceneDeduplicator


class DeduplicacionTest(unittest.TestCase):
    def setUp(self):
        self.dedup = SceneDeduplicator(refresh_seconds=300.0)

    def test_la_primera_escena_siempre_se_guarda(self):
        self.assertTrue(self.dedup.should_record([1, 2], {"person": 2}, now=0.0))

    def test_la_misma_escena_no_se_repite(self):
        self.dedup.should_record([1, 2], {"person": 2}, now=0.0)
        for segundo in (3.0, 6.0, 9.0, 60.0, 120.0):
            self.assertFalse(
                self.dedup.should_record([1, 2], {"person": 2}, now=segundo),
                f"no debe guardarse de nuevo en el segundo {segundo}",
            )

    def test_escenario_real_de_la_captura(self):
        # Dos personas quietas durante 6 minutos, un cuadro cada 3 segundos.
        pasos = 121  # de 0 a 360 segundos
        guardadas = sum(
            1 for paso in range(pasos)
            if self.dedup.should_record([1, 2], {"person": 2}, now=paso * 3.0)
        )
        # Antes: 121 imagenes casi identicas. Ahora: la primera y una de
        # refresco al cumplirse los 300 segundos.
        self.assertEqual(2, guardadas)
        self.assertEqual(pasos - 2, self.dedup.omitidas)

    def test_un_objeto_nuevo_dispara_evidencia(self):
        self.dedup.should_record([1, 2], {"person": 2}, now=0.0)
        self.assertTrue(
            self.dedup.should_record([1, 2, 7], {"person": 3}, now=3.0),
            "si entra alguien nuevo debe quedar constancia",
        )

    def test_cambiar_de_clase_dispara_evidencia(self):
        self.dedup.should_record([1], {"person": 1}, now=0.0)
        self.assertTrue(self.dedup.should_record([1], {"car": 1}, now=3.0))

    def test_que_se_vaya_un_objeto_tambien_cuenta(self):
        self.dedup.should_record([1, 2], {"person": 2}, now=0.0)
        self.assertTrue(self.dedup.should_record([1], {"person": 1}, now=3.0))

    def test_el_refresco_deja_constancia_periodica(self):
        self.dedup.should_record([1], {"person": 1}, now=0.0)
        self.assertFalse(self.dedup.should_record([1], {"person": 1}, now=299.0))
        self.assertTrue(self.dedup.should_record([1], {"person": 1}, now=300.0))

    def test_el_orden_de_los_objetos_no_importa(self):
        self.dedup.should_record([2, 1], {"person": 2}, now=0.0)
        self.assertFalse(self.dedup.should_record([1, 2], {"person": 2}, now=3.0))

    def test_sin_identificadores_usa_el_conteo_por_clase(self):
        # Si el modelo no entrega seguimiento, el conteo evita el aluvion.
        self.dedup.should_record([], {"person": 2}, now=0.0)
        self.assertFalse(self.dedup.should_record([], {"person": 2}, now=3.0))
        self.assertTrue(self.dedup.should_record([], {"person": 4}, now=6.0))

    def test_se_puede_desactivar(self):
        abierto = SceneDeduplicator(enabled=False)
        for paso in range(5):
            self.assertTrue(
                abierto.should_record([1], {"person": 1}, now=paso * 3.0),
                "desactivado debe comportarse como antes",
            )

    def test_reiniciar_olvida_la_escena(self):
        self.dedup.should_record([1], {"person": 1}, now=0.0)
        self.dedup.reset()
        self.assertTrue(self.dedup.should_record([1], {"person": 1}, now=3.0))


class ConfiguracionDeDeduplicacionTest(unittest.TestCase):
    def test_las_claves_existen_por_defecto(self):
        from core.config import DEFAULT_CONFIG

        self.assertTrue(DEFAULT_CONFIG["evidence_dedup"])
        self.assertEqual(300.0, DEFAULT_CONFIG["evidence_refresh_seconds"])
        self.assertGreaterEqual(DEFAULT_CONFIG["config_version"], 9)


if __name__ == "__main__":
    unittest.main()
