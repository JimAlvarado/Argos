"""Pruebas de la estructura de la ventana del modulo de objetos.

Regresion del reporte de operacion (14-ago-2026): al pulsar PROBAR CAMARA o
INICIAR CONTEO aparecia la imagen y los botones DESAPARECIAN. La causa fue el
orden de empaquetado: el lienzo iba primero y los botones al final, y en pack
el ultimo empaquetado es el primero en quedarse sin espacio cuando la imagen
crece. La ventana se construye de verdad (no solo analisis de codigo): esa fue
la leccion de la correccion 0.8.1.
"""
import tempfile
import unittest
from pathlib import Path

from core import config as core_config
from core import paths


class VentanaDeObjetosTest(unittest.TestCase):
    """Construye la ventana real una sola vez y examina su estructura."""

    @classmethod
    def setUpClass(cls):
        cls._temporal = tempfile.TemporaryDirectory(dir=Path.cwd())
        raiz = Path(cls._temporal.name)
        (raiz / "data" / "evidencias").mkdir(parents=True)
        cls._originales = (paths.DATA_DIR, paths.EVIDENCE_DIR, paths.DB_PATH,
                           core_config.CONFIG_PATH)
        paths.DATA_DIR = raiz / "data"
        paths.EVIDENCE_DIR = raiz / "data" / "evidencias"
        paths.DB_PATH = raiz / "data" / "detecciones.db"
        core_config.CONFIG_PATH = raiz / "data" / "config.json"

        import detector_objetos

        cls.app = detector_objetos.AppObjetos()
        cls.app.withdraw()
        cls.app.update_idletasks()

    @classmethod
    def tearDownClass(cls):
        cls.app._cerrar()
        (paths.DATA_DIR, paths.EVIDENCE_DIR, paths.DB_PATH,
         core_config.CONFIG_PATH) = cls._originales
        cls._temporal.cleanup()

    def test_los_botones_no_pueden_ser_empujados_por_la_imagen(self):
        """Botones y registro anclados abajo ANTES que el lienzo.

        En pack, el ultimo empaquetado es el primero en perder espacio. Si el
        lienzo tuviera prioridad, la imagen empujaria los botones fuera de la
        ventana, que es exactamente el bug reportado.
        """
        acciones = self.app.b_iniciar.master
        registro = self.app.registro.master
        panel = self.app.lienzo.master
        self.assertIs(panel, acciones.master,
                      "botones y lienzo deben compartir el mismo panel")

        self.assertEqual("bottom", acciones.pack_info()["side"],
                         "los botones deben anclarse al fondo del panel")
        self.assertEqual("bottom", registro.pack_info()["side"],
                         "el registro debe anclarse al fondo del panel")

        orden = [str(w) for w in panel.pack_slaves()]
        self.assertLess(
            orden.index(str(acciones)), orden.index(str(self.app.lienzo)),
            "los botones deben empaquetarse antes que el lienzo: el que va "
            "al final es el que cede espacio cuando la imagen crece")
        self.assertLess(
            orden.index(str(registro)), orden.index(str(self.app.lienzo)),
            "el registro debe empaquetarse antes que el lienzo")

    def test_la_region_se_ajusta_con_deslizadores(self):
        """La region ya no se teclea: cuatro deslizadores, sin valores invalidos."""
        self.assertEqual({"x", "y", "w", "h"},
                         set(self.app.region_sliders.keys()))
        region = self.app._region()
        for clave in ("x", "y", "w", "h"):
            self.assertIsInstance(region[clave], int)
        # Ajuste real medido: la camara se movio y x paso de 1600 a 1544.
        self.app.region_sliders["x"].set(1544)
        self.assertEqual(1544, self.app._region()["x"])

    def test_los_tres_botones_existen_con_sus_comandos(self):
        for boton, texto in ((self.app.b_iniciar, "INICIAR"),
                             (self.app.b_detener, "DETENER"),
                             (self.app.b_probar, "PROBAR")):
            self.assertIn(texto, boton.cget("text"))

    def test_la_tarjeta_indica_la_fuente_de_conteo(self):
        """Regla de operacion: una sola fuente a la vez; la tarjeta lo dice."""
        self.assertEqual("CLÁSICA",
                         self.app.m_fuente.value_label.cget("text"))

    def test_el_boton_de_fuente_va_arriba_y_evidencias_al_fondo(self):
        """El selector CLASICA/MODELO abre el panel; evidencias lo cierra."""
        self.assertEqual(["CLÁSICA", "MODELO"],
                         list(self.app.fuente_conteo.cget("values")))
        interior = self.app.evidencia.master
        orden = [str(w) for w in interior.pack_slaves()]
        self.assertEqual(0, orden.index(str(self.app.fuente_conteo)),
                         "la fuente de conteo va bajo la palabra CONFIGURACIÓN")
        self.assertEqual(len(orden) - 1, orden.index(str(self.app.evidencia)),
                         "guardar evidencias va hasta abajo del panel")

    def test_las_secciones_del_panel_se_pliegan_y_despliegan(self):
        seccion = self.app.sec_region
        self.assertFalse(seccion.abierta, "la region inicia plegada")
        self.assertFalse(seccion.contenido.winfo_manager())
        seccion.alternar()
        self.assertTrue(seccion.abierta)
        self.assertEqual("pack", seccion.contenido.winfo_manager())
        seccion.alternar()
        self.assertFalse(seccion.contenido.winfo_manager())

    def test_mover_un_deslizador_actualiza_la_region_del_worker_en_vivo(self):
        """Con el conteo activo, la region viaja al worker sin reiniciar."""
        recibidas = []

        class _WorkerFalso:
            def is_alive(self):
                return True

            def actualizar_region(self, region):
                recibidas.append(region)

        self.app.worker = _WorkerFalso()
        try:
            self.app.region_sliders["x"].set(1544)
            self.app._region_cambio("x", 1544)
        finally:
            self.app.worker = None
        self.assertTrue(recibidas, "el worker debe recibir la region nueva")
        self.assertEqual(1544, recibidas[-1]["x"])

    def test_la_confianza_se_muestra_y_viaja_al_worker_en_vivo(self):
        """El deslizador Confianza funciona como en el detector de personas."""

        class _WorkerFalso:
            confianza = None

            def is_alive(self):
                return True

        worker = _WorkerFalso()
        self.app.worker = worker
        try:
            self.app.confianza.set(0.30)
            self.app._confianza_cambio(0.30)
            self.assertEqual("Confianza: 30%",
                             self.app.confianza_txt.cget("text"))
            self.assertEqual(0.30, worker.confianza)
        finally:
            self.app.worker = None


if __name__ == "__main__":
    unittest.main()
