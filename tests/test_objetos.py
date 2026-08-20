"""Pruebas del modulo de deteccion y conteo de objetos.

Las reglas que se fijan aqui salen de mediciones sobre video real de planta,
contrastadas con un conteo manual verificado por el operador.
"""
import re
import unittest
from pathlib import Path

import numpy as np

from core.pipeline.classic import (
    Ajustes,
    ContadorDeObjetos,
    ModeloDeFondo,
    detectar,
)

RAIZ = Path(__file__).resolve().parent.parent


def banda_vacia(alto=510, ancho=280):
    return np.random.default_rng(11).integers(15, 45, (alto, ancho)).astype(np.uint8)


def con_pieza(fondo, y, alto=25, ancho=100, x=90, brillo=190):
    cuadro = fondo.copy()
    cuadro[y:y + alto, x:x + ancho] = brillo
    return cuadro


class DeteccionTest(unittest.TestCase):
    def setUp(self):
        self.fondo = banda_vacia()

    def test_la_banda_vacia_no_produce_cajas(self):
        self.assertEqual([], detectar(self.fondo, self.fondo))

    def test_detecta_una_pieza(self):
        cajas = detectar(con_pieza(self.fondo, 250), self.fondo)
        self.assertEqual(1, len(cajas))
        _, _, w, h = cajas[0]
        self.assertGreater(w, h, "la pieza es mas ancha que alta")

    def test_el_vapor_no_cuenta_como_pieza(self):
        import cv2

        cuadro = self.fondo.copy()
        cv2.circle(cuadro, (140, 250), 45, 210, -1)   # nube: brillante y redonda
        self.assertEqual([], detectar(cuadro, self.fondo))

    def test_ignora_la_maquina_y_la_rejilla(self):
        for y in (30, 470):
            self.assertEqual([], detectar(con_pieza(self.fondo, y), self.fondo),
                             f"la fila {y} esta fuera de la banda")


class FondoTest(unittest.TestCase):
    def test_la_mediana_ignora_el_material_de_paso(self):
        # Con mediana, una pieza que pasa no contamina el fondo. Con promedio si.
        fondo = banda_vacia()
        modelo = ModeloDeFondo(memoria=20, cada=1)
        for i in range(20):
            cuadro = con_pieza(fondo, 250) if i in (5, 6) else fondo
            modelo.actualizar(cuadro)
        self.assertLess(int(modelo.fondo[255, 140]), 60,
                        "el fondo no debe aprenderse la pieza")

    def test_una_pausa_de_produccion_no_envenena_el_fondo(self):
        # Medido en el colado del 14-ago: 70 minutos de pausas con piezas
        # quietas a la vista. La mediana se las aprendia como fondo y al
        # reanudar eran invisibles. Con la banda detenida (cuadros casi
        # identicos) el fondo NO debe alimentarse.
        import numpy as np

        fondo = banda_vacia()
        modelo = ModeloDeFondo(memoria=20, cada=1)
        azar = np.random.RandomState(20260815)
        for _ in range(10):
            # Banda andando: el cambio real medido entre cuadros es de 3.7 a
            # 9.0 niveles (muestras dia/noche); se simula con ruido por cuadro.
            con_ruido = fondo.astype(np.int16) + azar.randint(
                -5, 6, fondo.shape)
            modelo.actualizar(np.clip(con_ruido, 0, 255).astype(np.uint8))
        quieta = con_pieza(fondo, 250)
        for _ in range(60):                      # pausa: pieza quieta a la vista
            modelo.actualizar(quieta.copy())
        self.assertLess(int(modelo.fondo[255, 140]), 60,
                        "una pieza quieta durante una pausa no es fondo")


