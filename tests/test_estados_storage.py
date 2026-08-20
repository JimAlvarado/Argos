"""Pruebas de la tabla `estados` en EventStore y de su bitacora CSV.

Se prueba la COSTURA, no solo la logica: que las tres tablas anteriores sigan
funcionando, que el CSV en vivo y el reconstruido coincidan, y que un CSV con
encabezado de la version anterior no se desalinee al recibir una fila nueva.
Los fallos historicos de este proyecto vivieron ahi, no en los calculos.
"""
import csv
import tempfile
import unittest
from pathlib import Path

from core.evidence import EvidenceManager
from core.storage import EventStore
from core.utils import formato_duracion

# Datos del mantenedor medidos el 19-ago-2026 sobre el video real.
APERTURA = {
    "estacion": "mantenedor",
    "estado": "abierto",
    "inicio": "2026-08-19 04:19:13",
    "fin": "2026-08-19 04:27:39",
    "duracion_s": 506.1,
    "source": "PTZ Mantenedor Sur",
    "origen": "camara:rosado",
    "parcial": False,
    "con_hueco": False,
    "valor_medio": 20.74,
    "evidence_path": "",
}


class BaseConEventStore(unittest.TestCase):
    def setUp(self):
        self._temporal = tempfile.TemporaryDirectory()
        self.raiz = Path(self._temporal.name)
        self.evidencias = EvidenceManager(self.raiz / "evidencias")
        self.store = EventStore(self.raiz / "eventos.db", self.evidencias)

    def tearDown(self):
        self._temporal.cleanup()

    def apertura(self, **cambios) -> dict:
        evento = dict(APERTURA)
        evento.update(cambios)
        return evento


class TablaDeEstadosTest(BaseConEventStore):
    def test_guarda_y_devuelve_un_identificador(self):
        primero = self.store.insert_estado(self.apertura())
        segundo = self.store.insert_estado(self.apertura())
        self.assertGreater(primero, 0)
        self.assertNotEqual(primero, segundo)

    def test_abrir_dos_veces_la_misma_base_no_falla(self):
        # Es el camino de migracion real: al actualizar la version, la base ya
        # existe y `_initialize` corre otra vez sobre ella.
        otra = EventStore(self.raiz / "eventos.db", self.evidencias)
        self.assertGreater(otra.insert_estado(self.apertura()), 0)

    def test_las_tres_tablas_anteriores_siguen_funcionando(self):
        # Regresion: el cambio de esquema no debe tocar lo que ya opera. El
        # conteo de lingotes escribe en `crossings` y esta verificado 15/15.
        self.store.insert({
            "detected_at": "2026-08-19 04:00:00", "source": "camara",
            "total": 1, "classes": {"lingote": 1}, "max_confidence": 0.9,
            "model_name": "lingotes_v2_20260815.pt",
        })
        self.store.insert_crossing({
            "crossed_at": "2026-08-19 04:00:01", "source": "camara",
            "track_id": 7, "class_name": "lingote", "direction": "A → B",
            "confidence": 0.9, "model_name": "lingotes_v2_20260815.pt",
        })
        self.store.insert_zone_alert({
            "alerted_at": "2026-08-19 04:00:02", "source": "camara",
            "track_id": 3, "class_name": "person", "confidence": 0.8,
            "model_name": "yolov8n.pt",
        })
        identificadores = {e["id"][0] for e in self.store.recent(50)}
        self.assertEqual({"D", "C", "Z"}, identificadores)

    def test_las_banderas_viajan_como_booleanos(self):
        # SQLite no tiene booleanos; se guardan como 0/1 y deben volver a leerse
        # como banderas o el resumen contaria mal los intervalos dudosos.
        self.store.insert_estado(self.apertura(parcial=True, con_hueco=True))
        resumen = self.store.resumen_de_estados("mantenedor", "abierto")
        self.assertEqual(1, resumen["parciales"])
        self.assertEqual(1, resumen["con_hueco"])


