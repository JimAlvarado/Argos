"""Pruebas del motor de estados con duracion (fase 2).

Las mediciones que aparecen aqui salen del video real del mantenedor
(19-ago-2026, PTZ Mantenedor Sur): puerta cerrada R-G = -3.77, abierta
R-G = +20.74, apertura de t=72.5s a t=578.6s. Los valores no son inventados
para que la prueba pase: son los que el sistema va a ver en planta.
"""
import unittest

from core.pipeline.estados import Histeresis, MaquinaDeEstado
from core.utils import formato_duracion

# Medidos en el vano del mantenedor el 19-ago-2026.
CERRADA = -3.77
ABIERTA = 20.74


def maquina(activo_inicial=False, permanencia=3.0, momento_inicial=0.0):
    return MaquinaDeEstado(
        Histeresis.desde_estados_medidos(CERRADA, ABIERTA),
        permanencia=permanencia,
        nombre_activo="abierto",
        nombre_inactivo="cerrado",
        activo_inicial=activo_inicial,
        momento_inicial=momento_inicial,
    )


class HisteresisTest(unittest.TestCase):
    def test_exige_dos_umbrales_distintos(self):
        # Con entra == sale no hay antiparpadeo: una senal apoyada justo ahi
        # transicionaria en cada muestra.
        with self.assertRaises(ValueError):
            Histeresis(entra=10.0, sale=10.0)
        with self.assertRaises(ValueError):
            Histeresis(entra=5.0, sale=10.0)

    def test_los_umbrales_caen_entre_los_dos_estados_medidos(self):
        h = Histeresis.desde_estados_medidos(CERRADA, ABIERTA)
        # Los dos estados reales deben quedar HOLGADAMENTE fuera de la banda,
        # o el detector dudaria justo en las condiciones normales de operacion.
        self.assertLess(CERRADA, h.sale)
        self.assertGreater(ABIERTA, h.entra)
        self.assertGreater(h.entra, h.sale)

    def test_derivar_del_rango_observado_habria_sido_sesgado(self):
        # Regresion del error metodologico del primer analisis: los umbrales
        # salian de percentiles del rango observado, y como la puerta estuvo
        # abierta el 84% del video, el umbral de apertura caia DENTRO de la
        # distribucion de "abierta". El punto medio no tiene ese sesgo.
        h = Histeresis.desde_estados_medidos(CERRADA, ABIERTA)
        medio = (CERRADA + ABIERTA) / 2
        self.assertLess(abs((h.entra + h.sale) / 2 - medio), 1e-9)

    def test_acepta_senales_que_bajan_al_activarse(self):
        # No toda senal sube al activarse; el motor no debe presuponerlo.
        h = Histeresis.desde_estados_medidos(inactivo=100.0, activo=20.0)
        self.assertGreater(h.entra, h.sale)
        self.assertLess(h.entra, 100.0)
        self.assertGreater(h.sale, 20.0)

    def test_rechaza_estados_iguales_y_separacion_invalida(self):
        with self.assertRaises(ValueError):
            Histeresis.desde_estados_medidos(5.0, 5.0)
        for separacion in (0.0, 1.0, -0.5, 2.0):
            with self.assertRaises(ValueError):
                Histeresis.desde_estados_medidos(0.0, 10.0, separacion)