class ConteoTest(unittest.TestCase):
    def setUp(self):
        self.c = ContadorDeObjetos(linea_y=270, alto=510, ancho=280)

    def _bajar(self, desde, hasta, paso=20, ancho=100, alto=25, x=90):
        for y in range(desde, hasta, paso):
            self.c.actualizar([(x, y, ancho, alto)])

    def test_una_pieza_que_cruza_se_cuenta_una_vez(self):
        self._bajar(150, 400)
        self.assertEqual(1, self.c.total)

    def test_no_se_cuenta_dos_veces_por_parpadeo(self):
        # El fallo medido en video: sin tolerancia, la mascara parte una pieza
        # en dos y el conteo se inflaba al 193%.
        for y in range(150, 400, 20):
            self.c.actualizar([] if y in (230, 250) else [(90, y, 100, 25)])
        self.assertEqual(1, self.c.total, "el parpadeo no debe duplicar el conteo")

    def test_una_pieza_incompleta_no_se_cuenta(self):
        # Regla acordada con operacion: solo cuenta la pieza completa.
        for y in range(150, 400, 20):
            self.c.actualizar([(-30, y, 100, 25)])   # cortada por el borde
        self.assertEqual(0, self.c.total)

    def test_dos_piezas_se_cuentan_por_separado(self):
        # Cadencia realista: la pieza mas proxima real llega 35
        # actualizaciones despues (2.8 s a 12.5 fps, medido). Aqui van
        # separadas 320 px a 8 px por cuadro = 40 actualizaciones.
        for y in range(150, 750, 8):
            cajas = [(60, y, 90, 25), (60, y - 320, 90, 25)]
            self.c.actualizar([c for c in cajas if 0 <= c[1] < 480])
        self.assertEqual(2, self.c.total)

    def test_una_gemela_tardia_del_modelo_no_cuenta_doble(self):
        # Medido con el modelo v2 (15-ago): una segunda caja sobre la MISMA
        # pieza genera una pista gemela que cruza 6 a 16 actualizaciones
        # despues de la real. La pieza real mas cercana llega a las 35.
        for y in range(150, 390, 10):
            # pieza real + fragmento desplazado 80 px arriba (misma pieza)
            self.c.actualizar([(90, y, 100, 25), (95, y - 80, 90, 20)])
        # la real cruza primero; el fragmento cruza 8 actualizaciones despues
        self.assertEqual(1, self.c.total,
                         "dos cajas de la misma pieza son UNA pieza")

    def test_un_cruce_por_salto_de_pista_recien_nacida_no_cuenta(self):
        # Medido con v2: pista nacida junto a la linea, vista 2 veces, que
        # salta mas de dos alturas de pieza en una asociacion y "cruza".
        # Una pieza real ocluida por el tubo nace ARRIBA y viene rastreada:
        # esa si cuenta (test_una_oclusion_sobre_la_linea...).
        self.c.actualizar([(90, 230, 100, 25)])      # nace a 27 px de la linea
        self.c.actualizar([(90, 238, 100, 25)])
        self.c.actualizar([])                        # una ausencia
        self.c.actualizar([(90, 310, 100, 25)])      # salto de 72 px: "cruza"
        self.assertEqual(0, self.c.total,
                         "un salto asi en una pista recien nacida es ruido")

    def test_no_se_cuenta_antes_de_la_linea(self):
        self._bajar(150, 260)
        self.assertEqual(0, self.c.total)

    def test_reiniciar_pone_el_conteo_a_cero(self):
        self._bajar(150, 400)
        self.c.reiniciar()
        self.assertEqual(0, self.c.total)

    def test_una_redeteccion_junto_a_la_linea_no_cuenta_doble(self):
        # El doble conteo reportado en planta (14-ago): la pieza cruza y se
        # cuenta; una caja PARCIAL (solo la punta detectada) desplaza el
        # centro hacia arriba mas alla de la tolerancia de asociacion, nace
        # una pista nueva pegada a la linea, y al volver la caja completa el
        # centro re-cruza: la misma pieza sumaba dos veces.
        self._bajar(150, 300)
        self.assertEqual(1, self.c.total)
        self.c.actualizar([(90, 290, 100, 25)])   # la pieza sigue su camino
        self.c.actualizar([(90, 250, 100, 12)])   # caja parcial junto a la linea
        self.c.actualizar([(90, 262, 100, 25)])   # el centro re-cruza
        self.assertEqual(1, self.c.total,
                         "la re-deteccion de una pieza contada no debe sumar")

    def test_una_oclusion_sobre_la_linea_no_pierde_el_conteo(self):
        # La falla primordial reportada en planta (14-ago): un tubo cruza el
        # encuadre justo sobre la linea. La pieza se detecta bajando, el tubo
        # la tapa MAS de las 20 ausencias toleradas, y reaparece debajo de la
        # linea: la pista habia muerto, nacia una nueva abajo y el cruce
        # jamas se registraba.
        for y in range(150, 251, 10):        # baja hasta 10 px sobre la linea
            self.c.actualizar([(90, y, 100, 25)])
        for _ in range(35):                  # el tubo la tapa 35 cuadros
            self.c.actualizar([])
        self.c.actualizar([(90, 300, 100, 25)])   # reaparece del otro lado
        self.assertEqual(1, self.c.total,
                         "el cruce ocurrido durante la oclusion debe contarse")

    def test_un_salto_largo_por_oclusion_se_reasocia(self):
        # Version rapida de la banda: durante la oclusion la pieza siguio
        # avanzando, asi que reaparece LEJOS (mas alla de la distancia maxima
        # fija). El alcance debe crecer con la velocidad observada y el
        # tiempo ausente.
        for y in range(150, 251, 4):         # velocidad medida: 4 px/cuadro
            self.c.actualizar([(90, y, 100, 25)])
        for _ in range(30):
            self.c.actualizar([])
        self.c.actualizar([(90, 368, 100, 25)])   # 120 px mas abajo
        self.assertEqual(1, self.c.total,
                         "la reasociacion debe alcanzar a la pieza ocluida")

    def test_el_contador_reporta_los_ids_contados(self):
        # La interfaz muestra el id en ULTIMOS CONTEOS: el contador debe
        # decir QUE pista conto en cada actualizacion (el worker los junta
        # despues de cada llamada, igual que aqui).
        ids = []
        for y in range(150, 300, 20):
            self.c.actualizar([(90, y, 100, 25)])
            ids.extend(self.c.contadas_recientes)
        self.assertEqual(1, self.c.total)
        self.assertEqual(1, len(ids), "debe reportar el id de la pieza contada")

    def test_una_pieza_detectada_tarde_si_cuenta(self):
        # Lo contrario del candado anterior, medido en el video de dia: hay
        # piezas que la deteccion encuentra por primera vez junto a la linea
        # (visto=3 en los registros) SIN conteo reciente. Esas son reales.
        for y in range(240, 320, 10):        # nace a 30 px de la linea
            self.c.actualizar([(90, y, 100, 25)])
        self.assertEqual(1, self.c.total,
                         "una pieza detectada tarde sigue siendo una pieza")

    def test_una_pieza_partida_en_dos_cajas_no_cuenta_doble(self):
        # El otro doble conteo: el modelo (o la mascara) parte una pieza en
        # dos cajas que viajan pegadas y cruzan la linea casi al mismo tiempo.
        # Dos piezas reales jamas cruzan asi: la cadencia minima medida en las
        # muestras de 60 s es de 64 cuadros entre piezas.
        for y in range(150, 400, 20):
            self.c.actualizar([(60, y, 48, 25), (115, y + 4, 48, 22)])
        self.assertEqual(1, self.c.total,
                         "una pieza partida en dos cajas es UNA pieza")

    def test_la_linea_recomendada_esta_calibrada(self):
        # Barrida contra el conteo manual: y=180 dio 8 piezas y y=270 dio 15.
        self.assertEqual(270, ContadorDeObjetos.LINEA_RECOMENDADA)


