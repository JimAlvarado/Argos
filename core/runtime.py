"""Carga perezosa de PyTorch y Ultralytics, y eleccion del equipo.

Son las importaciones mas costosas del proyecto. Se cargan bajo demanda para
que la ventana aparezca de inmediato. Accede siempre como runtime.torch y
runtime.YOLO: son variables de modulo que cambian al cargarse.

Aqui vive tambien la unica decision de equipo (GPU o CPU). Antes cada modulo
consultaba `torch.cuda.is_available()` por su cuenta y el modulo de objetos
ni siquiera lo hacia: la inferencia quedaba a la autodeteccion de Ultralytics,
que funciona pero deja invisible con que se conto. Con estas funciones los dos
detectores preguntan lo mismo, en un solo lugar, y pueden reportarlo.
"""
from __future__ import annotations

import threading

# PyTorch y Ultralytics son las importaciones más costosas. Se cargan desde el
# hilo de modelos después de mostrar la ventana para que el módulo aparezca de
# inmediato al abrirlo desde el Centro de Control.
torch = None
YOLO = None
_INFERENCE_RUNTIME_LOCK = threading.Lock()


def load_inference_runtime():
    global torch, YOLO
    if torch is not None and YOLO is not None:
        return
    with _INFERENCE_RUNTIME_LOCK:
        if torch is None:
            import torch as torch_module

            torch = torch_module
        if YOLO is None:
            from ultralytics import YOLO as yolo_class

            YOLO = yolo_class


def hay_gpu() -> bool:
    """Si PyTorch ya esta cargado y ve una GPU CUDA utilizable.

    No fuerza la carga del runtime: preguntar por el equipo jamas debe costar
    los segundos que tarda importar torch. Si todavia no esta cargado, la
    respuesta es CPU. Cualquier fallo del driver tambien responde CPU: quedarse
    sin conteo por una consulta de hardware nunca es aceptable.
    """
    if torch is None:
        return False
    try:
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def dispositivo_inferencia():
    """Valor de `device` para Ultralytics: 0 (primera GPU) o "cpu"."""
    return 0 if hay_gpu() else "cpu"


def cuantizacion():
    """Valor de `quantize` para Ultralytics: 16 (FP16) con GPU, None sin ella.

    Es `quantize` y no `half`: desde Ultralytics 8.4 el argumento `half` quedo
    deprecado y sustituido por `quantize`. En CPU se deja None (FP32) porque
    la media precision ahi no acelera.
    """
    return 16 if hay_gpu() else None


def nombre_equipo() -> str:
    """Nombre del equipo para la interfaz y el registro."""
    if not hay_gpu():
        return "CPU"
    try:
        return str(torch.cuda.get_device_name(0))
    except Exception:
        return "GPU"


def preparar_equipo() -> bool:
    """Deja el equipo listo para inferir y devuelve si quedo GPU activa.

    `cudnn.benchmark` prueba algoritmos de convolucion en las primeras
    inferencias y se queda con el mas rapido. Solo conviene porque el recorte
    que se infiere tiene siempre el mismo tamano; con entradas variables el
    costo de la busqueda se repetiria en cada cambio.
    """
    if not hay_gpu():
        return False
    try:
        torch.backends.cudnn.benchmark = True
    except Exception:
        pass
    return True