class AntiparpadeoTest(unittest.TestCase):
    def test_un_valor_sostenido_dentro_de_la_banda_no_transiciona(self):
        """La prueba decisiva de la histeresis, separada de la permanencia.

        El valor se SOSTIENE mucho mas que la permanencia, asi que si la
        permanencia fuera lo unico que protege, aqui transicionaria. Solo la
        histeresis explica que no lo haga: estando inactivo hace falta superar
        `entra`, y estando activo hace falta caer por debajo de `sale`.

        Una version anterior de esta prueba oscilaba entre varios valores de la
        banda y pasaba incluso con la histeresis eliminada, porque ningun valor
        se mantenia los 3 s de permanencia. Se detecto reintroduciendo el bug.
        """
        h = Histeresis.desde_estados_medidos(CERRADA, ABIERTA)
        casos = (
            # (estado inicial, valor sostenido dentro de la banda)
            (False, h.entra - 0.01),   # casi entra, pero no entra
            (False, h.sale + 0.01),
            (True, h.sale + 0.01),     # casi sale, pero no sale
            (True, h.entra - 0.01),
        )
        for inicial, valor in casos:
            with self.subTest(inicial=inicial, valor=round(valor, 3)):
                m = maquina(activo_inicial=inicial, permanencia=3.0)
                eventos = [m.actualizar(valor, float(t)) for t in range(60)]
                self.assertEqual(
                    [], [e for e in eventos if e is not None],
                    "un valor dentro de la banda no puede transicionar",
                )
                self.assertEqual(inicial, m.activo)

    def test_una_senal_que_oscila_rapido_tampoco_transiciona(self):
        # Complementa a la anterior: aqui la que protege es la permanencia.
        h = Histeresis.desde_estados_medidos(CERRADA, ABIERTA)
        alternos = [CERRADA, ABIERTA]
        for inicial in (False, True):
            with self.subTest(inicial=inicial):
                m = maquina(activo_inicial=inicial, permanencia=3.0)
                eventos = [
                    m.actualizar(alternos[i % 2], float(i)) for i in range(60)
                ]
                self.assertEqual([], [e for e in eventos if e is not None])
                self.assertEqual(inicial, m.activo)

    def test_un_destello_mas_corto_que_la_permanencia_se_ignora(self):
        # El caso real: alguien cruza frente al vano. No es que la puerta
        # cambiara de estado.
        m = maquina(permanencia=3.0)
        eventos = []
        for t in range(0, 10):
            eventos.append(m.actualizar(CERRADA, float(t)))
        # Dos muestras (menos de 3 s) con la senal de abierta.
        eventos.append(m.actualizar(ABIERTA, 10.0))
        eventos.append(m.actualizar(ABIERTA, 11.0))
        # Y vuelve a cerrada.
        for t in range(12, 20):
            eventos.append(m.actualizar(CERRADA, float(t)))
        self.assertTrue(all(e is None for e in eventos))
        self.assertFalse(m.activo)

    def test_un_cambio_sostenido_produce_exactamente_un_evento(self):
        m = maquina(permanencia=3.0)
        for t in range(0, 10):
            m.actualizar(CERRADA, float(t))
        eventos = [m.actualizar(ABIERTA, float(t)) for t in range(10, 30)]
        reales = [e for e in eventos if e is not None]
        self.assertEqual(1, len(reales))
        self.assertTrue(m.activo)
        self.assertEqual("abierto", m.estado)


class DuracionTest(unittest.TestCase):
    def test_el_intervalo_empieza_en_el_cruce_no_en_la_confirmacion(self):
        """La duracion no debe perder los segundos de la permanencia.

        Si se anotara la hora de confirmacion, toda duracion saldria corta
        exactamente por la permanencia: un error sistematico, no aleatorio.
        """
        m = maquina(permanencia=3.0)
        for t in range(0, 10):
            m.actualizar(CERRADA, float(t))
        # Cruza a abierta en t=10 y se confirma en t=13.
        cierre = None
        for t in range(10, 14):
            cierre = m.actualizar(ABIERTA, float(t)) or cierre
        self.assertIsNotNone(cierre)
        self.assertEqual("cerrado", cierre.estado)
        self.assertEqual(10.0, cierre.fin, "el fin es el cruce, no la confirmacion")
        self.assertEqual(10.0, m.desde, "el estado nuevo empieza en el cruce")

        # Cruza a cerrada en t=100 y se confirma en t=103.
        apertura = None
        for t in range(100, 104):
            apertura = m.actualizar(CERRADA, float(t)) or apertura
        self.assertIsNotNone(apertura)
        self.assertEqual("abierto", apertura.estado)
        self.assertEqual(10.0, apertura.inicio)
        self.assertEqual(100.0, apertura.fin)
        self.assertEqual(90.0, apertura.duracion,
                         "90 s reales, no 87 ni 93")

    def test_la_linea_de_tiempo_queda_continua(self):
        # El fin de un intervalo debe ser el inicio del siguiente: sin huecos
        # ni solapes, o la suma de duraciones no cuadraria con el turno.
        m = maquina(permanencia=2.0)
        intervalos = []
        senal = ([CERRADA] * 10 + [ABIERTA] * 10) * 3
        for t, valor in enumerate(senal):
            evento = m.actualizar(valor, float(t))
            if evento:
                intervalos.append(evento)
        self.assertGreaterEqual(len(intervalos), 3)
        for previo, siguiente in zip(intervalos, intervalos[1:]):
            self.assertEqual(previo.fin, siguiente.inicio)

    def test_duracion_actual_sirve_de_cronometro(self):
        m = maquina(activo_inicial=True, momento_inicial=100.0)
        self.assertEqual(0.0, m.duracion_actual(100.0))
        self.assertEqual(50.0, m.duracion_actual(150.0))
        # Nunca negativa, aunque llegue un momento anterior por desorden.
        self.assertEqual(0.0, m.duracion_actual(90.0))


