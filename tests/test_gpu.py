"""Pruebas del uso de GPU NVIDIA (19-ago-2026, laptop G15 con RTX 3050).

Regresiones que cubren esta tanda, todas reales:

1. El modulo de objetos inferia sin `device` ni `quantize`: Ultralytics
   autodetecta y por eso "funcionaba", pero no habia forma de comprobar con
   que equipo se conto ni de forzar CPU si la GPU daba problemas.
2. `half` quedo deprecado en Ultralytics 8.4 y lo sustituye `quantize`. Se
   estuvo a punto de escribir `half=True` copiando documentacion vieja.
3. FP16 nunca debe pedirse en CPU: ahi no acelera y el argumento sobra.
4. `last_device` solo lo escribia el detector de personas, asi que abrir solo
   el modulo de objetos en un equipo con GPU seguia aplicando el perfil de CPU.
5. Preguntar por el equipo no puede tumbar un modulo ni forzar la carga de
   torch, que es justamente lo que la carga perezosa evita.

Las pruebas no exigen que haya GPU: simulan las dos maquinas (la laptop con
CUDA y la PC de planta sin ella) para que la suite valga en ambas.
"""
import queue
import tempfile
import types
import unittest
from pathlib import Path

import detector_objetos
from core import runtime

RAIZ = Path(__file__).resolve().parents[1]


def _contiene(archivo: str, texto: str) -> bool:
    """Si el archivo contiene el texto.

    Devuelve un booleano y no la fuente completa a proposito: assertIn sobre
    un modulo de mil lineas vuelca el archivo entero en el fallo y esconde
    cual fue el problema.
    """
    return texto in (RAIZ / archivo).read_text(encoding="utf-8")


def _torch_falso(con_gpu: bool, nombre: str = "NVIDIA GeForce RTX 3050 Laptop GPU"):
    """Imita lo justo de torch: CUDA disponible o no, nombre y cudnn."""
    cuda = types.SimpleNamespace(
        is_available=lambda: con_gpu,
        get_device_name=lambda indice: nombre,
    )
    backends = types.SimpleNamespace(
        cudnn=types.SimpleNamespace(benchmark=False)
    )
    return types.SimpleNamespace(cuda=cuda, backends=backends)


class EleccionDeEquipoTest(unittest.TestCase):
    """core/runtime.py es el unico lugar que decide GPU o CPU."""

    def setUp(self):
        self._torch_original = runtime.torch
        self.addCleanup(setattr, runtime, "torch", self._torch_original)

    def test_sin_torch_cargado_responde_cpu(self):
        # La carga perezosa es deliberada: preguntar por el equipo no debe
        # costar los segundos que tarda importar torch.
        runtime.torch = None
        self.assertFalse(runtime.hay_gpu())
        self.assertEqual(runtime.dispositivo_inferencia(), "cpu")
        self.assertIsNone(runtime.cuantizacion())
        self.assertEqual(runtime.nombre_equipo(), "CPU")
        self.assertFalse(runtime.preparar_equipo())

    def test_con_gpu_elige_la_primera_tarjeta_y_fp16(self):
        runtime.torch = _torch_falso(con_gpu=True)
        self.assertTrue(runtime.hay_gpu())
        self.assertEqual(runtime.dispositivo_inferencia(), 0)
        self.assertEqual(runtime.cuantizacion(), 16)
        self.assertIn("RTX 3050", runtime.nombre_equipo())

    def test_sin_gpu_nunca_pide_media_precision(self):
        # FP16 en CPU no acelera; pedirlo solo agrega un argumento inutil.
        runtime.torch = _torch_falso(con_gpu=False)
        self.assertEqual(runtime.dispositivo_inferencia(), "cpu")
        self.assertIsNone(runtime.cuantizacion())
        self.assertEqual(runtime.nombre_equipo(), "CPU")

    def test_preparar_equipo_activa_cudnn_solo_con_gpu(self):
        # cudnn.benchmark conviene porque el recorte tiene tamano constante.
        runtime.torch = _torch_falso(con_gpu=True)
        self.assertTrue(runtime.preparar_equipo())
        self.assertTrue(runtime.torch.backends.cudnn.benchmark)

        runtime.torch = _torch_falso(con_gpu=False)
        self.assertFalse(runtime.preparar_equipo())
        self.assertFalse(runtime.torch.backends.cudnn.benchmark)

    def test_un_driver_roto_responde_cpu_en_vez_de_reventar(self):
        # Quedarse sin conteo por una consulta de hardware nunca es aceptable.
        def revienta():
            raise RuntimeError("driver CUDA no responde")

        runtime.torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=revienta),
            backends=types.SimpleNamespace(
                cudnn=types.SimpleNamespace(benchmark=False)
            ),
        )
        self.assertFalse(runtime.hay_gpu())
        self.assertEqual(runtime.dispositivo_inferencia(), "cpu")
        self.assertEqual(runtime.nombre_equipo(), "CPU")


