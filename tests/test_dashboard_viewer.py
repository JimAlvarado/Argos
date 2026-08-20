"""Verifica el visor de capturas del dashboard sin abrir un navegador.

Comprueba que cada identificador que usa app.js exista en index.html y que la
navegacion con flechas este conectada. Atrapa errores de tipeo que de otro modo
solo se descubren haciendo clic.
"""
import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


class VisorDelDashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "app.js").read_text(encoding="utf-8")
        cls.css = (WEB / "styles.css").read_text(encoding="utf-8")
        cls.ids_html = set(re.findall(r'id="([^"]+)"', cls.html))

    def test_existen_los_elementos_del_visor(self):
        for identificador in (
            "viewer-modal", "viewer-image", "viewer-prev", "viewer-next",
            "viewer-label", "viewer-source", "viewer-time",
            "viewer-confidence", "viewer-position",
        ):
            self.assertIn(identificador, self.ids_html)

    def test_todo_id_usado_por_el_js_existe_en_el_html(self):
        usados = set(re.findall(r'\$\("#([a-zA-Z0-9_-]+)"\)', self.js))
        faltantes = sorted(usados - self.ids_html)
        self.assertEqual([], faltantes, f"ids sin elemento en index.html: {faltantes}")

    def test_las_flechas_cambian_de_captura(self):
        self.assertIn("ArrowLeft", self.js)
        self.assertIn("ArrowRight", self.js)
        self.assertIn("Escape", self.js)
        self.assertIn('addEventListener("keydown"', self.js)

    def test_el_teclado_solo_actua_con_el_visor_abierto(self):
        # Sin esta guarda, las flechas moverian capturas mientras el operador
        # navega el resto del dashboard.
        bloque = self.js[self.js.index('addEventListener("keydown"'):]
        self.assertIn("viewerIsOpen()", bloque[:200])

    def test_la_galeria_ya_no_abre_pestanas_nuevas(self):
        galeria = self.js[self.js.index("function renderEvidence"):]
        galeria = galeria[: galeria.index("async function openEvidence")]
        self.assertNotIn('target="_blank"', galeria)
        self.assertIn("data-viewer-index", galeria)

    def test_los_botones_se_desactivan_en_los_extremos(self):
        self.assertIn('$("#viewer-prev").disabled', self.js)
        self.assertIn('$("#viewer-next").disabled', self.js)

    def test_el_visor_queda_encima_de_la_galeria(self):
        # El fallo real: el visor tenia z-index 60 y .modal-backdrop tiene 80,
        # asi que la imagen se abria DETRAS de la galeria y solo se veia al
        # cerrarla. Esta prueba fija el orden de apilamiento.
        base = re.search(r"\.modal-backdrop\s*\{[^}]*z-index:\s*(\d+)", self.css)
        visor = re.search(r"\.viewer-backdrop\s*\{[^}]*z-index:\s*(\d+)", self.css)
        self.assertIsNotNone(base)
        self.assertIsNotNone(visor)
        self.assertGreater(
            int(visor.group(1)), int(base.group(1)),
            "el visor debe apilarse por encima del modal de evidencias",
        )

    def test_la_hoja_de_estilos_esta_versionada(self):
        # Sin versionar, el navegador conserva el CSS viejo y el z-index
        # corregido nunca llega al operador.
        version = re.search(r"styles\.css\?v=(\d+)", self.html)
        self.assertIsNotNone(version, "index.html debe versionar styles.css")
        self.assertGreaterEqual(int(version.group(1)), 24)

    def test_hay_estilos_para_el_visor(self):
        for clase in (".viewer-shell", ".viewer-nav", ".viewer-figure"):
            self.assertIn(clase, self.css)

    def test_la_version_del_script_subio(self):
        # index.html cachea app.js con ?v=N. Sin subirlo, el navegador sirve el
        # archivo viejo y el visor no aparece.
        version = re.search(r"app\.js\?v=(\d+)", self.html)
        self.assertIsNotNone(version, "index.html debe versionar app.js")
        self.assertGreaterEqual(int(version.group(1)), 24)


if __name__ == "__main__":
    unittest.main()