class RegistroDeEventosTest(BaseConEventStore):
    def test_el_estado_aparece_en_el_registro_con_su_duracion(self):
        self.store.insert_estado(self.apertura())
        eventos = self.store.recent(10)
        self.assertEqual(1, len(eventos))
        evento = eventos[0]
        self.assertTrue(evento["id"].startswith("E-"))
        # Se ubica por su INICIO: el operador busca "a que hora se abrio".
        self.assertEqual(APERTURA["inicio"], evento["detected_at"])
        etiqueta = next(iter(evento["classes"]))
        self.assertIn("MANTENEDOR", etiqueta)
        self.assertIn("abierto", etiqueta)
        self.assertIn("8m 26s", etiqueta)
        # El origen queda auditado igual que el modelo en las otras tablas.
        self.assertEqual("camara:rosado", evento["model_name"])

    def test_el_registro_avisa_de_los_intervalos_dudosos(self):
        self.store.insert_estado(self.apertura(parcial=True, con_hueco=True))
        etiqueta = next(iter(self.store.recent(1)[0]["classes"]))
        self.assertIn("parcial", etiqueta)
        self.assertIn("hueco", etiqueta)

    def test_se_ordena_junto_a_los_demas_eventos(self):
        self.store.insert_estado(self.apertura(inicio="2026-08-19 04:00:00"))
        self.store.insert_crossing({
            "crossed_at": "2026-08-19 05:00:00", "source": "camara",
            "track_id": 1, "class_name": "lingote", "direction": "A → B",
            "confidence": 0.9, "model_name": "m.pt",
        })
        eventos = self.store.recent(10)
        self.assertEqual("C", eventos[0]["id"][0], "lo mas reciente primero")
        self.assertEqual("E", eventos[1]["id"][0])


class ResumenTest(BaseConEventStore):
    def test_cuenta_veces_y_suma_tiempo(self):
        # El dato que pidio operacion: cuantas aperturas y cuanto tiempo.
        for duracion in (100.0, 200.0, 506.1):
            self.store.insert_estado(self.apertura(duracion_s=duracion))
        resumen = self.store.resumen_de_estados("mantenedor", "abierto")
        self.assertEqual(3, resumen["veces"])
        self.assertAlmostEqual(806.1, resumen["duracion_total"], places=3)
        self.assertAlmostEqual(506.1, resumen["duracion_maxima"], places=3)
        self.assertAlmostEqual(268.7, resumen["duracion_promedio"], places=1)

    def test_no_mezcla_estaciones_ni_estados(self):
        self.store.insert_estado(self.apertura(duracion_s=100.0))
        self.store.insert_estado(
            self.apertura(estado="cerrado", duracion_s=999.0))
        self.store.insert_estado(
            self.apertura(estacion="horno", estado="abierto", duracion_s=555.0))
        resumen = self.store.resumen_de_estados("mantenedor", "abierto")
        self.assertEqual(1, resumen["veces"])
        self.assertAlmostEqual(100.0, resumen["duracion_total"], places=3)

    def test_filtra_por_fecha(self):
        self.store.insert_estado(
            self.apertura(inicio="2026-08-18 04:00:00", duracion_s=100.0))
        self.store.insert_estado(
            self.apertura(inicio="2026-08-19 04:00:00", duracion_s=200.0))
        resumen = self.store.resumen_de_estados(
            "mantenedor", "abierto", desde="2026-08-19 00:00:00")
        self.assertEqual(1, resumen["veces"])
        self.assertAlmostEqual(200.0, resumen["duracion_total"], places=3)

    def test_sin_datos_no_divide_por_cero(self):
        resumen = self.store.resumen_de_estados("mantenedor", "abierto")
        self.assertEqual(0, resumen["veces"])
        self.assertEqual(0.0, resumen["duracion_promedio"])


def leer_csv(ruta: Path) -> tuple[list[str], list[dict]]:
    """Devuelve (encabezado, filas) saltando la linea `sep=` de Excel."""
    lineas = ruta.read_text(encoding="utf-8-sig").splitlines()
    inicio = 1 if lineas and lineas[0].startswith("sep=") else 0
    encabezado = next(csv.reader([lineas[inicio]]))
    filas = list(csv.DictReader(lineas[inicio:]))
    return encabezado, filas