class IntegracionModuloTest(unittest.TestCase):
    """El modulo debe estar declarado y ser alcanzable desde el dashboard."""

    @classmethod
    def setUpClass(cls):
        cls.centro = (RAIZ / "centro_control.py").read_text(encoding="utf-8")
        cls.html = (RAIZ / "web" / "index.html").read_text(encoding="utf-8")
        cls.modulo = (RAIZ / "detector_objetos.py").read_text(encoding="utf-8")

    def test_el_modulo_esta_registrado_y_disponible(self):
        self.assertIn('"objetos": {', self.centro)
        self.assertIn("Detección de Objetos", self.centro)
        self.assertIn("detector_objetos.py", self.centro)

    def test_ya_no_existe_el_modulo_facial(self):
        self.assertNotIn('"facial": {', self.centro)
        self.assertNotIn('data-module="facial"', self.html)

    def test_la_tarjeta_del_dashboard_abre_el_detector(self):
        self.assertIn('data-module="objetos"', self.html)
        tarjeta = self.html[self.html.index('data-module="objetos"'):]
        tarjeta = tarjeta[: tarjeta.index("</article>")]
        self.assertIn("Abrir detector", tarjeta)
        self.assertNotIn("disabled", tarjeta)

    def test_el_script_del_modulo_existe(self):
        self.assertTrue((RAIZ / "detector_objetos.py").is_file())

    def test_el_modulo_late_con_el_nombre_recibido(self):
        # Mismo contrato que el detector de personas: si no coincide con el
        # identificador del lanzador, el supervisor lo reinicia en bucle.
        self.assertIn('os.environ.get("ARZYZ_MODULE_ID"', self.modulo)
        self.assertIn("HeartbeatWriter(MODULO)", self.modulo)

    def test_el_modulo_no_duplica_la_deteccion(self):
        # Una sola implementacion: si se duplicara, el dataset dejaria de
        # corresponder con lo que ve produccion.
        self.assertIn("from core.pipeline.classic import", self.modulo)
        capturador = (RAIZ / "tools" / "capturador.py").read_text(encoding="utf-8")
        self.assertIn("from core.pipeline.classic import", capturador)

    def test_las_versiones_del_frontend_subieron(self):
        for recurso in ("app.js", "styles.css"):
            v = re.search(rf"{re.escape(recurso)}\?v=(\d+)", self.html)
            self.assertIsNotNone(v)
            self.assertGreaterEqual(int(v.group(1)), 26)


