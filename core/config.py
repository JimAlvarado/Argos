"""Constantes de configuracion y lectura/escritura de config.json.

Extraido de detector_empresarial.py sin modificar la logica.
"""
from __future__ import annotations

import json

from core import profiles

from core.paths import CONFIG_PATH, DEFAULT_MODEL

RTSP_TEMPLATES = {
    "Hikvision": "/Streaming/Channels/101",
    "Dahua": "/cam/realmonitor?channel=1&subtype=0",
    "Provision ISR": "/profile1",
    "Axis": "/axis-media/media.amp",
    "ZKTeco": "/user=admin&password=&channel=1&stream=0.sdp",
    "Genérica / otra": "/profile1",
}
RTSP_ROUTE_CANDIDATES = {
    # Provision publica normalmente el flujo principal y los subflujos en estos
    # perfiles. Probarlos automáticamente evita pedir rutas RTSP al operador.
    "Provision ISR": ["/profile1", "/profile2", "/profile3"],
}
SUPPORTED_CAMERA_BRANDS = ["Axis", "Provision ISR", "Hikvision"]
SUPPORTED_MODEL_TASKS = {"detect", "segment", "pose", "obb", "classify"}
TRACKABLE_MODEL_TASKS = {"detect", "segment", "pose", "obb"}
DANGER_SOUND_PATTERNS = {
    "Doble pitido": [
        (1350, 220, 110),
        (1350, 220, 950),
    ],
    "Triple urgente": [
        (1650, 150, 90),
        (1650, 150, 90),
        (1650, 150, 800),
    ],
    "Sirena alternada": [
        (850, 280, 35),
        (1450, 280, 35),
        (850, 280, 500),
    ],
    "Pulso rápido": [
        (1900, 100, 80),
        (1900, 100, 80),
        (1900, 100, 80),
        (1900, 100, 650),
    ],
}
DANGER_SOUND_OPTIONS = [*DANGER_SOUND_PATTERNS, "MP3 personalizado"]

DEFAULT_CONFIG = {
    "config_version": 10,
    "source_type": "Cámara local",
    "camera_index": "0",
    "brand": "Hikvision",
    "ip": "",
    "port": "554",
    "username": "",
    "route": "/Streaming/Channels/101",
    "video_file": "",
    "model_path": str(DEFAULT_MODEL),
    "model_task": "detect",
    "confidence": 0.25,
    "iou": 0.60,
    "image_size": 960,
    "max_detections": 300,
    "agnostic_nms": True,
    "class_confidence_overrides": {"surfboard": 0.65},
    "target_fps": 30,
    "event_interval": 3.0,
    "save_evidence": True,
    # Evita guardar la misma escena una y otra vez.
    "evidence_dedup": True,
    "evidence_refresh_seconds": 300.0,
    # Aplica el mejor perfil de resolucion, confianza, NMS y FPS al arrancar.
    "auto_profile": True,
    # Ultimo equipo detectado ("cpu" o "gpu"); lo escribe el cargador.
    "last_device": "cpu",
    # None significa todas las clases; una lista limita la inferencia por nombre.
    "enabled_class_names": None,
    "line_enabled": True,
    "line_defined": True,
    "line_points": [[0.10, 0.50], [0.90, 0.50]],
    "zone_enabled": False,
    "high_danger_zone": False,
    "danger_sound_mode": "Doble pitido",
    "danger_mp3_path": "",
    "zone_points": [[0.20, 0.20], [0.80, 0.20], [0.80, 0.80], [0.20, 0.80]],
    "zone_alert_cooldown": 4.0,
}


def load_config() -> dict:
    config = DEFAULT_CONFIG.copy()
    saved = {}
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            config.update(saved)
        except (OSError, ValueError):
            pass
    # Migración única: mejora la sensibilidad para cámaras Full HD sin volver a
    # modificar los ajustes que el operador elija en versiones posteriores.
    if int(saved.get("config_version", 1)) < 3:
        config["confidence"] = min(float(config["confidence"]), 0.30)
        config["image_size"] = max(int(config["image_size"]), 960)
        config["iou"] = 0.60
        config["config_version"] = 3
    if int(saved.get("config_version", 1)) < 4:
        # Perfil de tiempo real: más muestras para objetos rápidos sin volver
        # a cambiar los valores que el operador elija posteriormente.
        config["target_fps"] = max(int(config.get("target_fps", 12)), 30)
        config["confidence"] = min(float(config.get("confidence", 0.30)), 0.30)
        config["config_version"] = 4
    if int(saved.get("config_version", 1)) < 5:
        config["config_version"] = 5
    if int(saved.get("config_version", 1)) < 6:
        config["high_danger_zone"] = False
        config["config_version"] = 6
    if int(saved.get("config_version", 1)) < 7:
        config["line_defined"] = True
        config["config_version"] = 7
    if int(saved.get("config_version", 1)) < 8:
        config["danger_sound_mode"] = "Doble pitido"
        config["danger_mp3_path"] = ""
        config["config_version"] = 8
    if int(saved.get("config_version", 1)) < 9:
        config["evidence_dedup"] = True
        config["evidence_refresh_seconds"] = 300.0
        config["config_version"] = 9
    if int(saved.get("config_version", 1)) < 10:
        config["auto_profile"] = True
        config["last_device"] = config.get("last_device", "cpu")
        config["config_version"] = 10
    # El perfil se aplica antes de construir la ventana, asi el operador ve
    # desde el primer momento los valores con los que va a trabajar.
    if config.get("auto_profile", True):
        profiles.apply_profile(config, gpu=config.get("last_device") == "gpu")
    if config.get("danger_sound_mode") not in DANGER_SOUND_OPTIONS:
        config["danger_sound_mode"] = "Doble pitido"
    # Las contraseñas no se guardan en disco.
    config.pop("password", None)
    return config


def save_config(config: dict) -> None:
    safe_config = {k: v for k, v in config.items() if k != "password"}
    temp_path = CONFIG_PATH.with_suffix(".tmp")
    temp_path.write_text(
        json.dumps(safe_config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    temp_path.replace(CONFIG_PATH)