class IntervaloParcialTest(unittest.TestCase):
    def test_el_primer_intervalo_es_parcial_y_el_segundo_no(self):
        # La maquina no vio empezar el primer estado, asi que su duracion es
        # una cota inferior. Marcarlo evita contaminar promedios.
        m = maquina(permanencia=2.0)
        primero = None
        for t in range(0, 5):
            m.actualizar(CERRADA, float(t))
        for t in range(5, 9):
            primero = m.actualizar(ABIERTA, float(t)) or primero
        self.assertIsNotNone(primero)
        self.assertTrue(primero.parcial)

        segundo = None
        for t in range(20, 25):
            segundo = m.actualizar(CERRADA, float(t)) or segundo
        self.assertIsNotNone(segundo)
        self.assertFalse(segundo.parcial, "su inicio y su fin si se observaron")

    def test_cerrar_entrega_el_intervalo_en_curso_marcado_parcial(self):
        # Sin esto, la ultima apertura del turno no se registraria nunca.
        m = maquina(permanencia=2.0)
        for t in range(0, 5):
            m.actualizar(CERRADA, float(t))
        for t in range(5, 9):
            m.actualizar(ABIERTA, float(t))
        pendiente = m.cerrar(200.0)
        self.assertIsNotNone(pendiente)
        self.assertEqual("abierto", pendiente.estado)
        self.assertEqual(5.0, pendiente.inicio)
        self.assertEqual(195.0, pendiente.duracion)
        self.assertTrue(pendiente.parcial,
                        "el fin lo impuso el paro del modulo, no el proceso")

    def test_cerrar_sin_haber_observado_nada_no_inventa_un_intervalo(self):
        m = maquina()
        self.assertIsNone(m.cerrar(0.0))


class SinDatoConfiableTest(unittest.TestCase):
    """El caso de la camara PTZ que se reposiciona."""

    def test_sin_dato_no_transiciona_y_marca_hueco(self):
        m = maquina(permanencia=2.0)
        for t in range(0, 5):
            m.actualizar(CERRADA, float(t))
        # La camara se movio: no hay lectura valida del vano.
        for t in range(5, 30):
            self.assertIsNone(m.actualizar(None, float(t)))
        self.assertFalse(m.activo, "sin datos no se puede afirmar un cambio")

        cerrado = None
        for t in range(30, 35):
            cerrado = m.actualizar(ABIERTA, float(t)) or cerrado
        self.assertIsNotNone(cerrado)
        self.assertTrue(cerrado.con_hueco,
                        "el intervalo debe declarar que hubo ciego")

    def test_la_falta_de_datos_descarta_un_cambio_a_medio_confirmar(self):
        # No se puede afirmar que el estado se sostuvo si no hubo con que verlo.
        m = maquina(permanencia=5.0)
        for t in range(0, 5):
            m.actualizar(CERRADA, float(t))
        m.actualizar(ABIERTA, 5.0)          # empieza a cruzar
        m.actualizar(None, 6.0)             # se pierde la vista
        m.actualizar(ABIERTA, 7.0)          # vuelve la vista
        # Si el candidato no se hubiera descartado, en t=10 ya se cumplirian
        # los 5 s desde t=5 y confirmaria de mas.
        self.assertIsNone(m.actualizar(ABIERTA, 10.0))
        self.assertFalse(m.activo)

    def test_el_hueco_no_se_arrastra_al_intervalo_siguiente(self):
        m = maquina(permanencia=2.0)
        m.actualizar(None, 0.0)
        for t in range(1, 6):
            m.actualizar(CERRADA, float(t))
        con_hueco = None
        for t in range(6, 10):
            con_hueco = m.actualizar(ABIERTA, float(t)) or con_hueco
        self.assertTrue(con_hueco.con_hueco)
        limpio = None
        for t in range(20, 25):
            limpio = m.actualizar(CERRADA, float(t)) or limpio
        self.assertFalse(limpio.con_hueco, "este intervalo se vio completo")