if __name__ == "__main__":
    unittest.main()


class ContratoDeInterfazTest(unittest.TestCase):
    """Verifica las llamadas a widgets sin necesidad de abrir una ventana.

    El fallo que impidio abrir el modulo fue una llamada a MetricCard con tres
    argumentos cuando exige cuatro. Ninguna prueba lo detecto porque todas
    ejercitaban la logica, nunca la construccion de la interfaz. Esta prueba
    compara cada llamada contra la firma real del widget.
    """

    def test_metriccard_se_llama_con_los_argumentos_correctos(self):
        import ast
        import inspect

        from ui.widgets import MetricCard

        firma = inspect.signature(MetricCard.__init__)
        obligatorios = [
            n for n, p in firma.parameters.items()
            if n != "self" and p.default is inspect.Parameter.empty
        ]
        fuente = (RAIZ / "detector_objetos.py").read_text(encoding="utf-8")
        arbol = ast.parse(fuente)
        llamadas = [
            n for n in ast.walk(arbol)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "MetricCard"
        ]
        self.assertTrue(llamadas, "el modulo debe usar MetricCard")
        for llamada in llamadas:
            self.assertEqual(
                len(obligatorios), len(llamada.args) + len(llamada.keywords),
                f"MetricCard exige {obligatorios}; la llamada de la linea "
                f"{llamada.lineno} pasa {len(llamada.args)} argumentos",
            )

    def test_el_video_usa_ctkimage(self):
        # Con ImageTk la vista sale borrosa en pantallas con escalado.
        fuente = (RAIZ / "detector_objetos.py").read_text(encoding="utf-8")
        self.assertIn("ctk.CTkImage(", fuente)
        self.assertNotIn("ImageTk.PhotoImage", fuente)

    def test_no_quedan_llamadas_a_atributos_internos_del_widget(self):
        # Tocar value_label desde fuera acopla el modulo al interior del widget.
        fuente = (RAIZ / "detector_objetos.py").read_text(encoding="utf-8")
        self.assertNotIn(".value_label.configure(", fuente,
                         "usa MetricCard.set() en vez del interior del widget")