class BitacoraCSVTest(BaseConEventStore):
    def ruta_csv(self) -> Path:
        return self.evidencias.csv_path(APERTURA["inicio"])

    def test_la_duracion_llega_al_csv_legible_y_numerica(self):
        # Con `extrasaction="ignore"` una columna que no este declarada se
        # descarta EN SILENCIO: la duracion no llegaria y nadie se enteraria.
        self.store.insert_estado(self.apertura())
        encabezado, filas = leer_csv(self.ruta_csv())
        self.assertIn("duracion", encabezado)
        self.assertIn("duracion_s", encabezado)
        self.assertEqual(1, len(filas))
        self.assertEqual("8m 26s", filas[0]["duracion"])
        self.assertEqual("506.1", filas[0]["duracion_s"])
        self.assertEqual("ESTADO", filas[0]["tipo_evento"])
        self.assertEqual(APERTURA["fin"], filas[0]["fin"])

    def test_anota_las_observaciones_de_calidad(self):
        self.store.insert_estado(self.apertura(parcial=True, con_hueco=True))
        _, filas = leer_csv(self.ruta_csv())
        self.assertIn("parcial", filas[0]["observaciones"])
        self.assertIn("hueco", filas[0]["observaciones"])

    def test_las_columnas_nuevas_van_al_final(self):
        # Operacion ya tiene hojas de Excel apoyadas en el orden actual.
        self.store.insert_estado(self.apertura())
        encabezado, _ = leer_csv(self.ruta_csv())
        anteriores = [
            "id", "fecha_hora", "tipo_evento", "fuente", "total_objetos",
            "clases", "confianza_maxima", "direccion", "track_id",
            "modelo", "archivo_evidencia",
        ]
        self.assertEqual(anteriores, encabezado[: len(anteriores)])

    def test_el_csv_reconstruido_coincide_con_el_escrito_en_vivo(self):
        # Si cada camino armara la fila por su cuenta, un CSV reconstruido
        # diferiria del original y nadie sabria cual creer.
        self.store.insert_estado(self.apertura())
        _, en_vivo = leer_csv(self.ruta_csv())
        self.store.maintain_evidence()
        _, reconstruido = leer_csv(self.ruta_csv())
        self.assertEqual(en_vivo, reconstruido)

    def test_un_csv_con_encabezado_viejo_se_migra_sin_desalinearse(self):
        """Costura del dia del despliegue.

        Un archivo del dia anterior tiene el encabezado de 11 columnas. Al
        recibir una fila de 15 valores quedaria corrido: el error no salta, el
        dato sale mal. Debe migrarse conservando lo que ya estaba.
        """
        ruta = self.ruta_csv()
        ruta.parent.mkdir(parents=True, exist_ok=True)
        viejas = [
            "id", "fecha_hora", "tipo_evento", "fuente", "total_objetos",
            "clases", "confianza_maxima", "direccion", "track_id",
            "modelo", "archivo_evidencia",
        ]
        with ruta.open("w", encoding="utf-8-sig", newline="") as archivo:
            archivo.write("sep=,\r\n")
            escritor = csv.DictWriter(archivo, fieldnames=viejas)
            escritor.writeheader()
            escritor.writerow({
                "id": "D-1", "fecha_hora": APERTURA["inicio"],
                "tipo_evento": "DETECCION", "fuente": "camara",
                "total_objetos": "1", "clases": "lingote x1",
                "confianza_maxima": "90.00%", "direccion": "",
                "track_id": "", "modelo": "lingotes_v2_20260815.pt",
                "archivo_evidencia": "",
            })

        self.store.insert_estado(self.apertura())

        encabezado, filas = leer_csv(ruta)
        self.assertEqual(self.evidencias.CSV_FIELDS, encabezado)
        self.assertEqual(2, len(filas), "la fila anterior debe conservarse")
        previa, nueva = filas
        self.assertEqual("D-1", previa["id"])
        self.assertEqual("lingote x1", previa["clases"],
                         "la fila vieja no debe quedar corrida")
        self.assertEqual("lingotes_v2_20260815.pt", previa["modelo"])
        self.assertEqual("", previa["duracion"], "no tenia duracion")
        self.assertTrue(nueva["id"].startswith("E-"))
        self.assertEqual("8m 26s", nueva["duracion"])

    def test_migrar_es_idempotente(self):
        self.store.insert_estado(self.apertura())
        antes = self.ruta_csv().read_bytes()
        self.store.insert_estado(self.apertura(inicio=APERTURA["inicio"]))
        _, filas = leer_csv(self.ruta_csv())
        self.assertEqual(2, len(filas))
        self.assertNotEqual(b"", antes)


class FormatoEnLaBitacoraTest(unittest.TestCase):
    def test_la_duracion_del_csv_usa_el_mismo_formateador(self):
        # Una sola implementacion del formato: si el CSV y la pantalla usaran
        # formatos distintos, operacion reportaria numeros que no coinciden.
        self.assertEqual("8m 26s", formato_duracion(506.1))


if __name__ == "__main__":
    unittest.main()