class ModeloFalso:
    """Registra los argumentos con los que se le pide inferir."""

    def __init__(self):
        self.llamadas = []

    def predict(self, imagen, **argumentos):
        self.llamadas.append(argumentos)
        return []


class InferenciaDeObjetosTest(unittest.TestCase):
    """El modulo de objetos declara el equipo en cada inferencia."""

    def _worker(self):
        return detector_objetos.ContadorWorker(
            fuente="prueba.mp4",
            region={"x": 0, "y": 0, "w": 64, "h": 64},
            linea_relativa=0.53,
            guardar_evidencia=False,
            salida=queue.Queue(),
            store=None,
            evidencias=None,
            nombre_fuente="prueba",
        )

    def test_arranca_en_cpu_antes_de_cargar_el_modelo(self):
        # torch todavia no esta importado: el unico valor honesto es CPU.
        worker = self._worker()
        self.assertEqual(worker.dispositivo, "cpu")
        self.assertIsNone(worker.cuantizacion)
        self.assertEqual(worker.equipo, "CPU")

    def test_la_inferencia_declara_equipo_y_precision(self):
        # Sin esto la eleccion queda en manos de Ultralytics y es invisible.
        worker = self._worker()
        worker.dispositivo = 0
        worker.cuantizacion = 16
        modelo = ModeloFalso()
        worker._detectar_con_modelo(modelo, object())

        self.assertEqual(len(modelo.llamadas), 1)
        argumentos = modelo.llamadas[0]
        self.assertEqual(argumentos["device"], 0)
        self.assertEqual(argumentos["quantize"], 16)

    def test_en_cpu_pide_fp32(self):
        worker = self._worker()
        modelo = ModeloFalso()
        worker._detectar_con_modelo(modelo, object())

        argumentos = modelo.llamadas[0]
        self.assertEqual(argumentos["device"], "cpu")
        self.assertIsNone(argumentos["quantize"])

    def test_no_usa_el_argumento_deprecado_half(self):
        # Ultralytics 8.4 reemplazo `half` por `quantize`; el modulo de
        # personas ya usaba `quantize` y los dos deben hablar igual.
        self.assertFalse(_contiene("detector_objetos.py", "half="),
                         "usa quantize, no el argumento deprecado half")
        self.assertTrue(
            _contiene("detector_objetos.py", "quantize=self.cuantizacion"),
            "la inferencia debe declarar la precision con quantize")

    def test_quantize_sigue_siendo_un_argumento_valido_de_ultralytics(self):
        # Costura con la libreria: si una version futura renombra el
        # argumento, esta prueba avisa antes que la planta.
        from ultralytics.cfg import DEFAULT_CFG_DICT

        self.assertIn("quantize", DEFAULT_CFG_DICT)


class MemoriaDelEquipoTest(unittest.TestCase):
    """`last_device` recuerda el equipo entre arranques."""

    def test_el_modulo_de_objetos_tambien_anota_el_equipo(self):
        # Antes solo lo escribia el detector de personas: abrir unicamente
        # objetos en la laptop seguia aplicando el perfil de CPU.
        self.assertTrue(_contiene("detector_objetos.py", "_recordar_equipo"))
        self.assertTrue(_contiene("detector_objetos.py", '"last_device"'),
                        "el modulo de objetos debe anotar el equipo usado")

    def test_el_perfil_de_gpu_se_aplica_al_arrancar_si_se_recordo_gpu(self):
        from core import config as core_config
        from core import profiles

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as carpeta:
            original = core_config.CONFIG_PATH
            core_config.CONFIG_PATH = Path(carpeta) / "config.json"
            try:
                core_config.save_config(
                    {"last_device": "gpu", "auto_profile": True,
                     "config_version": 10}
                )
                config = core_config.load_config()
            finally:
                core_config.CONFIG_PATH = original

        esperado = profiles.recommended_profile(gpu=True)
        for clave, valor in esperado.items():
            self.assertEqual(config[clave], valor, f"perfil GPU en {clave}")


class UnicaFuenteDeEquipoTest(unittest.TestCase):
    """Ningun detector vuelve a consultar CUDA por su cuenta."""

    def test_los_detectores_preguntan_a_core_runtime(self):
        for archivo in ("detector_empresarial.py", "detector_objetos.py",
                        "ui/models.py"):
            self.assertFalse(
                _contiene(archivo, "torch.cuda.is_available()"),
                f"{archivo} duplica la decision de equipo; usa core.runtime",
            )


if __name__ == "__main__":
    unittest.main()