class ContratoDeTarjetaTest(unittest.TestCase):
    """Toda tarjeta disponible debe comportarse como la que ya funciona.

    El modulo no abria porque su boton no tenia `data-start`: el JS enlaza el
    clic con `$$("[data-start]")`, asi que el boton no hacia absolutamente nada.
    Tampoco tenia la clase `available`, de la que depende el efecto al pasar el
    puntero, ni un color de acento propio. Ninguna prueba lo detecto porque
    verificaban el texto de la tarjeta, no su contrato con el JS.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (RAIZ / "web" / "index.html").read_text(encoding="utf-8")
        cls.css = (RAIZ / "web" / "styles.css").read_text(encoding="utf-8")
        cls.js = (RAIZ / "web" / "app.js").read_text(encoding="utf-8")
        cls.centro = (RAIZ / "centro_control.py").read_text(encoding="utf-8")

    def _tarjeta(self, modulo: str) -> str:
        marca = f'data-module="{modulo}"'
        ini = self.html.rindex("<article", 0, self.html.index(marca))
        return self.html[ini: self.html.index("</article>", ini)]

    def _modulos_disponibles(self) -> list[str]:
        bloque = self.centro[self.centro.index("MODULES = {"):]
        bloque = bloque[: bloque.index("\n}\n")]
        encontrados = []
        for trozo in re.split(r'\n    "', bloque)[1:]:
            nombre = trozo.split('"')[0]
            if '"available": True' in trozo:
                encontrados.append(nombre)
        return encontrados

    def test_el_js_enlaza_el_clic_por_data_start(self):
        # Si esto cambia, el resto de las comprobaciones dejan de tener sentido.
        self.assertIn('$$("[data-start]")', self.js)

    def test_toda_tarjeta_disponible_lanza_su_modulo(self):
        disponibles = self._modulos_disponibles()
        self.assertIn("objetos", disponibles)
        self.assertIn("personas", disponibles)
        for modulo in disponibles:
            tarjeta = self._tarjeta(modulo)
            self.assertIn(f'data-start="{modulo}"', tarjeta,
                          f"el boton de {modulo} no dispara nada al pulsarlo")
            self.assertNotIn("disabled", tarjeta,
                             f"el boton de {modulo} esta deshabilitado")

    def test_toda_tarjeta_disponible_reacciona_al_puntero(self):
        # El efecto al pasar el puntero depende de .module-card.available:hover
        self.assertIn(".module-card.available:hover", self.css)
        for modulo in self._modulos_disponibles():
            encabezado = self._tarjeta(modulo).split(">")[0]
            self.assertIn("available", encabezado,
                          f"la tarjeta de {modulo} no reacciona al puntero")

    def test_cada_tarjeta_tiene_color_de_acento(self):
        for modulo in ("objetos", "personas"):
            clases = self._tarjeta(modulo).split(">")[0]
            tipo = [c for c in re.findall(r'class="([^"]+)"', clases)[0].split()
                    if c not in ("module-card", "available")]
            self.assertTrue(tipo, f"la tarjeta de {modulo} no tiene clase de tipo")
            self.assertIn(f".module-card.{tipo[0]} {{ --accent:", self.css,
                          f"falta el color de acento de .{tipo[0]}")

    def test_no_quedan_estilos_del_modulo_retirado(self):
        self.assertNotIn(".module-card.facial", self.css)


class PanelDeFuenteTest(unittest.TestCase):
    """El panel de fuente debe ser identico al del detector de personas.

    Vive en `ui/source.py` y lo usa el modulo de objetos. Si cada detector
    armara sus campos por su cuenta, con el tiempo dejarian de comportarse
    igual y una camara configurada en uno no serviria en el otro.
    """

    @classmethod
    def setUpClass(cls):
        cls.fuente = (RAIZ / "ui" / "source.py").read_text(encoding="utf-8")
        cls.layout = (RAIZ / "ui" / "layout.py").read_text(encoding="utf-8")
        cls.modulo = (RAIZ / "detector_objetos.py").read_text(encoding="utf-8")
        cls.detector = (RAIZ / "detector_empresarial.py").read_text(encoding="utf-8")

    def test_tiene_los_mismos_campos_que_el_detector_de_personas(self):
        for campo in ("camera_index_entry", "brand_combo", "ip_entry",
                      "port_entry", "user_entry", "password_entry",
                      "route_entry", "file_entry", "browse_video_button"):
            self.assertIn(campo, self.fuente, f"falta {campo}")
            self.assertIn(campo, self.layout, f"{campo} ya no existe en personas")

    def test_ofrece_los_mismos_tipos_de_fuente(self):
        for tipo in ("Cámara local", "Cámara IP / RTSP", "Archivo de video"):
            self.assertIn(tipo, self.fuente)
            self.assertIn(tipo, self.layout)

    def test_muestra_los_mismos_campos_en_modo_rtsp(self):
        # En personas: marca, ip, usuario y contrasena. El puerto es fijo.
        bloque = self.fuente[self.fuente.index("def mostrar_campos"):]
        bloque = bloque[: bloque.index("def _aplicar_marca")]
        for widget in ("brand_combo", "ip_entry", "user_entry", "password_entry"):
            self.assertIn(widget, bloque)

    def test_la_contrasena_nunca_se_guarda(self):
        guardado = self.fuente[self.fuente.index("def guardar_en"):]
        self.assertNotIn("password_entry", guardado,
                         "la contrasena no debe escribirse en config.json")
        self.assertIn("no se guarda", self.fuente)

    def test_la_url_rtsp_codifica_las_credenciales(self):
        # Sin codificar, una contrasena con / o espacio rompe la URL.
        self.assertIn("quote(", self.fuente)
        self.assertIn("credenciales", self.fuente)

    def test_el_modulo_usa_el_panel_compartido(self):
        self.assertIn("from ui.source import PanelDeFuente", self.modulo)
        # El panel vive dentro del submenu "FUENTE DE VIDEO", pero sigue
        # siendo el componente compartido con el detector de personas.
        self.assertIn("PanelDeFuente(sec_video.contenido, self.config_data)",
                      self.modulo)

    def test_los_botones_miden_igual_que_en_personas(self):
        import re

        alturas_personas = set(re.findall(
            r'text="[▶■▣][^"]*",\s*height=(\d+)', self.layout))
        alturas_objetos = set(re.findall(
            r'text="[▶■][^"]*", height=(\d+)', self.modulo))
        self.assertTrue(alturas_personas and alturas_objetos)
        self.assertEqual(alturas_personas, alturas_objetos,
                         "los botones de accion deben medir lo mismo")

    def test_usa_los_mismos_colores_de_accion(self):
        for color in ("#178c56", "#a6333b"):
            self.assertIn(color, self.layout)
            self.assertIn(color, self.modulo)


class FuenteRTSPTest(unittest.TestCase):
    """El fallo reportado: OpenCV recibia una lista de rutas en vez de una.

    Algunas marcas publican el flujo en perfiles distintos (Provision usa
    profile1, profile2 y profile3), asi que la fuente puede ser una lista.
    Pasarla tal cual a VideoCapture produce
    "Expected 'filename' to be a str or path-like object".
    """

    @classmethod
    def setUpClass(cls):
        cls.modulo = (RAIZ / "detector_objetos.py").read_text(encoding="utf-8")

    def test_prueba_cada_ruta_candidata(self):
        self.assertIn("def _abrir", self.modulo)
        bloque = self.modulo[self.modulo.index("def _abrir"):]
        bloque = bloque[: bloque.index("def _ciclo")]
        self.assertIn("isinstance(self.fuente, list)", bloque,
                      "debe recorrer las rutas candidatas una por una")
        self.assertIn("captura.release()", bloque,
                      "cada intento fallido debe liberarse")

    def test_usa_tiempo_de_espera_en_rtsp(self):
        # Sin limite, una IP equivocada deja la ventana colgada.
        self.assertIn("CAP_PROP_OPEN_TIMEOUT_MSEC", self.modulo)

    def test_el_error_no_revela_la_contrasena(self):
        bloque = self.modulo[self.modulo.index("def _abrir"):]
        bloque = bloque[: bloque.index("def _ciclo")]
        self.assertIn('"rtsp://***@"', bloque,
                      "la URL del mensaje debe ocultar las credenciales")


class BotonesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modulo = (RAIZ / "detector_objetos.py").read_text(encoding="utf-8")

    def test_hay_tres_botones_de_accion(self):
        for texto in ("INICIAR CONTEO", "DETENER", "PROBAR CÁMARA"):
            self.assertIn(texto, self.modulo)

    def test_los_botones_van_dentro_del_panel_de_video(self):
        # A lo ancho de la ventana quedaban desproporcionados.
        self.assertIn('acciones = ctk.CTkFrame(derecha', self.modulo)
        self.assertNotIn('acciones = ctk.CTkFrame(self,', self.modulo)

    def test_probar_camara_no_cuenta_ni_guarda(self):
        self.assertIn("def probar", self.modulo)
        self.assertIn("solo_vista=True", self.modulo)
        bloque = self.modulo[self.modulo.index("if fondo_modelo.listo"):]
        self.assertIn("if not self.solo_vista", bloque[:400],
                      "en modo vista no debe contarse")

    def test_el_modo_vista_no_guarda_evidencias(self):
        self.assertIn("bool(self.evidencia.get()) and not solo_vista", self.modulo)

    def test_el_cierre_espera_al_hilo(self):
        # Sin esperar, el hilo escribe en una cola ya destruida.
        self.assertIn("self.worker.join(timeout=3)", self.modulo)