class ValorMedioTest(unittest.TestCase):
    def test_el_promedio_pertenece_a_su_propio_intervalo(self):
        # Sirve para auditar por que el motor decidio lo que decidio, igual que
        # cada conteo de lingotes guarda con que modelo se conto.
        m = maquina(permanencia=2.0)
        for t in range(0, 10):
            m.actualizar(CERRADA, float(t))
        cerrado = None
        for t in range(10, 14):
            cerrado = m.actualizar(ABIERTA, float(t)) or cerrado
        self.assertAlmostEqual(CERRADA, cerrado.valor_medio, places=6,
                               msg="no debe mezclar muestras del estado nuevo")

        abierto = None
        for t in range(30, 35):
            abierto = m.actualizar(CERRADA, float(t)) or abierto
        self.assertAlmostEqual(ABIERTA, abierto.valor_medio, places=6)

    def test_un_destello_descartado_no_ensucia_el_promedio(self):
        m = maquina(permanencia=5.0)
        for t in range(0, 10):
            m.actualizar(CERRADA, float(t))
        m.actualizar(ABIERTA, 10.0)      # destello que no se confirma
        for t in range(11, 40):
            m.actualizar(CERRADA, float(t))
        pendiente = m.cerrar(40.0)
        # El destello cuenta como muestra del intervalo cerrado (ocurrio dentro
        # de el), pero no debe haberlo desplazado hacia el valor de abierta.
        self.assertLess(pendiente.valor_medio, 0.0)


class SenalRealDelMantenedorTest(unittest.TestCase):
    """Reproduce la medicion del 19-ago y comprueba que se recupera igual."""

    def test_recupera_la_apertura_medida_en_el_video(self):
        # Video real: 10 min a 2 Hz, cerrada hasta t=72.5, abierta hasta
        # t=578.6, cerrada hasta el final. El detector encontro 1 apertura de
        # 506.1 s y se verifico contra la imagen en cuatro instantes.
        m = maquina(permanencia=3.0)
        intervalos = []
        paso = 0.5
        t = 0.0
        while t < 600.0:
            abierta = 72.5 <= t < 578.6
            evento = m.actualizar(ABIERTA if abierta else CERRADA, t)
            if evento:
                intervalos.append(evento)
            t += paso
        pendiente = m.cerrar(600.0)
        if pendiente:
            intervalos.append(pendiente)

        aperturas = [i for i in intervalos if i.estado == "abierto"]
        self.assertEqual(1, len(aperturas), "una sola apertura, como en el video")
        apertura = aperturas[0]
        self.assertAlmostEqual(72.5, apertura.inicio, delta=paso)
        self.assertAlmostEqual(506.1, apertura.duracion, delta=1.0)
        self.assertFalse(apertura.parcial)
        self.assertFalse(apertura.con_hueco)

    def test_el_tiempo_abierto_coincide_con_el_84_por_ciento_medido(self):
        m = maquina(permanencia=3.0)
        abierto_total = 0.0
        t = 0.0
        while t < 600.0:
            abierta = 72.5 <= t < 578.6
            evento = m.actualizar(ABIERTA if abierta else CERRADA, t)
            if evento and evento.estado == "abierto":
                abierto_total += evento.duracion
            t += 0.5
        pendiente = m.cerrar(600.0)
        if pendiente and pendiente.estado == "abierto":
            abierto_total += pendiente.duracion
        self.assertAlmostEqual(84.4, abierto_total / 600.0 * 100, delta=0.5)


class FormatoDuracionTest(unittest.TestCase):
    def test_lo_que_lee_el_operador(self):
        self.assertEqual("45s", formato_duracion(45))
        self.assertEqual("8m 26s", formato_duracion(506.1))
        self.assertEqual("1h 04m", formato_duracion(3840))
        self.assertEqual("0s", formato_duracion(0))
        self.assertEqual("0s", formato_duracion(-5), "nunca duraciones negativas")


if __name__ == "__main__":
    unittest.main()
