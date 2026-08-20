"""Perfil operativo recomendado segun el hardware disponible.

Los valores salen de las mediciones registradas en
docs/historico/ANALISIS_VELOCIDAD.md sobre este mismo equipo, no de
recomendaciones genericas.

Las tres decisiones y su razon:

- **image_size** es la palanca principal: el costo escala con el numero de
  pixeles, asi que 960 cuesta 2.25 veces mas que 640. En CPU se midieron entre
  6.4 y 6.9 inferencias por segundo; bajar a 640 aproximadamente lo duplica.
- **confidence** decide que se pierde. Se midio 23 de 53 muestras al 40% y 35 de
  53 al 30%, asi que 0.30 es el punto util en CPU. Con GPU sobra margen y 0.25
  recupera objetos pequenos.
- **iou** es el umbral de supresion de cajas solapadas. Con 0.60 sobreviven
  duplicados sobre la misma persona (el sintoma de "3 cajas para 2 personas").
  Bajarlo suprime con mas fuerza.

Medicion del perfil GPU (19-ago-2026, laptop G15 con RTX 3050 Laptop de 4 GB,
modelo lingotes_v2_20260815.pt sobre el recorte de 280x510 de la lingotera):
CPU en FP32 56.9 ms por cuadro (17.6 inferencias/s) contra GPU en FP16
17.8 ms (56.3 inferencias/s); 3.2 veces mas rapido, con las mismas
detecciones y 33 MB de VRAM. Por eso el perfil GPU sube a 960 px y 30 FPS:
el margen alcanza de sobra y la memoria no es la restriccion.
"""
from __future__ import annotations

PERFIL_CPU = {
    "image_size": 640,
    "confidence": 0.30,
    "iou": 0.45,
    "target_fps": 15,
}

PERFIL_GPU = {
    "image_size": 960,
    "confidence": 0.25,
    "iou": 0.50,
    "target_fps": 30,
}

CLAVES = tuple(PERFIL_CPU)


def recommended_profile(gpu: bool = False) -> dict:
    """Devuelve una copia del perfil recomendado para el equipo."""
    return dict(PERFIL_GPU if gpu else PERFIL_CPU)


def apply_profile(config: dict, gpu: bool = False) -> dict:
    """Aplica el perfil recomendado sobre la configuracion recibida.

    Solo toca las cuatro claves del perfil; el resto de los ajustes del operador
    se conserva intacto.
    """
    config.update(recommended_profile(gpu))
    return config


def describe(gpu: bool = False) -> str:
    """Texto corto para mostrar en la interfaz o en el registro."""
    perfil = recommended_profile(gpu)
    equipo = "GPU" if gpu else "CPU"
    return (
        f"Perfil {equipo}: {perfil['image_size']} px, "
        f"confianza {perfil['confidence']:.0%}, "
        f"NMS {perfil['iou']:.2f}, {perfil['target_fps']} FPS"
    )
